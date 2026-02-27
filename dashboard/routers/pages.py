from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Any, TypedDict, cast
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile
from starlette.responses import Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import get_async_session
from shared.models import Nano, NanoPermission, ApiCredential, PendingApproval, RunLog
from shared.config import ADMIN_API_KEY, SENSITIVE_ENDPOINTS, ALL_PERMISSIONS


class ApprovalData(TypedDict):
    """Approval summary passed to templates and live JSON."""
    id: str
    status: str
    explanation: str | None
    reasoning: str | None


class ApprovalLiveData(TypedDict):
    """Full approval data for the live polling endpoint."""
    id: str
    endpoint: str
    status: str
    request_body: str | None
    explanation: str | None
    reasoning: str | None
    created_at: str | None
    resolved_at: str | None


class RunLiveResponse(TypedDict, total=False):
    """Shape of the JSON returned by /runs/{id}/live."""
    status: str
    log_content: str
    exit_code: int | None
    finished_at: str | None
    pipeline: dict[str, Any]
    api_calls: list[dict[str, Any]]
    approvals: list[ApprovalLiveData]

GOOGLE_OAUTH2_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]

router = APIRouter()

template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=template_dir)


def _pretty_json(value: str) -> str:
    """Jinja2 filter: parse a JSON string and re-format it with indentation."""
    try:
        return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return value


templates.env.filters["tojson_pretty"] = _pretty_json
templates.env.globals["SENSITIVE_ENDPOINTS"] = SENSITIVE_ENDPOINTS
templates.env.globals["ALL_PERMISSIONS"] = ALL_PERMISSIONS

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
NANO_LOG_DIR = "/var/log/nanos"


# --- Lock screen ---

@router.get("/unlock")
async def unlock_page(request: Request) -> Response:
    """Show the unlock page (no lock check)."""
    # Check if this is initial setup (no credentials yet)
    is_setup = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{GATEWAY_URL}/api/admin/lock-status")
            if resp.status_code == 200:
                is_setup = not resp.json().get("has_credentials", True)
    except Exception:
        pass
    return templates.TemplateResponse("unlock.html", {"request": request, "is_setup": is_setup})


@router.post("/unlock")
async def unlock_submit(request: Request) -> JSONResponse:
    """Forward master key to gateway to unlock the system."""
    body = await request.json()
    master_key = body.get("master_key", "")
    if not master_key:
        return JSONResponse({"success": False, "error": "Master key is required"})

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{GATEWAY_URL}/api/admin/unlock",
                json={"master_key": master_key},
            )
            if resp.status_code == 200:
                return JSONResponse({"success": True})
            return JSONResponse({"success": False, "error": resp.json().get("detail", "Unlock failed")})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/reset")
async def reset_master_key() -> JSONResponse:
    """Wipe all credentials and reset the master key via gateway."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{GATEWAY_URL}/api/admin/reset")
            if resp.status_code == 200:
                return JSONResponse({"success": True})
            return JSONResponse({"success": False, "error": "Reset failed"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


async def check_gateway_lock() -> bool:
    """Return True if the gateway is locked (master key not set)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{GATEWAY_URL}/api/admin/lock-status")
            if resp.status_code == 200:
                return bool(resp.json().get("locked", True))
    except Exception:
        pass
    return False  # If we can't reach the gateway, don't block the dashboard


def _read_log_file(run: RunLog) -> str:
    """Read log content for a run, checking log_file_path and the predictable path.

    When a run has failed (non-zero exit code or error status), stderr from the
    database is always appended so tracebacks are visible in the dashboard.
    """
    log_content = ""

    # Try the stored log_file_path first
    paths_to_try = []
    if run.log_file_path:
        paths_to_try.append(run.log_file_path)

    # Also try the predictable path: /var/log/nanos/{nano_name}/{run_id}.log
    if run.nano:
        predictable = os.path.join(NANO_LOG_DIR, run.nano.name, f"{run.id}.log")
        paths_to_try.append(predictable)
        # Also try the directory (runner sets NANO_LOG_DIR to a directory)
        predictable_dir = os.path.join(NANO_LOG_DIR, run.nano.name, str(run.id))
        paths_to_try.append(predictable_dir)

    for log_path in paths_to_try:
        try:
            if os.path.isfile(log_path):
                with open(log_path) as f:
                    log_content = f.read()
                if log_content:
                    break
            elif os.path.isdir(log_path):
                for fname in sorted(os.listdir(log_path)):
                    if fname.endswith(".log"):
                        with open(os.path.join(log_path, fname)) as f:
                            log_content = f.read()
                        if log_content:
                            break
                if log_content:
                    break
        except OSError:
            continue

    # If no file content found, fall back to stdout from DB
    if not log_content and run.stdout:
        log_content = run.stdout

    # Always append stderr when the run failed so tracebacks are visible
    has_error = run.exit_code not in (None, 0) or run.status == "error"
    if has_error and run.stderr:
        separator = "\n--- stderr ---\n" if log_content else ""
        log_content = (log_content or "") + separator + run.stderr

    return log_content


