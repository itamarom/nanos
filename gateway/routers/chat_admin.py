"""Gateway router for live chat — agent loop, tool dispatch, LLM calls.

All endpoints require X-Admin-Key. Mounted at /api/admin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import get_async_session
from shared.models import (
    ChatConversation, ChatMessage, PendingApproval,
    Nano, NanoApiKey, NanoPermission, RunLog,
)
from gateway.auth import verify_admin_key
from gateway.schemas import ChatRequest, ChatResponse
from shared.nano_types import safe_resolve

# SDK reference generator — auto-generated from actual NanosClient introspection
sys.path.insert(0, "/app/sdk")
try:
    from nanos_sdk.docgen import SDK_REFERENCE as _SDK_REFERENCE
except ImportError:
    _SDK_REFERENCE = "*(SDK reference unavailable — nanos_sdk not found)*"

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_admin_key)])

MAX_ITERATIONS = 25

# ---------------------------------------------------------------------------
# Tool registry — import from the mounted nano-harness volume
# ---------------------------------------------------------------------------
sys.path.insert(0, "/nanos/nano-harness") if "/nanos/nano-harness" not in sys.path else None
from tools import _TOOL_REGISTRY, ToolDef, _API_GUIDELINES, get_tools_for_api_prefixes, SENSITIVE_TOOL_NAMES  # noqa: E402

# Build API_GROUPS: maps group name → set of permission strings
# e.g. "hubspot" → {"hubspot.contacts.list", "hubspot.contacts.get", ...}
API_GROUPS: dict[str, set[str]] = {}
for _td in _TOOL_REGISTRY:
    _group = _td.permission.split(".")[0]
    API_GROUPS.setdefault(_group, set()).add(_td.permission)

# Build tool name → ToolDef lookup
_TOOL_BY_NAME: dict[str, ToolDef] = {}  # type: ignore[no-any-unimported]
for _td in _TOOL_REGISTRY:
    if _td.name not in _TOOL_BY_NAME:
        _TOOL_BY_NAME[_td.name] = _td


_get_tools_and_sensitive = get_tools_for_api_prefixes

# Utility tools always available (no API required)
_SLEEP_TOOL = {
    "type": "function",
    "function": {
        "name": "sleep",
        "description": "Wait for the specified number of seconds before continuing.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Number of seconds to wait (max 300)"},
            },
            "required": ["seconds"],
        },
    },
}
_UTILITY_TOOLS = [_SLEEP_TOOL]


# ---------------------------------------------------------------------------
# Background-task SSE architecture — StreamState + registry
# ---------------------------------------------------------------------------

@dataclass
class StreamState:
    """Holds SSE events produced by a background agent task."""
    events: list[str] = field(default_factory=list)
    done: bool = False
    notify: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, event_str: str) -> None:
        self.events.append(event_str)
        self.notify.set()

    def finish(self) -> None:
        self.done = True
        self.notify.set()


_active_streams: dict[str, StreamState] = {}


def _build_system_prompt(enabled_apis: list[str]) -> str:
    """Build a system prompt with today's date and per-API guidelines."""
    parts = [
        "You are a helpful assistant with access to real APIs. "
        "Use the provided tools to answer the user's questions.\n\n"
        "## Behavior\n"
        "- Be proactive. When a task requires multiple API calls, chain them automatically "
        "without asking the user for permission. For example, if asked to list ALL contacts, "
        "paginate through every page until done — don't stop after the first page to ask.\n"
        "- When multiple independent pieces of data are needed, call several tools in "
        "parallel in a single response (e.g. fetch contacts and deals at the same time).\n"
        "- Only pause to ask the user when there is genuine ambiguity about what they want. "
        "Never ask for confirmation before executing a read-only operation.\n"
        "- Sensitive/write operations (create, update, delete, send) will go through an "
        "approval flow automatically — you do not need to ask the user before calling them.\n"
        "- When searching and the first query returns no results, consider alternative "
        "spellings, abbreviations, or different properties before concluding there are none.",
        f"Today is {datetime.utcnow().strftime('%Y-%m-%d')}.",
    ]
    for prefix, text in _API_GUIDELINES.items():
        if any(api == prefix or api.startswith(prefix + ".") or prefix.startswith(api + ".") for api in enabled_apis):
            parts.append(text)
    if any(api == "hubspot" or api.startswith("hubspot.") for api in enabled_apis):
        parts.append(
            "HubSpot search filters use AND logic — all filters in one call must match "
            "simultaneously. If you need OR logic (e.g. contacts in New York OR New Jersey), "
            "make separate search calls for each condition and combine the results. "
            "Also try variations: search by 'state' not just 'city', try abbreviations "
            "(NY, NYC), and use CONTAINS_TOKEN for partial matches."
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class ConversationCreate(BaseModel):
    title: str = "New Chat"
    model: str = "gpt-4.1"
    enabled_apis: list[str] = []


class ConversationUpdate(BaseModel):
    title: str | None = None
    model: str | None = None
    enabled_apis: list[str] | None = None


class ChatSend(BaseModel):
    message: str
    model: str | None = None
    enabled_apis: list[str] | None = None


class ChatContinue(BaseModel):
    pass


class MessageOut(BaseModel):
    id: str
    role: str
    content: str | None = None
    tool_calls: Any | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: Any | None = None
    tool_status: str | None = None
    approval_id: str | None = None
    created_at: str


class ConversationOut(BaseModel):
    id: str
    title: str
    model: str
    enabled_apis: list[str]
    status: str
    created_at: str
    updated_at: str


class AgentResponse(BaseModel):
    status: str
    messages: list[MessageOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg_to_out(m: ChatMessage) -> MessageOut:
    tc = None
    if m.tool_calls:
        try:
            tc = json.loads(m.tool_calls)
        except (json.JSONDecodeError, TypeError):
            tc = m.tool_calls
    ta = None
    if m.tool_args:
        try:
            ta = json.loads(m.tool_args)
        except (json.JSONDecodeError, TypeError):
            ta = m.tool_args
    return MessageOut(
        id=str(m.id),
        role=m.role,
        content=m.content,
        tool_calls=tc,
        tool_call_id=m.tool_call_id,
        tool_name=m.tool_name,
        tool_args=ta,
        tool_status=m.tool_status,
        approval_id=str(m.approval_id) if m.approval_id else None,
        created_at=m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if m.created_at else "",
    )


def _conv_to_out(c: ChatConversation) -> ConversationOut:
    apis: list[str] = []
    try:
        apis = json.loads(c.enabled_apis) if c.enabled_apis else []
    except (json.JSONDecodeError, TypeError):
        pass
    return ConversationOut(
        id=str(c.id),
        title=c.title,
        model=c.model,
        enabled_apis=apis,
        status=c.status,
        created_at=c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if c.created_at else "",
        updated_at=c.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if c.updated_at else "",
    )


def _build_openai_messages(db_messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert DB messages into OpenAI-format messages list."""
    msgs: list[dict[str, Any]] = []
    for m in db_messages:
        if m.role == "user":
            msgs.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant"}
            if m.content:
                entry["content"] = m.content
            if m.tool_calls:
                try:
                    entry["tool_calls"] = json.loads(m.tool_calls)
                except (json.JSONDecodeError, TypeError):
                    pass
                if "content" not in entry:
                    entry["content"] = None
            msgs.append(entry)
        elif m.role == "tool":
            msgs.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content or "",
            })
    return msgs


# ---------------------------------------------------------------------------
# Tool dispatch — calls existing gateway service functions directly
# ---------------------------------------------------------------------------

async def _dispatch_tool(tool_name: str, args: dict[str, Any], session: AsyncSession) -> str:
    """Execute a non-sensitive tool and return the result as a JSON string."""
    try:
        result = await _call_service(tool_name, args, session)
        return json.dumps(result, default=str) if result is not None else '{"ok": true}'
    except Exception as e:
        logger.exception("Tool dispatch error for %s", tool_name)
        return json.dumps({"error": str(e)})


async def _call_service(tool_name: str, args: dict[str, Any], session: AsyncSession) -> Any:
    """Route a tool call to the appropriate gateway service function."""
    # Strip post_execution_log — not sent to APIs
    args.pop("post_execution_log", None)

    # --- Calendar ---
    if tool_name == "calendar_events_list":
        from gateway.services.google_calendar_service import list_events
        return await list_events(args["start"], args["end"], session)

    # --- Gmail ---
    if tool_name == "gmail_get_profile":
        from gateway.services.gmail_service import get_profile
        return await get_profile(session)
    if tool_name == "gmail_messages_list":
        from gateway.services.gmail_service import list_messages
        return await list_messages(args["query"], session, max_results=args.get("max_results", 10))
    if tool_name == "gmail_messages_get":
        from gateway.services.gmail_service import get_message
        return await get_message(args["message_id"], session)
    if tool_name == "gmail_threads_get":
        from gateway.services.gmail_service import get_thread
        return await get_thread(args["thread_id"], session)

    # --- Slack ---
    if tool_name == "slack_send_message":
        from gateway.services.slack_service import send_message
        return await send_message(args["text"], session)

    # --- HubSpot Contacts ---
    if tool_name == "hubspot_contacts_list":
        from gateway.services.hubspot_service import list_contacts
        return await list_contacts(session, limit=args.get("limit", 20),
                                   after=args.get("after"), properties=args.get("properties"))
    if tool_name == "hubspot_contacts_get":
        from gateway.services.hubspot_service import get_contact
        return await get_contact(args["contact_id"], session, properties=args.get("properties"))
    if tool_name == "hubspot_contacts_search":
        from gateway.services.hubspot_service import search_contacts
        return await search_contacts(args["filters"], args.get("properties"), args.get("limit", 20), session)

    # --- HubSpot Deals ---
    if tool_name == "hubspot_deals_list":
        from gateway.services.hubspot_service import list_deals
        return await list_deals(session, limit=args.get("limit", 20),
                                after=args.get("after"), properties=args.get("properties"))
    if tool_name == "hubspot_deals_get":
        from gateway.services.hubspot_service import get_deal
        return await get_deal(args["deal_id"], session, properties=args.get("properties"))
    if tool_name == "hubspot_deals_search":
        from gateway.services.hubspot_service import search_deals
        return await search_deals(args["filters"], args.get("properties"), args.get("limit", 20), session)

    # --- HubSpot Tasks ---
    if tool_name == "hubspot_tasks_list":
        from gateway.services.hubspot_service import list_tasks
        return await list_tasks(session, limit=args.get("limit", 20),
                                after=args.get("after"), properties=args.get("properties"))
    if tool_name == "hubspot_tasks_get":
        from gateway.services.hubspot_service import get_task
        return await get_task(args["task_id"], session, properties=args.get("properties"))
    if tool_name == "hubspot_tasks_search":
        from gateway.services.hubspot_service import search_tasks
        return await search_tasks(args["filters"], args.get("properties"), args.get("limit", 20), session)

    # --- HubSpot Properties ---
    if tool_name == "hubspot_properties_list":
        from gateway.services.hubspot_service import list_properties
        return await list_properties(args["object_type"], session)
    if tool_name == "hubspot_properties_get":
        from gateway.services.hubspot_service import get_property
        return await get_property(args["object_type"], args["property_name"], session)

    # --- WhatsApp ---
    if tool_name == "whatsapp_chats_list":
        from gateway.services.whatsapp_service import list_chats
        return await list_chats(args.get("limit", 20), session)
    if tool_name == "whatsapp_messages_list":
        from gateway.services.whatsapp_service import list_messages as wa_list_messages
        return await wa_list_messages(args.get("chat_jid"), args.get("limit", 20), args.get("before"), args.get("after"), session)
    if tool_name == "whatsapp_messages_search":
        from gateway.services.whatsapp_service import search_messages
        return await search_messages(args["query"], session)
    if tool_name == "whatsapp_groups_list":
        from gateway.services.whatsapp_service import list_groups
        return await list_groups(session)
    if tool_name == "whatsapp_media_download":
        from gateway.services.whatsapp_service import download_media
        return await download_media(args["chat_jid"], args["message_id"], session)
    if tool_name == "whatsapp_history_backfill":
        from gateway.services.whatsapp_service import history_backfill
        return await history_backfill(args["chat_jid"], args.get("requests", 1), args.get("count", 50), session)

    # --- Notion ---
    if tool_name == "notion_search":
        from gateway.services.notion_service import search
        return await search(session, query=args.get("query"), filter=args.get("filter"),
                            page_size=args.get("page_size"), start_cursor=args.get("start_cursor"))
    if tool_name == "notion_databases_get":
        from gateway.services.notion_service import get_database
        return await get_database(args["database_id"], session)
    if tool_name == "notion_databases_query":
        from gateway.services.notion_service import query_database
        return await query_database(args["database_id"], session, filter=args.get("filter"),
                                    sorts=args.get("sorts"), page_size=args.get("page_size"),
                                    start_cursor=args.get("start_cursor"))
    if tool_name == "notion_pages_get":
        from gateway.services.notion_service import get_page
        return await get_page(args["page_id"], session)
    if tool_name == "notion_blocks_list":
        from gateway.services.notion_service import list_blocks
        return await list_blocks(args["block_id"], session, page_size=args.get("page_size"),
                                 start_cursor=args.get("start_cursor"))
    if tool_name == "notion_comments_list":
        from gateway.services.notion_service import list_comments
        return await list_comments(session, block_id=args.get("block_id"),
                                   page_size=args.get("page_size"), start_cursor=args.get("start_cursor"))
    if tool_name == "notion_users_list":
        from gateway.services.notion_service import list_users
        return await list_users(session, page_size=args.get("page_size"),
                                start_cursor=args.get("start_cursor"))

    # --- Linear ---
    if tool_name == "linear_issues_list":
        from gateway.services.linear_service import list_issues
        return await list_issues(session, filter=args.get("filter"),
                                 first=args.get("first", 50), after=args.get("after"))
    if tool_name == "linear_issues_get":
        from gateway.services.linear_service import get_issue
        return await get_issue(args["issue_id"], session)
    if tool_name == "linear_comments_list":
        from gateway.services.linear_service import list_comments as linear_list_comments
        return await linear_list_comments(args["issue_id"], session,
                                          first=args.get("first", 50), after=args.get("after"))
    if tool_name == "linear_projects_list":
        from gateway.services.linear_service import list_projects
        return await list_projects(session, first=args.get("first", 50), after=args.get("after"))
    if tool_name == "linear_projects_get":
        from gateway.services.linear_service import get_project
        return await get_project(args["project_id"], session)
    if tool_name == "linear_teams_list":
        from gateway.services.linear_service import list_teams
        return await list_teams(session)
    if tool_name == "linear_cycles_list":
        from gateway.services.linear_service import list_cycles
        return await list_cycles(args["team_id"], session,
                                 first=args.get("first", 20), after=args.get("after"))
    if tool_name == "linear_users_list":
        from gateway.services.linear_service import list_users as linear_list_users
        return await linear_list_users(session)

    # --- Nano Types (code on disk) ---
    if tool_name == "list_nano_types":
        return await _list_nano_types()
    if tool_name == "get_nano_type":
        return await _get_nano_type(args["name"])
    if tool_name == "create_nano_type":
        return await _create_nano_type(
            args["name"], args["description"], args["script_code"],
            args["permissions"], args.get("schedule"),
        )
    if tool_name == "update_nano_type":
        return await _update_nano_type(args)
    if tool_name == "delete_nano_type":
        return await _delete_nano_type(args["name"], session)
    if tool_name == "show_code_block":
        # No-op server-side; the UI renders this from tool_args
        return {"displayed": True}
    # --- Nanos (registered instances in DB) ---
    if tool_name == "list_nanos":
        return await _list_nanos(session)
    if tool_name == "get_nano":
        return await _get_nano(args["identifier"], session)
    if tool_name == "create_nano":
        return await _create_nano(args["name"], args["permissions"], session)
    if tool_name == "update_nano":
        return await _update_nano(args, session)
    if tool_name == "delete_nano":
        return await _delete_nano(args["name"], session)
    if tool_name == "run_nano":
        return await _run_nano(args["name"], session, draft_mode=args.get("draft_mode", False))
    if tool_name == "run_temp_nano":
        return await _run_temp_nano(
            args["script_code"], args.get("permissions", []), session,
            draft_mode=args.get("draft_mode", False),
        )
    if tool_name == "nano_run_history":
        return await _nano_run_history(args["nano_name"], args.get("limit", 10), session)
    if tool_name == "nano_read_log":
        return await _nano_read_log(args["run_log_id"], session)

    # --- Utility ---
    if tool_name == "sleep":
        seconds = min(max(float(args.get("seconds", 1)), 0), 300)
        await asyncio.sleep(seconds)
        return {"slept": seconds}

    return {"error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# Nano management service functions
# ---------------------------------------------------------------------------

NANOS_BASE_DIR = "/nanos"


async def _list_nano_types() -> list[dict[str, Any]]:
    """List all nano type directories on disk."""
    out: list[dict[str, Any]] = []
    if not os.path.isdir(NANOS_BASE_DIR):
        return out
    for entry in sorted(os.listdir(NANOS_BASE_DIR)):
        type_dir = os.path.join(NANOS_BASE_DIR, entry)
        config_path = os.path.join(type_dir, "config.yaml")
        if not os.path.isdir(type_dir) or not os.path.exists(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            out.append({
                "name": entry,
                "description": cfg.get("description", ""),
                "permissions": cfg.get("permissions", []),
                "schedule": cfg.get("schedule"),
            })
        except Exception:
            out.append({"name": entry, "description": "(error reading config)", "permissions": [], "schedule": None})
    return out


async def _get_nano_type(name: str) -> dict[str, Any]:
    """Read a nano type's code and config from disk."""
    try:
        type_dir = safe_resolve(NANOS_BASE_DIR, name)
    except ValueError:
        return {"error": f"Invalid nano type name: {name}"}
    if not os.path.isdir(type_dir):
        return {"error": f"Nano type not found on disk: {name}"}
    result: dict[str, Any] = {"name": name}
    config_path = os.path.join(type_dir, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            result["config"] = yaml.safe_load(f) or {}
    script_path = os.path.join(type_dir, "nano.py")
    if os.path.exists(script_path):
        with open(script_path, "r", encoding="utf-8") as f:
            result["script_code"] = f.read()
    return result


async def _create_nano_type(
    name: str, description: str, script_code: str,
    permissions: list[str], schedule: str | None,
) -> dict[str, Any]:
    """Write nano.py + config.yaml to /nanos/{name}/."""
    if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name) and not re.match(r'^[a-z0-9]$', name):
        return {"error": "Name must be lowercase alphanumeric with hyphens (e.g. 'daily-summary')"}

    nano_dir = os.path.join(NANOS_BASE_DIR, name)
    os.makedirs(nano_dir, exist_ok=True)

    with open(os.path.join(nano_dir, "nano.py"), "w", encoding="utf-8") as f:
        f.write(script_code)

    config_data = {"name": name, "description": description, "permissions": permissions}
    if schedule:
        config_data["schedule"] = schedule
    with open(os.path.join(nano_dir, "config.yaml"), "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False)

    return {"name": name, "message": f"Nano type '{name}' created on disk"}


async def _update_nano_type(args: dict[str, Any]) -> dict[str, Any]:
    """Update an existing nano type's code and/or config on disk."""
    name = args["name"]
    try:
        type_dir = safe_resolve(NANOS_BASE_DIR, name)
    except ValueError:
        return {"error": f"Invalid nano type name: {name}"}
    config_path = os.path.join(type_dir, "config.yaml")
    if not os.path.isdir(type_dir) or not os.path.exists(config_path):
        return {"error": f"Nano type '{name}' not found on disk"}

    # Rewrite nano.py if provided
    if args.get("script_code"):
        with open(os.path.join(type_dir, "nano.py"), "w", encoding="utf-8") as f:
            f.write(args["script_code"])

    # Update config.yaml fields if any provided
    config_changed = False
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if args.get("description") is not None:
        cfg["description"] = args["description"]
        config_changed = True
    if args.get("permissions") is not None:
        cfg["permissions"] = args["permissions"]
        config_changed = True
    if "schedule" in args:
        if args["schedule"]:
            cfg["schedule"] = args["schedule"]
        else:
            cfg.pop("schedule", None)
        config_changed = True
    if config_changed:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)

    updated = []
    if args.get("script_code"):
        updated.append("nano.py")
    if config_changed:
        updated.append("config.yaml")
    return {"name": name, "message": f"Nano type '{name}' updated: {', '.join(updated) or 'no changes'}"}


async def _delete_nano_type(name: str, session: AsyncSession) -> dict[str, Any]:
    """Delete a nano type from disk. Checks for registered instances first."""
    import shutil
    try:
        type_dir = safe_resolve(NANOS_BASE_DIR, name)
    except ValueError:
        return {"error": f"Invalid nano type name: {name}"}
    if not os.path.isdir(type_dir):
        return {"error": f"Nano type '{name}' not found on disk"}

    # Check for instances referencing this type
    result = await session.execute(
        select(Nano).where(Nano.type_name == name, Nano.name != "__chat__")
    )
    instances = result.scalars().all()
    if instances:
        return {
            "error": f"Cannot delete: {len(instances)} nano(s) reference this type. Delete them first.",
            "instances": [{"name": n.name, "id": str(n.id), "is_active": n.is_active} for n in instances],
        }

    shutil.rmtree(type_dir)
    return {"name": name, "message": f"Nano type '{name}' deleted from disk"}


async def _list_nanos(session: AsyncSession) -> list[dict[str, Any]]:
    """List all registered nanos from DB."""
    result = await session.execute(
        select(Nano)
        .options(selectinload(Nano.permissions))
        .where(Nano.name != "__chat__").order_by(Nano.name)
    )
    nanos = result.scalars().all()
    return [{
        "id": str(n.id),
        "name": n.name,
        "description": n.description or "",
        "type_name": n.type_name or "",
        "schedule": n.schedule,
        "is_active": n.is_active,
        "permissions": [p.endpoint for p in n.permissions],
    } for n in nanos]


async def _get_nano(identifier: str, session: AsyncSession) -> dict[str, Any]:
    """Get a specific registered nano by name or UUID."""
    nano = None
    try:
        uid = uuid.UUID(identifier)
        result = await session.execute(
            select(Nano).options(selectinload(Nano.permissions)).where(Nano.id == uid)
        )
        nano = result.scalar_one_or_none()
    except ValueError:
        pass
    if not nano:
        result = await session.execute(
            select(Nano).options(selectinload(Nano.permissions)).where(Nano.name == identifier)
        )
        nano = result.scalar_one_or_none()
    if not nano:
        return {"error": f"Nano not found: {identifier}"}
    return {
        "id": str(nano.id),
        "name": nano.name,
        "description": nano.description or "",
        "script_path": nano.script_path,
        "schedule": nano.schedule,
        "is_active": nano.is_active,
        "type_name": nano.type_name or "",
        "parameters": nano.parameters,
        "permissions": [p.endpoint for p in nano.permissions],
    }


async def _create_nano(name: str, permissions: list[str], session: AsyncSession) -> dict[str, Any]:
    """Register a nano instance in the DB from an existing type on disk."""
    # Verify type exists on disk
    try:
        type_dir = safe_resolve(NANOS_BASE_DIR, name)
    except ValueError:
        return {"error": f"Invalid nano type name: {name}"}
    config_path = os.path.join(type_dir, "config.yaml")
    if not os.path.isdir(type_dir) or not os.path.exists(config_path):
        return {"error": f"Nano type '{name}' not found on disk. Call create_nano_type first."}

    # Read description from config
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # Check for collision in DB
    result = await session.execute(select(Nano).where(Nano.name == name))
    if result.scalar_one_or_none():
        return {"error": f"A nano named '{name}' is already registered"}

    nano = Nano(
        id=uuid.uuid4(),
        name=name,
        description=cfg.get("description", ""),
        script_path=f"{name}/nano.py",
        schedule=cfg.get("schedule"),
        is_active=True,
        type_name=name,
    )
    session.add(nano)
    await session.flush()

    api_key = "nk_" + secrets.token_hex(16)
    session.add(NanoApiKey(nano_id=nano.id, key=api_key))

    for perm in permissions:
        session.add(NanoPermission(nano_id=nano.id, endpoint=perm))

    await session.commit()
    return {
        "id": str(nano.id),
        "name": name,
        "api_key": api_key,
        "message": f"Nano '{name}' registered successfully",
    }


async def _update_nano(args: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    """Update a registered nano's metadata."""
    name = args["name"]
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return {"error": f"Nano not found: {name}"}

    if "description" in args and args["description"] is not None:
        nano.description = args["description"]
    if "schedule" in args:
        nano.schedule = args["schedule"] if args["schedule"] else None
    if "is_active" in args and args["is_active"] is not None:
        nano.is_active = args["is_active"]

    if "permissions" in args and args["permissions"] is not None:
        for perm in list(nano.permissions):
            await session.delete(perm)
        await session.flush()
        for perm_str in args["permissions"]:
            session.add(NanoPermission(nano_id=nano.id, endpoint=perm_str))

    nano.updated_at = datetime.utcnow()
    await session.commit()
    return {"id": str(nano.id), "name": name, "message": f"Nano '{name}' updated"}


async def _delete_nano(name: str, session: AsyncSession) -> dict[str, Any]:
    """Delete a registered nano (DB record). Files on disk are preserved."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return {"error": f"Nano not found: {name}"}
    await session.delete(nano)
    await session.commit()
    return {"deleted": True}


async def _run_nano(name: str, session: AsyncSession, draft_mode: bool = False) -> dict[str, Any]:
    """Trigger a manual run of a registered nano via Celery."""
    result = await session.execute(select(Nano).where(Nano.name == name))
    nano = result.scalar_one_or_none()
    if not nano:
        return {"error": f"Nano not found: {name}"}

    run_log = RunLog(
        id=uuid.uuid4(),
        nano_id=nano.id,
        trigger="manual",
        started_at=datetime.utcnow(),
        status="running",
        draft_mode=draft_mode,
    )
    session.add(run_log)
    await session.commit()

    from celery import Celery
    from shared.config import REDIS_URL
    celery_app = Celery(broker=REDIS_URL)
    task = celery_app.send_task(
        "tasks.run_nano_task",
        args=[str(nano.id), "manual", str(run_log.id), draft_mode],
    )
    run_log.celery_task_id = task.id
    await session.commit()

    msg = f"Nano '{name}' triggered"
    if draft_mode:
        msg += " in draft mode (sensitive calls will be logged but not executed)"
    msg += ". Use nano_read_log with run_log_id to check results."

    return {
        "run_log_id": str(run_log.id),
        "name": name,
        "draft_mode": draft_mode,
        "message": msg,
    }


async def _nano_run_history(nano_name: str, limit: int, session: AsyncSession) -> list[dict[str, Any]] | dict[str, str]:
    result = await session.execute(select(Nano).where(Nano.name == nano_name))
    nano = result.scalar_one_or_none()
    if not nano:
        return {"error": f"Nano not found: {nano_name}"}
    log_result = await session.execute(
        select(RunLog)
        .where(RunLog.nano_id == nano.id)
        .order_by(RunLog.started_at.desc())
        .limit(limit)
    )
    logs = log_result.scalars().all()
    return [{
        "id": str(rl.id),
        "trigger": rl.trigger,
        "started_at": rl.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if rl.started_at else None,
        "finished_at": rl.finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if rl.finished_at else None,
        "status": rl.status,
        "exit_code": rl.exit_code,
    } for rl in logs]


async def _nano_read_log(run_log_id: str, session: AsyncSession) -> dict[str, Any]:
    try:
        uid = uuid.UUID(run_log_id)
    except ValueError:
        return {"error": "Invalid run_log_id"}
    result = await session.execute(select(RunLog).where(RunLog.id == uid))
    rl = result.scalar_one_or_none()
    if not rl:
        return {"error": f"Run log not found: {run_log_id}"}
    return {
        "status": rl.status,
        "exit_code": rl.exit_code,
        "stdout": rl.stdout or "",
        "stderr": rl.stderr or "",
        "started_at": rl.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if rl.started_at else None,
        "finished_at": rl.finished_at.strftime("%Y-%m-%dT%H:%M:%SZ") if rl.finished_at else None,
    }


# ---------------------------------------------------------------------------
# Lazy __chat__ nano for PendingApproval FK
# ---------------------------------------------------------------------------

_chat_nano_id: uuid.UUID | None = None


async def _get_chat_nano_id(session: AsyncSession) -> uuid.UUID:
    """Get or create the __chat__ nano used for chat approval FKs."""
    global _chat_nano_id
    if _chat_nano_id:
        return _chat_nano_id

    result = await session.execute(select(Nano).where(Nano.name == "__chat__"))
    nano = result.scalar_one_or_none()
    if nano:
        _chat_nano_id = nano.id
        return nano.id

    nano = Nano(
        id=uuid.uuid4(),
        name="__chat__",
        description="Virtual nano for live chat approvals",
        script_path="__chat__",
        type_name="__chat__",
        is_active=False,
    )
    session.add(nano)
    await session.commit()
    _chat_nano_id = nano.id
    return nano.id


# ---------------------------------------------------------------------------
# Ephemeral nano execution
# ---------------------------------------------------------------------------

_MAX_OUTPUT_CHARS = 10_000


async def _run_temp_nano(
    script_code: str,
    permissions: list[str],
    session: AsyncSession,
    draft_mode: bool = False,
) -> dict[str, Any]:
    """Run code as an ephemeral nano — create temp DB rows, execute, clean up.

    Creates a temporary Nano + API key + permissions in the DB so the
    gateway's existing auth/permission checks work unchanged.  The nano
    row is deleted (cascade cleans up key + permissions) in a finally block.
    """
    temp_nano_id = uuid.uuid4()
    temp_name = f"__temp_{temp_nano_id.hex[:12]}__"
    api_key_str = f"nk_{secrets.token_hex(16)}"
    tmp_path: str | None = None

    try:
        # ---- 1. Create ephemeral nano + key + permissions in DB ----
        nano = Nano(
            id=temp_nano_id,
            name=temp_name,
            description="Ephemeral nano for run_temp_nano",
            script_path="__ephemeral__",
            type_name=None,
            is_active=False,
        )
        session.add(nano)
        session.add(NanoApiKey(
            id=uuid.uuid4(), nano_id=temp_nano_id,
            key=api_key_str, is_active=True,
        ))
        for perm in permissions:
            session.add(NanoPermission(
                id=uuid.uuid4(), nano_id=temp_nano_id, endpoint=perm,
            ))
        await session.commit()

        # ---- 2. Write script to a temp file ----
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="temp_nano_")
        os.write(fd, script_code.encode())
        os.close(fd)

        # ---- 3. Build env mirroring worker/runner.py ----
        env = os.environ.copy()
        env["NANO_API_KEY"] = api_key_str
        env["NANO_GATEWAY_URL"] = "http://localhost:8000"
        env["NANO_PERMISSIONS"] = ",".join(permissions)
        # Prepend SDK so `from nanos_sdk import NanosClient` works
        sdk_path = "/app/sdk"
        env["PYTHONPATH"] = f"{sdk_path}:{env.get('PYTHONPATH', '')}"
        if draft_mode:
            env["NANO_DRAFT_MODE"] = "true"

        # ---- 4. Execute as subprocess with 60s timeout ----
        proc = await asyncio.create_subprocess_exec(
            sys.executable, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=60,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Script timed out after 60 seconds.",
                "draft_mode": draft_mode,
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Truncate large outputs
        if len(stdout) > _MAX_OUTPUT_CHARS:
            stdout = stdout[:_MAX_OUTPUT_CHARS] + f"\n... truncated ({len(stdout_bytes)} chars total)"
        if len(stderr) > _MAX_OUTPUT_CHARS:
            stderr = stderr[:_MAX_OUTPUT_CHARS] + f"\n... truncated ({len(stderr_bytes)} chars total)"

        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "draft_mode": draft_mode,
        }

    finally:
        # ---- 5. Clean up: delete temp nano + related rows + temp file ----
        # Delete explicitly in dependency order to avoid ORM cascade issues
        # (ORM cascade tries to nullify FKs, violating NOT NULL).
        try:
            await session.rollback()  # clear any broken transaction state
            # Re-parent any pending approvals to __chat__ so they stay visible
            chat_nano_id = await _get_chat_nano_id(session)
            await session.execute(
                update(PendingApproval)
                .where(PendingApproval.nano_id == temp_nano_id)
                .values(nano_id=chat_nano_id)
            )
            await session.execute(delete(NanoPermission).where(NanoPermission.nano_id == temp_nano_id))
            await session.execute(delete(NanoApiKey).where(NanoApiKey.nano_id == temp_nano_id))
            await session.execute(delete(Nano).where(Nano.id == temp_nano_id))
            await session.commit()
        except Exception:
            logger.exception("Failed to clean up ephemeral nano %s", temp_name)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def run_agent_loop(conversation: ChatConversation, session: AsyncSession) -> AgentResponse:
    """Run the synchronous agent loop within the current request."""
    from gateway.services.openai_service import chat_completion

    enabled_apis: list[str] = json.loads(conversation.enabled_apis) if conversation.enabled_apis else []
    tools_defs, sensitive_names = _get_tools_and_sensitive(enabled_apis)
    tools_defs = tools_defs + _UTILITY_TOOLS
    system_prompt = _build_system_prompt(enabled_apis)

    # Reload all messages from DB
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at)
    )
    db_messages = list(result.scalars().all())
    openai_messages = _build_openai_messages(db_messages)

    new_messages: list[ChatMessage] = []
    conversation.status = "running"
    await session.commit()

    for _iteration in range(MAX_ITERATIONS):
        # Call LLM — retry transient connection errors up to 2 times
        resp: ChatResponse | None = None
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                chat_req = ChatRequest(
                    messages=[{"role": "system", "content": system_prompt}] + openai_messages,
                    model=conversation.model,
                    tools=tools_defs if tools_defs else None,
                    temperature=0.7,
                )
                resp = await chat_completion(chat_req, session)
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                last_error = e
                logger.warning("LLM call transient error (attempt %d/3): %s", _attempt + 1, e)
                await asyncio.sleep(2 ** _attempt)
                continue
            except Exception as e:
                last_error = e
                break
        if resp is None:
            logger.exception("LLM call failed after retries")
            err_msg = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=f"Error calling LLM: {last_error}",
            )
            session.add(err_msg)
            conversation.status = "error"
            await session.commit()
            new_messages.append(err_msg)
            return AgentResponse(status="error", messages=[_msg_to_out(m) for m in new_messages])

        # No tool calls → final response
        if not resp.tool_calls:
            assistant_msg = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=resp.content or "",
            )
            session.add(assistant_msg)
            conversation.status = "idle"
            await session.commit()
            new_messages.append(assistant_msg)
            return AgentResponse(status="idle", messages=[_msg_to_out(m) for m in new_messages])

        # Has tool calls — save assistant message with tool_calls JSON
        tc_json = json.dumps([
            {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in resp.tool_calls
        ])
        assistant_msg = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=resp.content,
            tool_calls=tc_json,
        )
        session.add(assistant_msg)
        await session.flush()
        new_messages.append(assistant_msg)

        # Also add assistant message to openai_messages for context
        oai_assistant: dict[str, Any] = {"role": "assistant", "content": resp.content}
        oai_assistant["tool_calls"] = json.loads(tc_json)
        openai_messages.append(oai_assistant)

        # Process each tool call
        batch_id = None
        sensitive_approval_ids = []

        for tc in resp.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            # Check if tool's API permission is enabled (prefix match)
            tool_def = _TOOL_BY_NAME.get(tool_name)
            if tool_def:
                if not any(tool_def.permission.startswith(api) for api in enabled_apis):
                    # Blocked — API not enabled
                    blocked_content = json.dumps({"error": f"API '{tool_def.permission}' is not enabled. Enable it in the settings panel."})
                    blocked_msg = ChatMessage(
                        conversation_id=conversation.id,
                        role="tool",
                        content=blocked_content,
                        tool_call_id=tc.id,
                        tool_name=tool_name,
                        tool_args=json.dumps(tool_args),
                        tool_status="blocked",
                    )
                    session.add(blocked_msg)
                    new_messages.append(blocked_msg)
                    openai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": blocked_content})
                    continue

            # Check if sensitive — collect into batch
            if tool_name in sensitive_names:
                nano_id = await _get_chat_nano_id(session)
                perm_str = tool_def.permission if tool_def else tool_name

                wait_str = tool_args.pop("wait_until_date", None)
                wait_until = datetime.fromisoformat(wait_str) if wait_str else None

                if batch_id is None:
                    batch_id = str(uuid.uuid4())[:8]

                approval = PendingApproval(
                    nano_id=nano_id,
                    batch_id=batch_id,
                    endpoint=perm_str,
                    method="POST",
                    request_body=json.dumps(tool_args),
                    status="pending",
                    wait_until_date=wait_until,
                )
                session.add(approval)
                await session.flush()

                pending_msg = ChatMessage(
                    conversation_id=conversation.id,
                    role="tool",
                    content=None,
                    tool_call_id=tc.id,
                    tool_name=tool_name,
                    tool_args=json.dumps(tool_args),
                    tool_status="pending_approval",
                    approval_id=approval.id,
                )
                session.add(pending_msg)
                new_messages.append(pending_msg)
                sensitive_approval_ids.append(str(approval.id))
                continue

            # Non-sensitive — execute immediately
            result_str = await _dispatch_tool(tool_name, tool_args, session)
            tool_msg = ChatMessage(
                conversation_id=conversation.id,
                role="tool",
                content=result_str,
                tool_call_id=tc.id,
                tool_name=tool_name,
                tool_args=json.dumps(tool_args),
                tool_status="executed",
            )
            session.add(tool_msg)
            new_messages.append(tool_msg)
            openai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        # After processing all tool calls: if we collected sensitive approvals, stop
        if sensitive_approval_ids:
            conversation.status = "awaiting_approval"
            await session.commit()
            return AgentResponse(
                status="awaiting_approval",
                messages=[_msg_to_out(m) for m in new_messages],
            )

        await session.flush()
        # Loop continues — LLM will see tool results

    # Max iterations reached
    max_msg = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="I've reached the maximum number of tool calls for this turn. Please continue the conversation if you need more.",
    )
    session.add(max_msg)
    conversation.status = "idle"
    await session.commit()
    new_messages.append(max_msg)
    return AgentResponse(status="idle", messages=[_msg_to_out(m) for m in new_messages])


# ---------------------------------------------------------------------------
# Streaming agent loop — yields SSE events
# ---------------------------------------------------------------------------

def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE event string."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def run_agent_loop_stream(
    conversation: ChatConversation,
    session: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE event strings as the agent loop runs."""
    from gateway.services.openai_service import chat_completion_stream

    enabled_apis: list[str] = json.loads(conversation.enabled_apis) if conversation.enabled_apis else []
    tools_defs, sensitive_names = _get_tools_and_sensitive(enabled_apis)
    tools_defs = tools_defs + _UTILITY_TOOLS
    system_prompt = _build_system_prompt(enabled_apis)

    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at)
    )
    db_messages = list(result.scalars().all())
    openai_messages = _build_openai_messages(db_messages)

    conversation.status = "running"
    await session.commit()

    for _iteration in range(MAX_ITERATIONS):
        # Call LLM (streaming) — retry transient connection errors up to 2 times
        resp: ChatResponse | None = None
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                chat_req = ChatRequest(
                    messages=[{"role": "system", "content": system_prompt}] + openai_messages,
                    model=conversation.model,
                    tools=tools_defs if tools_defs else None,
                    temperature=0.7,
                )
                resp = None
                async for event_type, data in chat_completion_stream(chat_req, session):
                    if event_type == "text_delta":
                        yield _sse_event("text_delta", {"content": data})
                    elif event_type == "response":
                        resp = data
                if resp is None:
                    raise RuntimeError("No response received from LLM stream")
                break  # success
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
                last_error = e
                logger.warning("LLM call transient error (attempt %d/3): %s", _attempt + 1, e)
                await asyncio.sleep(2 ** _attempt)
                continue
            except Exception as e:
                last_error = e
                break  # non-retryable
        if resp is None:
            logger.exception("LLM call failed after retries")
            err_msg = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=f"Error calling LLM: {last_error}",
            )
            session.add(err_msg)
            conversation.status = "error"
            await session.commit()
            yield _sse_event("message", _msg_to_out(err_msg).dict())
            yield _sse_event("done", {"status": "error"})
            return

        # No tool calls → final text response
        if not resp.tool_calls:
            assistant_msg = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=resp.content or "",
            )
            session.add(assistant_msg)
            conversation.status = "idle"
            await session.commit()
            yield _sse_event("message", _msg_to_out(assistant_msg).dict())
            yield _sse_event("done", {"status": "idle"})
            return

        # Has tool calls
        tc_json = json.dumps([
            {"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in resp.tool_calls
        ])
        assistant_msg = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=resp.content,
            tool_calls=tc_json,
        )
        session.add(assistant_msg)
        await session.commit()

        # Yield assistant message (may have content)
        yield _sse_event("message", _msg_to_out(assistant_msg).dict())

        oai_assistant: dict[str, Any] = {"role": "assistant", "content": resp.content}
        oai_assistant["tool_calls"] = json.loads(tc_json)
        openai_messages.append(oai_assistant)

        batch_id = None
        sensitive_approval_ids = []

        for tc in resp.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            tool_def = _TOOL_BY_NAME.get(tool_name)

            # Check if tool's API is enabled
            if tool_def:
                if not any(tool_def.permission.startswith(api) for api in enabled_apis):
                    blocked_content = json.dumps({"error": f"API '{tool_def.permission}' is not enabled. Enable it in the settings panel."})
                    blocked_msg = ChatMessage(
                        conversation_id=conversation.id,
                        role="tool",
                        content=blocked_content,
                        tool_call_id=tc.id,
                        tool_name=tool_name,
                        tool_args=json.dumps(tool_args),
                        tool_status="blocked",
                    )
                    session.add(blocked_msg)
                    await session.commit()
                    yield _sse_event("message", _msg_to_out(blocked_msg).dict())
                    openai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": blocked_content})
                    continue

            # Check if sensitive — collect into batch instead of returning
            if tool_name in sensitive_names:
                nano_id = await _get_chat_nano_id(session)
                perm_str = tool_def.permission if tool_def else tool_name

                wait_str = tool_args.pop("wait_until_date", None)
                wait_until = datetime.fromisoformat(wait_str) if wait_str else None

                if batch_id is None:
                    batch_id = str(uuid.uuid4())[:8]

                approval = PendingApproval(
                    nano_id=nano_id,
                    batch_id=batch_id,
                    endpoint=perm_str,
                    method="POST",
                    request_body=json.dumps(tool_args),
                    status="pending",
                    wait_until_date=wait_until,
                )
                session.add(approval)
                await session.flush()

                pending_msg = ChatMessage(
                    conversation_id=conversation.id,
                    role="tool",
                    content=None,
                    tool_call_id=tc.id,
                    tool_name=tool_name,
                    tool_args=json.dumps(tool_args),
                    tool_status="pending_approval",
                    approval_id=approval.id,
                )
                session.add(pending_msg)
                await session.commit()
                sensitive_approval_ids.append(str(approval.id))
                msg_data = _msg_to_out(pending_msg).dict()
                msg_data["wait_until_date"] = approval.wait_until_date.isoformat() if approval.wait_until_date else None
                yield _sse_event("message", msg_data)
                continue

            # Yield a pending tool card immediately (no result yet)
            pending_tool_msg = ChatMessage(
                conversation_id=conversation.id,
                role="tool",
                content=None,
                tool_call_id=tc.id,
                tool_name=tool_name,
                tool_args=json.dumps(tool_args),
                tool_status="pending",
            )
            session.add(pending_tool_msg)
            await session.commit()
            yield _sse_event("message", _msg_to_out(pending_tool_msg).dict())

            # Execute tool
            result_str = await _dispatch_tool(tool_name, tool_args, session)

            # Update the pending message with result
            pending_tool_msg.content = result_str
            pending_tool_msg.tool_status = "executed"
            await session.commit()

            yield _sse_event("tool_update", {
                "id": str(pending_tool_msg.id),
                "content": result_str,
                "tool_status": "executed",
            })
            openai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        # After processing all tool calls: if we collected sensitive approvals, stop and wait
        if sensitive_approval_ids:
            conversation.status = "awaiting_approval"
            await session.commit()
            yield _sse_event("done", {
                "status": "awaiting_approval",
                "approval_ids": sensitive_approval_ids,
                "batch_id": batch_id,
            })
            return

        await session.commit()

    # Max iterations
    max_msg = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="I've reached the maximum number of tool calls for this turn. Please continue the conversation if you need more.",
    )
    session.add(max_msg)
    conversation.status = "idle"
    await session.commit()
    yield _sse_event("message", _msg_to_out(max_msg).dict())
    yield _sse_event("done", {"status": "idle"})