def _read_pipeline_file(run: RunLog) -> dict[str, Any] | None:
    """Read .pipeline.json for a run, return parsed dict or None."""
    paths_to_try = []
    if run.log_file_path:
        paths_to_try.append(os.path.join(run.log_file_path, ".pipeline.json"))
    if run.nano:
        predictable_dir = os.path.join(NANO_LOG_DIR, run.nano.name, str(run.id))
        paths_to_try.append(os.path.join(predictable_dir, ".pipeline.json"))

    for p in paths_to_try:
        try:
            if os.path.isfile(p):
                with open(p) as f:
                    return cast(dict[str, Any], json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _read_api_calls_file(run: RunLog) -> list[dict[str, Any]] | None:
    """Read .api_calls.jsonl for a run, return list of dicts or None.

    The SDK writes ``{NANO_LOG_DIR}.api_calls.jsonl`` which resolves to a
    sibling file next to the run directory, e.g.
    ``/var/log/nanos/{nano}/{run_id}.api_calls.jsonl``.
    """
    paths_to_try = []
    if run.log_file_path:
        paths_to_try.append(run.log_file_path + ".api_calls.jsonl")
    if run.nano:
        predictable = os.path.join(NANO_LOG_DIR, run.nano.name, f"{run.id}.api_calls.jsonl")
        paths_to_try.append(predictable)

    for p in paths_to_try:
        try:
            if os.path.isfile(p):
                calls = []
                with open(p) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            calls.append(json.loads(line))
                return calls if calls else None
        except (OSError, json.JSONDecodeError):
            continue
    return None


@router.get("/")
async def index(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Overview dashboard."""
    # Count nanos
    result = await session.execute(select(func.count(Nano.id)))
    nano_count = result.scalar()

    result = await session.execute(
        select(func.count(Nano.id)).where(Nano.is_active.is_(True))
    )
    active_count = result.scalar()

    # Count credentials
    result = await session.execute(select(func.count(ApiCredential.id)))
    cred_count = result.scalar()

    # Count pending approvals
    result = await session.execute(
        select(func.count(PendingApproval.id)).where(PendingApproval.status == "pending")
    )
    pending_count = result.scalar()

    # Recent runs
    result = await session.execute(
        select(RunLog)
        .options(selectinload(RunLog.nano))
        .order_by(RunLog.started_at.desc())
        .limit(10)
    )
    recent_runs = result.scalars().all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "nano_count": nano_count,
        "active_count": active_count,
        "cred_count": cred_count,
        "pending_count": pending_count,
        "recent_runs": recent_runs,
    })


API_CATALOG: list[dict[str, Any]] = [
    {
        "name": "openai", "label": "OpenAI",
        "test_name": "openai",
        "endpoints": ["openai.chat", "openai.embeddings"],
        "fields": [{"key": "api_key", "label": "API Key", "placeholder": "sk-..."}],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://platform.openai.com/api-keys' target='_blank'>platform.openai.com/api-keys</a></li>"
            "<li>Click <strong>Create new secret key</strong></li>"
            "<li>Copy the key (starts with <code>sk-</code>) and paste it here</li>"
            "</ol>"
        ),
    },
    {
        "name": "google-calendar", "label": "Google Calendar",
        "test_name": "calendar",
        "oauth": True,
        "endpoints": [
            "calendar.events.list", "calendar.events.create",
            "calendar.events.update", "calendar.events.delete",
        ],
        "fields": [
            {"key": "client_id", "label": "OAuth2 Client ID", "placeholder": "...apps.googleusercontent.com"},
            {"key": "client_secret", "label": "OAuth2 Client Secret", "placeholder": "GOCSPX-..."},
        ],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://console.cloud.google.com/apis/credentials' target='_blank'>"
            "Google Cloud Console &gt; Credentials</a></li>"
            "<li>Create an <strong>OAuth 2.0 Client ID</strong> (type: Web application)</li>"
            "<li>Add <code>http://localhost</code> as an authorized redirect URI</li>"
            "<li>Copy the <strong>Client ID</strong> and <strong>Client Secret</strong></li>"
            "<li>Click <strong>Authorize with Google</strong> &mdash; a new tab opens for sign-in</li>"
            "<li>After consenting, you'll be redirected to a page that won't load (this is normal). "
            "Copy the URL from the address bar and paste it back here</li>"
            "</ol>"
            "<p><strong>Tip:</strong> The OAuth flow requests both Calendar and Gmail scopes at once, "
            "so one authorization covers both.</p>"
        ),
    },
    {
        "name": "gmail", "label": "Gmail",
        "test_name": "gmail",
        "oauth": True,
        "endpoints": [
            "gmail.messages.list", "gmail.messages.get", "gmail.threads.get",
            "gmail.messages.send", "gmail.messages.reply",
        ],
        "fields": [
            {"key": "client_id", "label": "OAuth2 Client ID", "placeholder": "...apps.googleusercontent.com"},
            {"key": "client_secret", "label": "OAuth2 Client Secret", "placeholder": "GOCSPX-..."},
        ],
        "help": (
            "<p>Gmail uses the same OAuth2 credentials as Google Calendar. "
            "If you already authorized Calendar, Gmail is configured automatically.</p>"
            "<ol>"
            "<li>Make sure the <strong>Gmail API</strong> is enabled in your "
            "<a href='https://console.cloud.google.com/apis/library' target='_blank'>Google Cloud project</a></li>"
            "<li>If not already configured, use the same Client ID and Secret as Calendar, "
            "click <strong>Authorize with Google</strong>, and paste back the redirect URL</li>"
            "</ol>"
        ),
    },
    {
        "name": "slack", "label": "Slack",
        "test_name": "slack",
        "endpoints": ["slack.send_message"],
        "fields": [{"key": "webhook_url", "label": "Webhook URL", "placeholder": "https://hooks.slack.com/services/..."}],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://api.slack.com/apps' target='_blank'>api.slack.com/apps</a> and select your app</li>"
            "<li>Navigate to <strong>Incoming Webhooks</strong> and toggle it on</li>"
            "<li>Click <strong>Add New Webhook to Workspace</strong> and choose a channel</li>"
            "<li>Copy the Webhook URL (starts with <code>https://hooks.slack.com/services/</code>)</li>"
            "</ol>"
        ),
    },
    {
        "name": "hubspot", "label": "HubSpot CRM",
        "test_name": "hubspot",
        "endpoints": [
            "hubspot.contacts.list", "hubspot.contacts.get",
            "hubspot.contacts.create", "hubspot.contacts.update", "hubspot.contacts.delete",
            "hubspot.contacts.search",
            "hubspot.deals.list", "hubspot.deals.get",
            "hubspot.deals.create", "hubspot.deals.update", "hubspot.deals.delete",
            "hubspot.deals.search",
            "hubspot.tasks.list", "hubspot.tasks.get",
            "hubspot.tasks.create", "hubspot.tasks.update", "hubspot.tasks.delete",
            "hubspot.tasks.search",
        ],
        "fields": [{"key": "access_token", "label": "Private App Access Token", "placeholder": "pat-na1-..."}],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://app.hubspot.com/' target='_blank'>app.hubspot.com</a> "
            "&gt; Settings &gt; Integrations &gt; Private Apps</li>"
            "<li>Click <strong>Create a private app</strong></li>"
            "<li>Under <em>Scopes</em>, add: <code>crm.objects.contacts</code>, "
            "<code>crm.objects.deals</code>, <code>crm.objects.custom</code> (read/write)</li>"
            "<li>Click <strong>Create app</strong> and copy the access token</li>"
            "</ol>"
        ),
    },
    {
        "name": "slackbot", "label": "Slack Bot (Approvals)",
        "test_name": None,
        "endpoints": [],
        "fields": [
            {"key": "bot_token", "label": "Bot Token", "placeholder": "xoxb-..."},
            {"key": "app_token", "label": "App-Level Token", "placeholder": "xapp-..."},
            {"key": "channel_id", "label": "Channel ID", "placeholder": "C07..."},
        ],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://api.slack.com/apps' target='_blank'>api.slack.com/apps</a> and select your app</li>"
            "<li><strong>Bot Token:</strong> Under <em>OAuth &amp; Permissions</em>, copy the "
            "<strong>Bot User OAuth Token</strong> (<code>xoxb-...</code>). "
            "Required scopes: <code>chat:write</code>, <code>channels:read</code></li>"
            "<li><strong>App Token:</strong> Under <em>Basic Information &gt; App-Level Tokens</em>, "
            "create a token with <code>connections:write</code> scope (<code>xapp-...</code>)</li>"
            "<li><strong>Channel ID:</strong> In Slack, right-click the channel &gt; "
            "<em>View channel details</em> &gt; copy the ID at the bottom (e.g. <code>C07...</code>)</li>"
            "</ol>"
        ),
    },
    {
        "name": "notion", "label": "Notion",
        "test_name": "notion",
        "endpoints": [
            "notion.search",
            "notion.databases.get", "notion.databases.query",
            "notion.pages.get", "notion.pages.create", "notion.pages.update", "notion.pages.delete",
            "notion.blocks.list", "notion.blocks.append", "notion.blocks.update", "notion.blocks.delete",
            "notion.comments.list", "notion.comments.create",
            "notion.users.list",
        ],
        "fields": [{"key": "api_token", "label": "Integration Token", "placeholder": "ntn_..."}],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://www.notion.so/my-integrations' target='_blank'>notion.so/my-integrations</a></li>"
            "<li>Click <strong>New integration</strong></li>"
            "<li>Give it a name and select the workspace</li>"
            "<li>Copy the <strong>Internal Integration Token</strong> (starts with <code>ntn_</code>)</li>"
            "<li>In Notion, share the pages/databases you want accessible with this integration</li>"
            "</ol>"
        ),
    },
    {
        "name": "linear", "label": "Linear",
        "test_name": "linear",
        "endpoints": [
            "linear.issues.list", "linear.issues.get",
            "linear.issues.create", "linear.issues.update", "linear.issues.delete",
            "linear.comments.list", "linear.comments.create", "linear.comments.update", "linear.comments.delete",
            "linear.projects.list", "linear.projects.get",
            "linear.teams.list", "linear.cycles.list", "linear.users.list",
        ],
        "fields": [{"key": "api_key", "label": "API Key", "placeholder": "lin_api_..."}],
        "help": (
            "<ol>"
            "<li>Go to <a href='https://linear.app/settings/api' target='_blank'>linear.app/settings/api</a></li>"
            "<li>Click <strong>Create key</strong> under Personal API keys</li>"
            "<li>Give it a label and click <strong>Create</strong></li>"
            "<li>Copy the key (starts with <code>lin_api_</code>)</li>"
            "</ol>"
        ),
    },
    {
        "name": "whatsapp", "label": "WhatsApp",
        "test_name": "whatsapp",
        "auth_type": "qr",
        "endpoints": [
            "whatsapp.chats.list", "whatsapp.messages.search",
            "whatsapp.messages.send_text", "whatsapp.messages.send_file",
            "whatsapp.groups.list", "whatsapp.media.download",
            "whatsapp.history.backfill",
        ],
        "fields": [],
        "help": (
            "<ol>"
            "<li>Click <strong>Authenticate</strong> to open the QR code scanner</li>"
            "<li>Click <strong>Start Authentication</strong> in the dialog</li>"
            "<li>Open WhatsApp on your phone &gt; <em>Linked Devices</em> &gt; <em>Link a Device</em></li>"
            "<li>Scan the QR code displayed in the browser</li>"
            "<li>Wait for confirmation &mdash; the page will reload automatically</li>"
            "</ol>"
        ),
    },
]


@router.get("/apis")
async def apis(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """API catalog page."""
    result = await session.execute(select(ApiCredential).order_by(ApiCredential.api_name))
    credentials = result.scalars().all()

    cred_map = {c.api_name: c for c in credentials}
    catalog = []
    for api in API_CATALOG:
        entry = {**api, "configured": api["name"] in cred_map}
        if entry["configured"]:
            cred = cred_map[api["name"]]
            entry["updated_at"] = cred.updated_at

        # WhatsApp: check auth status + sync status from gateway
        if api.get("auth_type") == "qr":
            entry["wa_sync_status"] = "idle"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        f"{GATEWAY_URL}/api/whatsapp/auth/status",
                        headers={"X-Admin-Key": ADMIN_API_KEY},
                    )
                    if resp.status_code == 200:
                        wa_status = resp.json()
                        entry["configured"] = wa_status.get("authenticated", False)
                    # Also check sync status
                    sync_resp = await client.get(
                        f"{GATEWAY_URL}/api/whatsapp/sync/status",
                        headers={"X-Admin-Key": ADMIN_API_KEY},
                    )
                    if sync_resp.status_code == 200:
                        entry["wa_sync_status"] = sync_resp.json().get("status", "idle")
            except Exception:
                entry["configured"] = False

        catalog.append(entry)

    return templates.TemplateResponse("apis.html", {
        "request": request,
        "apis": catalog,
    })


@router.get("/types")
async def types_list(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """List all nano types from disk."""
    from shared.nano_types import list_types
    types = list_types()

    # Count instances per type
    result = await session.execute(select(Nano).order_by(Nano.name))
    nanos = result.scalars().all()
    instance_counts: dict[str, int] = {}
    for n in nanos:
        if n.type_name:
            instance_counts[n.type_name] = instance_counts.get(n.type_name, 0) + 1

    for t in types:
        t["instance_count"] = instance_counts.get(t["type_name"], 0)

    return templates.TemplateResponse("types.html", {
        "request": request,
        "types": types,
    })


@router.get("/types/{type_name}")
async def type_detail(type_name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Single type detail page with instances and code preview."""
    from shared.nano_types import load_type
    type_info = load_type(type_name)
    if not type_info:
        return templates.TemplateResponse("type_detail.html", {
            "request": request,
            "type_info": {"type_name": type_name, "name": type_name, "description": "Not found", "permissions": []},
            "instances": [],
            "code": None,
        })

    # Get instances of this type
    result = await session.execute(
        select(Nano).where(Nano.type_name == type_name).order_by(Nano.name)
    )
    instances = result.scalars().all()

    # Try to read the source code
    code = None
    code_path = os.path.join("/nanos", type_name, "nano.py")
    try:
        with open(code_path) as f:
            code = f.read()
    except OSError:
        pass

    return templates.TemplateResponse("type_detail.html", {
        "request": request,
        "type_info": type_info,
        "instances": instances,
        "code": code,
    })


@router.post("/types/{type_name}/create-instance")
async def create_instance(type_name: str, request: Request) -> JSONResponse:
    """Create a new nano instance of the given type via the gateway admin API."""
    from shared.nano_types import load_type

    type_config = load_type(type_name)
    if not type_config:
        return JSONResponse({"error": f"Type '{type_name}' not found"}, status_code=404)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Instance name is required"}, status_code=400)

    schedule = str(form.get("schedule", "")).strip() or None

    # Build parameters from form fields (param_* keys)
    parameter_schema = type_config.get("parameter_schema") or {}
    parameters: dict[str, Any] = {}
    for key, spec in parameter_schema.items():
        raw = str(form.get(f"param_{key}", "")).strip()
        if not raw:
            # Use default from schema if available
            if isinstance(spec, dict) and "default" in spec:
                parameters[key] = spec["default"]
            continue
        # Coerce to the declared type
        ptype = spec.get("type", "string") if isinstance(spec, dict) else "string"
        try:
            if ptype == "integer":
                parameters[key] = int(raw)
            elif ptype == "float" or ptype == "number":
                parameters[key] = float(raw)
            elif ptype == "boolean":
                parameters[key] = raw.lower() in ("true", "1", "yes")
            else:
                parameters[key] = raw
        except (ValueError, TypeError):
            parameters[key] = raw

    # Merge baseline permissions with user-selected ones
    baseline_perms = set(type_config.get("permissions", []))
    selected_perms = {str(p) for p in form.getlist("permissions")}
    all_perms = sorted(baseline_perms | selected_perms, key=lambda p: ALL_PERMISSIONS.index(p) if p in ALL_PERMISSIONS else 999)

    # Call the gateway admin API to create the nano
    payload = {
        "name": name,
        "type_name": type_name,
        "description": type_config.get("description", ""),
        "schedule": schedule,
        "permissions": all_perms,
    }
    if parameters:
        payload["parameters"] = parameters

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{GATEWAY_URL}/api/admin/nanos",
                headers={"X-Admin-Key": ADMIN_API_KEY},
                json=payload,
            )
            if resp.status_code == 201:
                data = resp.json()
                return JSONResponse({"name": data["name"], "api_key": data["api_key"], "id": str(data["id"])})
            else:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text or f"Gateway returned {resp.status_code}"
                return JSONResponse({"error": detail}, status_code=resp.status_code)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/types/{type_name}/delete")
async def delete_type(type_name: str) -> RedirectResponse:
    """Delete a nano type and all its instances via the gateway admin API."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{GATEWAY_URL}/api/admin/types/{type_name}",
            params={"force": "true"},
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return RedirectResponse(url="/types", status_code=303)


@router.get("/nanos")
async def nanos_list(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """List all nanos."""
    result = await session.execute(
        select(Nano).options(selectinload(Nano.permissions)).order_by(Nano.name)
    )
    nanos = result.scalars().all()
    return templates.TemplateResponse("nanos.html", {
        "request": request,
        "nanos": nanos,
    })


@router.get("/nanos/export")
async def export_nanos() -> JSONResponse:
    """Download all nanos as a JSON file."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/admin/nanos/export",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return JSONResponse(
        resp.json(),
        headers={"Content-Disposition": "attachment; filename=nanos-export.json"},
    )


@router.post("/nanos/import")
async def import_nanos(request: Request) -> JSONResponse:
    """Import nanos from an uploaded JSON file."""
    form = await request.form()
    upload = form.get("file")
    if not upload or not isinstance(upload, UploadFile):
        return JSONResponse({"success": False, "error": "No file uploaded"})

    content = await upload.read()
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return JSONResponse({"success": False, "error": "Invalid JSON file"})

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/nanos/import",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json=payload,
        )
        if resp.status_code == 200:
            data = resp.json()
            return JSONResponse({
                "success": True,
                "imported": data.get("imported", 0),
                "skipped": data.get("skipped", []),
            })
        else:
            detail = resp.json().get("detail", "Import failed")
            return JSONResponse({"success": False, "error": detail})


@router.get("/nanos/{name}")
async def nano_detail(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Single nano detail page with logs."""
    result = await session.execute(
        select(Nano)
        .options(selectinload(Nano.permissions), selectinload(Nano.run_logs))
        .where(Nano.name == name)
    )
    nano = result.scalar_one_or_none()
    if not nano:
        return templates.TemplateResponse("nano_detail.html", {
            "request": request,
            "nano": None,
            "runs": [],
            "log_content": None,
        })

    runs_result = await session.execute(
        select(RunLog)
        .where(RunLog.nano_id == nano.id)
        .order_by(RunLog.started_at.desc())
        .limit(50)
    )
    runs = runs_result.scalars().all()

    # Try to read latest log file
    log_content = None
    if runs:
        log_content = _read_log_file(runs[0]) or None

    # Load parameter schema from type config (if available)
    parameter_schema: dict[str, Any] | None = None
    if nano.type_name:
        from shared.nano_types import load_type
        type_config = load_type(nano.type_name)
        if type_config:
            parameter_schema = type_config.get("parameter_schema")

    # Parse current parameter values
    current_params: dict[str, Any] = {}
    if nano.parameters:
        try:
            current_params = json.loads(nano.parameters)
        except (json.JSONDecodeError, TypeError):
            pass

    return templates.TemplateResponse("nano_detail.html", {
        "request": request,
        "nano": nano,
        "runs": runs,
        "log_content": log_content,
        "parameter_schema": parameter_schema,
        "current_params": current_params,
        "ALL_PERMISSIONS": ALL_PERMISSIONS,
    })


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Single run detail page with log output."""
    result = await session.execute(
        select(RunLog)
        .options(selectinload(RunLog.nano), selectinload(RunLog.approvals))
        .where(RunLog.id == run_id)
    )
    run = result.scalar_one_or_none()

    log_content = None
    pipeline_data = None
    api_calls = None
    if run:
        log_content = _read_log_file(run) or None
        pipeline_data = _read_pipeline_file(run)
        api_calls = _read_api_calls_file(run)

    state_before = None
    state_after = None
    if run:
        import json as _json
        if run.state_before:
            try:
                state_before = _json.loads(run.state_before)
            except (ValueError, TypeError):
                pass
        if run.state_after:
            try:
                state_after = _json.loads(run.state_after)
            except (ValueError, TypeError):
                pass

    approvals = run.approvals if run else []
    approvals_data: list[ApprovalData] = [
        ApprovalData(
            id=str(a.id),
            status=a.status,
            explanation=a.explanation,
            reasoning=a.reasoning,
        )
        for a in approvals
    ]

    return templates.TemplateResponse("run_detail.html", {
        "request": request,
        "run": run,
        "log_content": log_content,
        "pipeline_data": pipeline_data,
        "api_calls": api_calls,
        "approvals": approvals,
        "approvals_data": approvals_data,
        "state_before": state_before,
        "state_after": state_after,
    })


@router.get("/runs/{run_id}/live")
async def run_live(run_id: str, session: AsyncSession = Depends(get_async_session)) -> JSONResponse:
    """JSON endpoint returning current run status and log content (for polling)."""
    result = await session.execute(
        select(RunLog)
        .options(selectinload(RunLog.nano), selectinload(RunLog.approvals))
        .where(RunLog.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        return JSONResponse({"error": "not found"}, status_code=404)

    log_content = _read_log_file(run)
    pipeline_data = _read_pipeline_file(run)
    api_calls = _read_api_calls_file(run)

    approval_list: list[ApprovalLiveData] = [
        ApprovalLiveData(
            id=str(a.id),
            endpoint=a.endpoint,
            status=a.status,
            request_body=a.request_body,
            explanation=a.explanation,
            reasoning=a.reasoning,
            created_at=a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else None,
            resolved_at=a.resolved_at.strftime("%Y-%m-%d %H:%M:%S") if a.resolved_at else None,
        )
        for a in run.approvals
    ]
    resp: RunLiveResponse = {
        "status": run.status,
        "log_content": log_content,
        "exit_code": run.exit_code,
        "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
        "approvals": approval_list,
    }
    if pipeline_data:
        resp["pipeline"] = pipeline_data
    if api_calls:
        resp["api_calls"] = api_calls
    return JSONResponse(resp)


@router.get("/logs")
async def logs(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """All run logs."""
    result = await session.execute(
        select(RunLog)
        .options(selectinload(RunLog.nano))
        .order_by(RunLog.started_at.desc())
        .limit(100)
    )
    runs = result.scalars().all()
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "runs": runs,
    })


@router.get("/approvals")
async def approvals(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Pending approvals page."""
    result = await session.execute(
        select(PendingApproval)
        .options(selectinload(PendingApproval.nano))
        .order_by(
            case((PendingApproval.status == "pending", 0), else_=1),
            case(
                (PendingApproval.wait_until_date.is_(None), 0),
                (PendingApproval.wait_until_date <= func.now(), 0),
                else_=1,
            ),
            PendingApproval.created_at.desc(),
        )
        .limit(100)
    )
    approvals = result.scalars().all()
    return templates.TemplateResponse("approvals.html", {
        "request": request,
        "approvals": approvals,
        "now": datetime.utcnow(),
    })


@router.post("/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, request: Request) -> Response:
    """Approve a pending approval via gateway admin API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/approvals/{approval_id}/approve",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    # Return JSON for AJAX, redirect for form submissions
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse(resp.json())
    return RedirectResponse(url="/approvals", status_code=303)


@router.post("/approvals/{approval_id}/reject")
async def reject_action(approval_id: str, request: Request) -> Response:
    """Reject a pending approval via gateway admin API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/approvals/{approval_id}/reject",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse(resp.json())
    return RedirectResponse(url="/approvals", status_code=303)


@router.post("/approvals/reject-all")
async def reject_all_action(request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Reject all pending approvals."""
    result = await session.execute(
        select(PendingApproval).where(PendingApproval.status == "pending")
    )
    pending = result.scalars().all()
    async with httpx.AsyncClient() as client:
        for approval in pending:
            await client.post(
                f"{GATEWAY_URL}/api/admin/approvals/{approval.id}/reject",
                headers={"X-Admin-Key": ADMIN_API_KEY},
            )
    return RedirectResponse(url="/approvals", status_code=303)


@router.get("/nanos/{name}/code")
async def nano_code(name: str, session: AsyncSession = Depends(get_async_session)) -> JSONResponse:
    """Return the nano's source code as JSON."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return JSONResponse({"error": "Nano not found"}, status_code=404)

    code_path = os.path.join("/nanos", nano.script_path)
    try:
        with open(code_path) as f:
            code = f.read()
    except OSError:
        return JSONResponse({"error": "Source file not found"}, status_code=404)

    return JSONResponse({"code": code, "path": nano.script_path})


@router.post("/nanos/{name}/run")
async def run_nano(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Trigger a manual run of a nano via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    form = await request.form()
    draft_mode = form.get("draft_mode") == "true"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}/run",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"draft_mode": draft_mode} if draft_mode else None,
        )
        resp.raise_for_status()
        data = resp.json()
    return RedirectResponse(url=f"/runs/{data['run_log_id']}", status_code=303)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str) -> RedirectResponse:
    """Stop a running nano via the gateway admin API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/runs/{run_id}/stop",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.post("/nanos/{name}/parameters")
async def update_parameters(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Update a nano's parameters via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    form = await request.form()
    params_str = str(form.get("parameters", "{}")).strip()
    try:
        params = json.loads(params_str)
    except json.JSONDecodeError:
        return RedirectResponse(url=f"/nanos/{name}", status_code=303)
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"parameters": params},
        )
        resp.raise_for_status()
    return RedirectResponse(url=f"/nanos/{name}", status_code=303)


@router.post("/nanos/{name}/permissions")
async def update_permissions(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Update a nano's permissions via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    form = await request.form()
    permissions = [str(p) for p in form.getlist("permissions")]
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"permissions": permissions},
        )
        resp.raise_for_status()
    return RedirectResponse(url=f"/nanos/{name}", status_code=303)


@router.post("/nanos/{name}/toggle")
async def toggle_nano(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Enable or disable a nano via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    new_state = not nano.is_active
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"is_active": new_state},
        )
        resp.raise_for_status()
    form = await request.form()
    redirect = str(form.get("redirect", f"/nanos/{name}"))
    return RedirectResponse(url=redirect, status_code=303)