async def _run_stream_background(conv_id: str, stream_state: StreamState) -> None:
    """Background task: run the agent loop, pushing SSE events into stream_state.

    Owns its own DB session so it is completely decoupled from the HTTP
    request lifecycle — client disconnect does not affect this task.
    """
    from shared.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChatConversation).where(ChatConversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                stream_state.push(_sse_event("done", {"status": "error", "detail": "Conversation not found"}))
                return

            async for event_str in run_agent_loop_stream(conv, session):
                stream_state.push(event_str)
    except Exception as e:
        logger.exception("Background stream error for conv %s", conv_id)
        try:
            async with AsyncSessionLocal() as err_session:
                result = await err_session.execute(
                    select(ChatConversation).where(ChatConversation.id == conv_id)
                )
                conv = result.scalar_one_or_none()
                if conv:
                    conv.status = "error"
                    await err_session.commit()
        except Exception:
            logger.debug("Failed to set error status after background crash", exc_info=True)
        stream_state.push(_sse_event("done", {"status": "error", "detail": str(e)}))
    finally:
        stream_state.finish()
        # Schedule removal from registry after 60s (allows late re-attach)
        async def _deferred_remove():
            await asyncio.sleep(60)
            _active_streams.pop(conv_id, None)
        asyncio.create_task(_deferred_remove())


async def _consume_stream(
    stream_state: StreamState,
    start_offset: int = 0,
) -> AsyncGenerator[str, None]:
    """Async generator that reads events from a StreamState, yielding SSE.

    This is the consumer side — it just reads; the background task produces.
    If the client disconnects, this generator stops but the background task
    continues unaffected.
    """
    idx = start_offset
    while True:
        # Drain any buffered events
        while idx < len(stream_state.events):
            yield stream_state.events[idx]
            idx += 1

        if stream_state.done:
            # All events consumed and producer is done
            break

        # Wait for new events or heartbeat timeout
        stream_state.notify.clear()
        # Re-check after clear to avoid race
        if idx < len(stream_state.events) or stream_state.done:
            continue
        try:
            await asyncio.wait_for(stream_state.notify.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            yield _sse_event("heartbeat", {})


# ---------------------------------------------------------------------------
# Teaching prompt for nano development mode
# ---------------------------------------------------------------------------

def _build_nano_teaching_prompt(enabled_apis: list[str]) -> str:
    """Build a detailed prompt that teaches the LLM how to create nanos."""
    # Determine which APIs are available in this session
    available_apis = []
    api_labels = {
        "calendar": "Google Calendar",
        "gmail": "Gmail",
        "slack": "Slack",
        "hubspot": "HubSpot CRM",
        "whatsapp": "WhatsApp",
        "notion": "Notion",
        "linear": "Linear",
    }
    for api_prefix, label in api_labels.items():
        if any(api == api_prefix or api.startswith(api_prefix + ".") for api in enabled_apis):
            available_apis.append(label)

    available_section = ""
    if available_apis:
        available_section = "\n\nAPIs currently enabled in this chat session (the nano can use these):\n" + "\n".join(f"- {a}" for a in available_apis)

    return f"""[System: Nano Development Mode]

You are now in nano development mode. You can help the user create, view, and manage nanos — small Python automation scripts that run on the Nanos framework.

## Key Concepts: Nano Types vs Nanos

A **nano type** is a code template on disk — a directory containing `nano.py` + `config.yaml` under `/nanos/{{name}}/`. It defines *what* the automation does.

A **nano** (instance) is a registered entry in the database that references a type and can actually be run. It has an API key, permissions, schedule, and run history.

Creating a nano is a two-step process:
1. `create_nano_type` — writes the code files to disk
2. `create_nano` — registers the nano in the DB from the type on disk

## What does a Nano do?

A nano automates a task by calling external APIs through the Nanos gateway. Nanos:
- Are single-purpose automation scripts
- Run on a schedule (cron) or are triggered manually
- Access all APIs through the secure gateway using the NanosClient SDK
- Never call external APIs directly

## Nano Type Structure

Each nano type is a single `nano.py` file that follows this template:

```python
from nanos_sdk import NanosClient
import logging

logger = logging.getLogger("nano-name")

def main():
    client = NanosClient()  # Reads NANO_API_KEY + NANO_GATEWAY_URL from env

    # Your automation logic here
    # Use client methods to call APIs

    logger.info("Done!")

if __name__ == "__main__":
    main()
```

{_SDK_REFERENCE}

**CRITICAL SDK RULES — violations will crash the nano:**
- **ONLY use methods listed above.** Do NOT guess or invent method names, parameters, or keyword arguments. If a method or parameter is not listed above, it does NOT exist.
- **ONLY use the exact parameters shown in each method signature.** Do NOT add extra keyword arguments (e.g. `wait_until_date`, `timeout`, `format`). The signatures above are complete — there are no hidden optional parameters.
- **ONLY import `NanosClient` from `nanos_sdk`.** The SDK has no other public exports. Use the Python standard library for everything else (e.g. `import inspect`, not `from nanos_sdk import inspect`).

## What Makes a Good Nano

- **Single purpose**: Does one thing well
- **Proper logging**: Uses `logging.getLogger()` to report progress and results
- **Error handling**: Wraps API calls in try/except, logs errors, exits cleanly
- **Clear output**: Logs what it did and what it found
- **Idempotent**: Safe to run multiple times when possible
- **Uses parameters**: Configurable values come from `client.get_parameter()` not hardcoded

## What to Avoid

- Too complex — split into multiple nanos instead
- No error handling — always handle API failures
- Hardcoded values — use parameters or state for configurable data
- Direct API calls — always go through NanosClient
- Missing permissions — declare every API endpoint the nano calls

## Sensitive Endpoints & Approvals

Sensitive API calls (create, update, delete, send) return `ApprovalCreatedResponse` with
`approval_id` and `status` — NOT the actual API result. The page/record/message is only
created when the approval is executed (after human approval).

**Critical pattern:** When a sensitive call needs follow-up (e.g. create a page then add
content), include all data in the initial call if the API supports it. For example, pass
`children=` to `notion_pages_create()` instead of creating an empty page and appending
blocks separately.

If you must chain sensitive calls, use `client.wait_for_approval(approval_id)` to block
until the first approval resolves, then parse `response_body` to get the created resource ID.
{available_section}

## Your Available Tools

### Quick iteration (preferred for development):
- **run_temp_nano**: Run code as an ephemeral nano — no disk writes, no DB registration, no approval popups. Returns stdout/stderr/exit_code directly. Supports `draft_mode=true` to log sensitive calls without executing them.

### Nano Type tools (code on disk):
- **list_nano_types**: List all nano types on disk
- **get_nano_type**: Read a nano type's code and config from disk
- **create_nano_type**: Write nano.py + config.yaml to disk (creates the type)
- **update_nano_type**: Rewrite nano.py and/or update config.yaml for an existing type
- **delete_nano_type**: Delete a nano type from disk (instances must be deleted first)

### Nano tools (registered instances in DB):
- **list_nanos**: List all registered nanos
- **get_nano**: Get details of a specific nano by name or ID
- **create_nano**: Register a nano in the DB from an existing type on disk
- **update_nano**: Update a nano's metadata (description, schedule, permissions, active status)
- **delete_nano**: Delete a nano (removes DB record, files on disk are preserved)
- **run_nano**: Trigger a manual run of a nano (returns run_log_id, use nano_read_log to check result). Supports `draft_mode=true` for safe testing.
- **nano_run_history**: See recent run logs for a nano
- **nano_read_log**: Read stdout/stderr from a specific run

### UI tools:
- **show_code_block**: Display formatted, syntax-highlighted code in the chat

## Draft Mode

Use `draft_mode=true` on `run_temp_nano` or `run_nano` when you want to test logic without real side effects. In draft mode:
- The nano runs normally, but **sensitive API calls** (send email, create event, send WhatsApp, etc.) are **logged with full parameters but NOT executed**
- No Slack approval notifications are sent
- The run completes immediately without blocking on approvals

Use `draft_mode=false` (default) for real execution — the nano will actually call APIs. For sensitive calls, the approval flow applies as normal.

## Development Workflow

1. Ask the user what automation they want to build
2. Based on the conversation context and their description, generate the nano.py code
3. **First draft: add verbose logging.** Log every API call input and output, every decision branch, every variable value. This helps you diagnose issues from the run output. Example:
   ```python
   logger.info("Fetching messages with query=%r", query)
   messages = client.gmail_messages_list(q=query)
   logger.info("Got %d messages", len(messages))
   for msg in messages:
       logger.info("  Message %s: subject=%r, from=%r", msg.id, msg.subject, msg.from_)
   ```
4. Show the code using `show_code_block` with filename="nano.py" and language="python"
5. Use `run_temp_nano` to test the code immediately — no approval needed, results come back directly
6. Iterate: fix bugs, adjust logic, re-run with `run_temp_nano` until it works
7. If the nano uses sensitive APIs and you want to verify parameters without executing, use `draft_mode=true`
8. Once working, ask the user if they want to save it permanently
9. **Before saving: clean up logging.** Remove noisy per-item debug logs, keep high-level progress logs (e.g. "Processing 5 messages", "Sent notification to #channel"). The saved nano should log what it does, not dump every variable.
10. `create_nano_type` to write files to disk, then `create_nano` to register in DB
11. If the user wants changes after saving, update with `update_nano_type`"""


@router.get("/chat/nano-prompt")
async def get_nano_prompt(session: AsyncSession = Depends(get_async_session)) -> dict[str, str]:
    """Return the nano development teaching prompt text."""
    # We don't know which conversation this is for yet, so return with empty enabled_apis.
    # The frontend will send it as a user message with the current enabled_apis context.
    return {"prompt": _build_nano_teaching_prompt([])}


@router.get("/chat/nano-prompt/{conv_id}")
async def get_nano_prompt_for_conversation(
    conv_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Return the nano teaching prompt with APIs from a specific conversation."""
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    enabled_apis = []
    if conv and conv.enabled_apis:
        try:
            enabled_apis = json.loads(conv.enabled_apis)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"prompt": _build_nano_teaching_prompt(enabled_apis)}


@router.get("/chat/nano-type-instances/{name}")
async def get_nano_type_instances(name: str, session: AsyncSession = Depends(get_async_session)) -> dict[str, Any]:
    """Return registered nanos that reference a given nano type."""
    result = await session.execute(
        select(Nano).where(Nano.type_name == name, Nano.name != "__chat__")
    )
    nanos = result.scalars().all()
    return {"instances": [{"name": n.name, "id": str(n.id), "is_active": n.is_active} for n in nanos]}


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.post("/chat/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationCreate, session: AsyncSession = Depends(get_async_session)) -> ConversationOut:
    conv = ChatConversation(
        title=body.title,
        model=body.model,
        enabled_apis=json.dumps(body.enabled_apis),
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return _conv_to_out(conv)


@router.get("/chat/conversations", response_model=list[ConversationOut])
async def list_conversations(session: AsyncSession = Depends(get_async_session)) -> list[ConversationOut]:
    result = await session.execute(
        select(ChatConversation).order_by(ChatConversation.updated_at.desc()).limit(50)
    )
    convs = result.scalars().all()
    return [_conv_to_out(c) for c in convs]


@router.patch("/chat/conversations/{conv_id}", response_model=ConversationOut)
async def update_conversation(
    conv_id: uuid.UUID,
    body: ConversationUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> ConversationOut:
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        conv.title = body.title
    if body.model is not None:
        conv.model = body.model
    if body.enabled_apis is not None:
        conv.enabled_apis = json.dumps(body.enabled_apis)
    conv.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(conv)
    return _conv_to_out(conv)


@router.delete("/chat/conversations/{conv_id}")
async def delete_conversation(conv_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> Response:
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await session.delete(conv)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@router.post("/chat/conversations/{conv_id}/send", response_model=AgentResponse)
async def conversation_send(
    conv_id: uuid.UUID,
    body: ChatSend,
    session: AsyncSession = Depends(get_async_session),
) -> AgentResponse:
    """Send a user message to a conversation and run the agent loop."""
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # If awaiting approval, resolve or supersede pending tool messages
    if conv.status == "awaiting_approval":
        result2 = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id, ChatMessage.tool_status == "pending_approval")
        )
        for pm in result2.scalars().all():
            if pm.approval_id:
                appr_result = await session.execute(
                    select(PendingApproval).where(PendingApproval.id == pm.approval_id)
                )
                appr = appr_result.scalar_one_or_none()
                if appr and appr.status != "pending":
                    if appr.status in ("approved", "executed"):
                        pm.tool_status = "approved"
                        pm.content = appr.response_body or '{"ok": true}'
                    elif appr.status == "rejected":
                        pm.tool_status = "rejected"
                        pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
                    else:
                        pm.tool_status = "error"
                        pm.content = appr.response_body or '{"error": "Execution failed"}'
                else:
                    pm.tool_status = "superseded"
                    pm.content = json.dumps({"superseded": True, "message": "User continued the conversation."})
            else:
                pm.tool_status = "superseded"
                pm.content = json.dumps({"superseded": True, "message": "User continued the conversation."})
        conv.status = "idle"

    # Update model/apis if provided
    if body.model:
        conv.model = body.model
    if body.enabled_apis is not None:
        conv.enabled_apis = json.dumps(body.enabled_apis)

    # Save user message
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=body.message,
    )
    session.add(user_msg)
    await session.flush()

    # Auto-title: use first ~50 chars of first user message
    if conv.title == "New Chat":
        title = body.message[:50].strip()
        if len(body.message) > 50:
            title += "..."
        conv.title = title

    conv.updated_at = datetime.utcnow()
    await session.commit()

    return await run_agent_loop(conv, session)


@router.post("/chat/conversations/{conv_id}/continue", response_model=AgentResponse)
async def conversation_continue(
    conv_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> AgentResponse:
    """Resume agent loop after all approvals in a batch have been resolved."""
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find ALL pending_approval tool messages
    msg_result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id, ChatMessage.tool_status == "pending_approval")
    )
    pending_msgs = list(msg_result.scalars().all())
    if not pending_msgs:
        raise HTTPException(status_code=400, detail="No pending approval found")

    # Look up all linked approvals
    approval_ids = [pm.approval_id for pm in pending_msgs if pm.approval_id]
    approvals_by_id: dict[Any, Any] = {}
    if approval_ids:
        appr_result = await session.execute(
            select(PendingApproval).where(PendingApproval.id.in_(approval_ids))
        )
        approvals_by_id = {a.id: a for a in appr_result.scalars().all()}

    # Find the newest batch_id from ALL tool messages (not just pending ones)
    newest_result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conv.id,
            ChatMessage.role == "tool",
            ChatMessage.approval_id.isnot(None),
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    newest_tool_msg = newest_result.scalar_one_or_none()
    current_batch = None
    if newest_tool_msg and newest_tool_msg.approval_id:
        newest_appr = approvals_by_id.get(newest_tool_msg.approval_id)
        if not newest_appr:
            appr_r = await session.execute(
                select(PendingApproval).where(PendingApproval.id == newest_tool_msg.approval_id)
            )
            newest_appr = appr_r.scalar_one_or_none()
        if newest_appr and newest_appr.batch_id:
            current_batch = newest_appr.batch_id

    still_pending = 0
    for pm in pending_msgs:
        appr = approvals_by_id.get(pm.approval_id)
        if not appr:
            continue

        # Supersede stale approvals from older batches
        if current_batch and appr.batch_id != current_batch:
            if appr.status == "pending":
                pm.tool_status = "superseded"
                pm.content = json.dumps({"superseded": True, "message": "Superseded by a newer batch."})
            elif appr.status in ("approved", "executed"):
                pm.tool_status = "approved"
                pm.content = appr.response_body or '{"ok": true}'
            elif appr.status == "rejected":
                pm.tool_status = "rejected"
                pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
            else:
                pm.tool_status = "error"
                pm.content = appr.response_body or '{"error": "Execution failed"}'
            continue

        if appr.status == "pending":
            still_pending += 1
            continue

        if appr.status in ("approved", "executed"):
            pm.tool_status = "approved"
            pm.content = appr.response_body or '{"ok": true}'
        elif appr.status == "rejected":
            pm.tool_status = "rejected"
            pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
        elif appr.status == "failed":
            pm.tool_status = "error"
            pm.content = appr.response_body or '{"error": "Execution failed"}'
        else:
            pm.tool_status = "error"
            pm.content = json.dumps({"error": f"Unknown approval status: {appr.status}"})

    if still_pending > 0:
        await session.commit()
        raise HTTPException(status_code=400, detail=f"{still_pending} approval(s) still pending")

    conv.status = "running"
    conv.updated_at = datetime.utcnow()
    await session.commit()

    return await run_agent_loop(conv, session)


# ---------------------------------------------------------------------------
# Streaming agent endpoints (SSE)
# ---------------------------------------------------------------------------

async def _prepare_send(conv_id: uuid.UUID, body: ChatSend, session: AsyncSession) -> ChatConversation:
    """Shared setup for send and send-stream: validate, save user message, return conv."""
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # If awaiting approval, resolve or supersede all pending tool messages
    # so the user can continue chatting (orphaned tool_call_ids would break the LLM API)
    if conv.status == "awaiting_approval":
        msg_result2 = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id, ChatMessage.tool_status == "pending_approval")
        )
        pending_msgs = list(msg_result2.scalars().all())
        for pm in pending_msgs:
            if pm.approval_id:
                appr_result = await session.execute(
                    select(PendingApproval).where(PendingApproval.id == pm.approval_id)
                )
                appr = appr_result.scalar_one_or_none()
                if appr and appr.status != "pending":
                    # Already resolved — apply result
                    if appr.status in ("approved", "executed"):
                        pm.tool_status = "approved"
                        pm.content = appr.response_body or '{"ok": true}'
                    elif appr.status == "rejected":
                        pm.tool_status = "rejected"
                        pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
                    else:
                        pm.tool_status = "error"
                        pm.content = appr.response_body or '{"error": "Execution failed"}'
                else:
                    # Still pending — supersede
                    pm.tool_status = "superseded"
                    pm.content = json.dumps({"superseded": True, "message": "User continued the conversation."})
            else:
                pm.tool_status = "superseded"
                pm.content = json.dumps({"superseded": True, "message": "User continued the conversation."})
        conv.status = "idle"

    if body.model:
        conv.model = body.model
    if body.enabled_apis is not None:
        conv.enabled_apis = json.dumps(body.enabled_apis)
    user_msg = ChatMessage(conversation_id=conv.id, role="user", content=body.message)
    session.add(user_msg)
    await session.flush()
    if conv.title == "New Chat":
        title = body.message[:50].strip()
        if len(body.message) > 50:
            title += "..."
        conv.title = title
    conv.updated_at = datetime.utcnow()
    await session.commit()
    return conv


async def _prepare_continue(conv_id: uuid.UUID, session: AsyncSession) -> ChatConversation:
    """Shared setup for continue and continue-stream: resolve all batch approvals, return conv."""
    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find ALL pending_approval tool messages for this conversation
    msg_result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id, ChatMessage.tool_status == "pending_approval")
    )
    pending_msgs = list(msg_result.scalars().all())
    if not pending_msgs:
        raise HTTPException(status_code=400, detail="No pending approval found")

    # Look up all linked approvals in one pass
    approval_ids = [pm.approval_id for pm in pending_msgs if pm.approval_id]
    approvals_by_id: dict[Any, Any] = {}
    if approval_ids:
        appr_result = await session.execute(
            select(PendingApproval).where(PendingApproval.id.in_(approval_ids))
        )
        approvals_by_id = {a.id: a for a in appr_result.scalars().all()}

    # Find the newest batch_id in this conversation by checking ALL tool messages
    # (not just pending_approval ones — the current batch may already be resolved)
    newest_result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conv.id,
            ChatMessage.role == "tool",
            ChatMessage.approval_id.isnot(None),
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    newest_tool_msg = newest_result.scalar_one_or_none()
    current_batch = None
    if newest_tool_msg and newest_tool_msg.approval_id:
        newest_appr = approvals_by_id.get(newest_tool_msg.approval_id)
        if not newest_appr:
            appr_r = await session.execute(
                select(PendingApproval).where(PendingApproval.id == newest_tool_msg.approval_id)
            )
            newest_appr = appr_r.scalar_one_or_none()
        if newest_appr and newest_appr.batch_id:
            current_batch = newest_appr.batch_id

    still_pending = 0
    for pm in pending_msgs:
        appr = approvals_by_id.get(pm.approval_id)
        if not appr:
            continue

        # Supersede stale pending approvals from older batches
        if current_batch and appr.batch_id != current_batch:
            if appr.status == "pending":
                pm.tool_status = "superseded"
                pm.content = json.dumps({"superseded": True, "message": "Superseded by a newer batch."})
            elif appr.status in ("approved", "executed"):
                pm.tool_status = "approved"
                pm.content = appr.response_body or '{"ok": true}'
            elif appr.status == "rejected":
                pm.tool_status = "rejected"
                pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
            else:
                pm.tool_status = "error"
                pm.content = appr.response_body or '{"error": "Execution failed"}'
            continue

        if appr.status == "pending":
            still_pending += 1
            continue

        # Resolve the ChatMessage based on approval result
        if appr.status in ("approved", "executed"):
            pm.tool_status = "approved"
            pm.content = appr.response_body or '{"ok": true}'
        elif appr.status == "rejected":
            pm.tool_status = "rejected"
            pm.content = json.dumps({"rejected": True, "message": "The user rejected this action."})
        elif appr.status == "failed":
            pm.tool_status = "error"
            pm.content = appr.response_body or '{"error": "Execution failed"}'
        else:
            pm.tool_status = "error"
            pm.content = json.dumps({"error": f"Unknown approval status: {appr.status}"})

    if still_pending > 0:
        await session.commit()  # Save partial progress
        raise HTTPException(status_code=400, detail=f"{still_pending} approval(s) still pending")

    conv.status = "running"
    conv.updated_at = datetime.utcnow()
    await session.commit()
    return conv


def _start_background_stream(conv_id: str) -> StreamState:
    """Create a StreamState, register it, and spawn the background task."""
    state = StreamState()
    _active_streams[str(conv_id)] = state
    asyncio.create_task(_run_stream_background(str(conv_id), state))
    return state


@router.post("/chat/conversations/{conv_id}/send-stream")
async def conversation_send_stream(
    conv_id: uuid.UUID,
    body: ChatSend,
    session: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    """Send a user message and stream the agent loop as SSE events."""
    cid = str(conv_id)
    existing = _active_streams.get(cid)
    if existing and not existing.done:
        raise HTTPException(status_code=409, detail="Stream already running for this conversation")

    conv = await _prepare_send(conv_id, body, session)
    state = _start_background_stream(cid)
    return StreamingResponse(
        _consume_stream(state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/conversations/{conv_id}/continue-stream")
async def conversation_continue_stream(
    conv_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    """Resume after approval and stream as SSE events."""
    cid = str(conv_id)
    existing = _active_streams.get(cid)
    if existing and not existing.done:
        raise HTTPException(status_code=409, detail="Stream already running for this conversation")

    conv = await _prepare_continue(conv_id, session)
    state = _start_background_stream(cid)
    return StreamingResponse(
        _consume_stream(state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/conversations/{conv_id}/retry-stream")
async def conversation_retry_stream(
    conv_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    """Re-run the agent loop or re-attach to a running stream."""
    cid = str(conv_id)
    # If a stream is already running, attach to it
    existing = _active_streams.get(cid)
    if existing and not existing.done:
        return StreamingResponse(
            _consume_stream(existing, start_offset=len(existing.events)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = await session.execute(select(ChatConversation).where(ChatConversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    state = _start_background_stream(cid)
    return StreamingResponse(
        _consume_stream(state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/conversations/{conv_id}/attach-stream")
async def conversation_attach_stream(
    conv_id: uuid.UUID,
) -> StreamingResponse:
    """Attach to a running background stream, or return done if idle."""
    cid = str(conv_id)
    existing = _active_streams.get(cid)
    if existing:
        return StreamingResponse(
            _consume_stream(existing, start_offset=len(existing.events)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # No active stream — return a single done event
    async def _idle_stream():
        yield _sse_event("done", {"status": "idle", "reconnected": True})

    return StreamingResponse(
        _idle_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