@router.post("/nanos/{name}/schedule")
async def update_schedule(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Update a nano's schedule via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    form = await request.form()
    schedule = str(form.get("schedule", "")).strip() or None
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"schedule": schedule},
        )
        resp.raise_for_status()
    return RedirectResponse(url=f"/nanos/{name}", status_code=303)


@router.post("/nanos/{name}/rename")
async def rename_nano(name: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Rename a nano via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    form = await request.form()
    new_name = str(form.get("name", "")).strip()
    if not new_name or new_name == name:
        return RedirectResponse(url=f"/nanos/{name}", status_code=303)
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"name": new_name},
        )
        if resp.status_code == 409:
            # Name conflict — redirect back without changing
            return RedirectResponse(url=f"/nanos/{name}", status_code=303)
        resp.raise_for_status()
    return RedirectResponse(url=f"/nanos/{new_name}", status_code=303)


@router.post("/nanos/{name}/delete")
async def delete_nano(name: str, session: AsyncSession = Depends(get_async_session)) -> RedirectResponse:
    """Delete a nano instance via the gateway admin API."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return RedirectResponse(url="/nanos", status_code=303)
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{GATEWAY_URL}/api/admin/nanos/{nano.id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return RedirectResponse(url="/nanos", status_code=303)


@router.post("/apis/{api_name}/configure")
async def configure_api(api_name: str, request: Request) -> JSONResponse:
    """Save API credentials via the gateway admin API, then run a test."""
    form = await request.form()

    api_def = next((a for a in API_CATALOG if a["name"] == api_name), None)
    if not api_def:
        return JSONResponse({"success": False, "error": "Unknown API"})

    cred_data: dict[str, str] = {}
    for field in api_def["fields"]:
        value = str(form.get(field["key"], "")).strip()
        if value:
            cred_data[field["key"]] = value

    if not cred_data:
        return JSONResponse({"success": False, "error": "No credentials provided"})

    if api_name in ("google-calendar", "gmail"):
        cred_data["type"] = "oauth2"

    # Save credentials
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/credentials",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"api_name": api_name, "credentials": cred_data},
        )
        resp.raise_for_status()

    # Run test if available
    test_name = api_def.get("test_name")
    if test_name:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(f"{GATEWAY_URL}/api/test/{test_name}")
                result = resp.json()
                return JSONResponse({
                    "success": result.get("success", False),
                    "tests": result.get("tests", []),
                })
            except Exception as e:
                return JSONResponse({"success": True, "tests": [], "warning": f"Saved but test failed: {e}"})

    return JSONResponse({"success": True, "tests": []})


@router.post("/apis/{api_name}/test")
async def test_api(api_name: str) -> JSONResponse:
    """Run a connectivity test for an API."""
    api_def = next((a for a in API_CATALOG if a["name"] == api_name), None)
    test_name = api_def.get("test_name") if api_def else None
    if not test_name:
        return JSONResponse({"success": False, "error": "No test available for this API"})

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{GATEWAY_URL}/api/test/{test_name}")
            return JSONResponse(resp.json())
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)})


@router.post("/apis/{api_name}/delete")
async def delete_api(api_name: str) -> RedirectResponse:
    """Delete API credentials via the gateway admin API."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{GATEWAY_URL}/api/admin/credentials/{api_name}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return RedirectResponse(url="/apis", status_code=303)




@router.get("/apis/export")
async def export_credentials() -> JSONResponse:
    """Download all encrypted credentials as a JSON file."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/admin/credentials/export",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        resp.raise_for_status()
    return JSONResponse(
        resp.json(),
        headers={"Content-Disposition": "attachment; filename=nanos-credentials.json"},
    )


@router.post("/apis/import")
async def import_credentials(request: Request) -> JSONResponse:
    """Import credentials from an uploaded JSON file."""
    form = await request.form()
    upload = form.get("file")
    if not upload or not isinstance(upload, UploadFile):
        return JSONResponse({"success": False, "error": "No file uploaded"})

    password = str(form.get("password", "")).strip() or None

    content = await upload.read()
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return JSONResponse({"success": False, "error": "Invalid JSON file"})

    if password:
        payload["password"] = password

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/credentials/import",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json=payload,
        )
        if resp.status_code == 200:
            data = resp.json()
            return JSONResponse({"success": True, "imported": data.get("imported", 0)})
        else:
            detail = resp.json().get("detail", "Import failed")
            return JSONResponse({"success": False, "error": detail})


# --- WhatsApp QR auth proxy routes ---

@router.get("/apis/whatsapp/auth-stream")
async def whatsapp_auth_stream() -> StreamingResponse:
    """Proxy the SSE auth stream from the gateway."""
    async def _proxy():
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)) as client:
            async with client.stream(
                "GET",
                f"{GATEWAY_URL}/api/whatsapp/auth/stream",
                headers={"X-Admin-Key": ADMIN_API_KEY},
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/apis/whatsapp/auth-status")
async def whatsapp_auth_status() -> JSONResponse:
    """Proxy auth status check from the gateway."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/whatsapp/auth/status",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/apis/whatsapp/auth-logout")
async def whatsapp_auth_logout() -> Response:
    """Proxy logout to the gateway, then redirect to APIs page."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/whatsapp/auth/logout",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        if resp.status_code >= 400:
            return JSONResponse(resp.json(), status_code=resp.status_code)
    return RedirectResponse(url="/apis", status_code=303)


@router.get("/apis/whatsapp/sync-status")
async def whatsapp_sync_status() -> JSONResponse:
    """Proxy sync status check from the gateway."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/whatsapp/sync/status",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)


# --- Google OAuth2 flow ---
# Google blocks private IPs as redirect URIs, so we use http://localhost
# (which Google special-cases). The browser redirects there after consent,
# the page won't load, but the URL bar contains the code. The user pastes
# that URL back into the dashboard modal and we exchange it server-side.

@router.post("/apis/{api_name}/oauth-url")
async def oauth_url(api_name: str, request: Request) -> JSONResponse:
    """Return the Google OAuth authorization URL (JSON)."""
    form = await request.form()
    client_id = str(form.get("client_id", "")).strip()
    if not client_id:
        return JSONResponse({"error": "Client ID is required"}, status_code=400)

    auth_params = urlencode({
        "client_id": client_id,
        "redirect_uri": "http://localhost",
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH2_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    return JSONResponse({
        "url": f"https://accounts.google.com/o/oauth2/v2/auth?{auth_params}",
    })


@router.post("/apis/{api_name}/oauth-exchange")
async def oauth_exchange(api_name: str, request: Request) -> JSONResponse:
    """Exchange an OAuth authorization code for tokens and save credentials."""
    form = await request.form()
    client_id = str(form.get("client_id", "")).strip()
    client_secret = str(form.get("client_secret", "")).strip()
    code_input = str(form.get("code", "")).strip()

    if not all([client_id, client_secret, code_input]):
        return JSONResponse({"success": False, "error": "All fields are required"})

    # Extract code from full redirect URL if user pasted it
    if code_input.startswith("http"):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(code_input)
        qs = parse_qs(parsed.query)
        if "code" not in qs:
            return JSONResponse({"success": False, "error": "No 'code' parameter found in the URL"})
        code_input = qs["code"][0]

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code_input,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "http://localhost",
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()

    if "error" in token_data:
        detail = token_data.get("error_description", token_data["error"])
        return JSONResponse({"success": False, "error": detail})

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return JSONResponse({"success": False, "error": "No refresh token received. Try again with prompt=consent."})

    # Save credentials
    cred_data = {
        "type": "oauth2",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/credentials",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={"api_name": api_name, "credentials": cred_data},
        )
        resp.raise_for_status()

    # Also save for the sibling API since scopes cover both
    sibling = "gmail" if api_name == "google-calendar" else "google-calendar" if api_name == "gmail" else None
    if sibling:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{GATEWAY_URL}/api/admin/credentials",
                headers={"X-Admin-Key": ADMIN_API_KEY},
                json={"api_name": sibling, "credentials": cred_data},
            )

    # Run test
    api_def = next((a for a in API_CATALOG if a["name"] == api_name), None)
    test_name = api_def.get("test_name") if api_def else None
    tests = []
    if test_name:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{GATEWAY_URL}/api/test/{test_name}")
                result = resp.json()
                tests = result.get("tests", [])
        except Exception:
            pass

    return JSONResponse({"success": True, "tests": tests})
