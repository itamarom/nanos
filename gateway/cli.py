"""CLI for managing nanos, credentials, and testing APIs."""
import json
import os
import sys
import secrets

import click
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.database import SyncSessionLocal
from shared.models import Nano, NanoApiKey, NanoPermission, ApiCredential, RunLog


def get_session():
    return SyncSessionLocal()


@click.group()
def cli():
    """Nanos Framework CLI — manage nanos, credentials, and test APIs."""
    pass


# --- Credential management ---

GOOGLE_OAUTH2_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _google_oauth2_flow(api_name, client_id, client_secret, refresh_token):
    """Run OAuth2 authorization code flow for Google APIs.

    Returns a credential dict with type="oauth2".
    """
    import requests
    from urllib.parse import urlencode, urlparse, parse_qs

    if not client_id:
        client_id = click.prompt("OAuth2 Client ID")
    if not client_secret:
        client_secret = click.prompt("OAuth2 Client Secret")

    if refresh_token:
        click.echo(f"Using provided refresh token for '{api_name}'.")
    else:
        # Build authorization URL with combined scopes for both services
        auth_params = urlencode({
            "client_id": client_id,
            "redirect_uri": "http://localhost",
            "response_type": "code",
            "scope": " ".join(GOOGLE_OAUTH2_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        })
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{auth_params}"

        click.echo("\nOpen this URL in your browser to authorize:\n")
        click.echo(auth_url)
        click.echo("\nAfter authorizing, you'll be redirected to http://localhost/?code=...")
        click.echo("Copy the full redirect URL or just the 'code' parameter value.\n")

        code_input = click.prompt("Paste the authorization code (or full redirect URL)")

        # Extract code if user pasted the full redirect URL
        if code_input.startswith("http"):
            parsed = urlparse(code_input)
            code_params = parse_qs(parsed.query)
            if "code" not in code_params:
                raise click.ClickException("Could not find 'code' parameter in URL")
            code_input = code_params["code"][0]

        # Exchange authorization code for tokens
        token_resp = requests.post(
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
            raise click.ClickException(
                f"Token exchange failed: {token_data['error']} — {token_data.get('error_description', '')}"
            )

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise click.ClickException("No refresh_token in response. Re-run with prompt=consent.")

        click.echo(f"\nRefresh token obtained successfully.")

        # Show ready-to-paste command for the other Google service
        other = "gmail" if api_name == "google-calendar" else "google-calendar"
        click.echo(f"\nTo configure '{other}' with the same token, run:")
        click.echo(
            f"  python cli.py add-credential {other} "
            f"--client-id '{client_id}' --client-secret '{client_secret}' "
            f"--refresh-token '{refresh_token}'"
        )

    return {
        "type": "oauth2",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }


@cli.command("add-credential")
@click.argument("api_name")
@click.option("--key", help="API key (for OpenAI)")
@click.option("--token", help="Bot token (for Telegram, Slack)")
@click.option("--chat-id", help="Chat ID (for Telegram)")
@click.option("--json", "json_str", help="Service account JSON string (for Google, legacy)")
@click.option("--delegated-user", help="Delegated user email (for Google service account)")
@click.option("--bot-token", help="Bot token (for Slack/Slackbot)")
@click.option("--app-token", help="App-level token (for Slackbot Socket Mode)")
@click.option("--channel-id", help="Channel ID (for Slackbot)")
@click.option("--client-id", help="OAuth2 client ID (for Google)")
@click.option("--client-secret", help="OAuth2 client secret (for Google)")
@click.option("--refresh-token", help="OAuth2 refresh token (for Google, skip interactive flow)")
def add_credential(api_name, key, token, chat_id, json_str, delegated_user, bot_token,
                   app_token, channel_id, client_id, client_secret, refresh_token):
    """Add or update API credentials."""
    session = get_session()
    try:
        cred_data = {}

        if api_name == "openai":
            if not key:
                key = click.prompt("OpenAI API key")
            cred_data = {"api_key": key}

        elif api_name == "telegram":
            if not token:
                token = click.prompt("Telegram bot token")
            if not chat_id:
                chat_id = click.prompt("Telegram chat ID")
            cred_data = {"token": token, "chat_id": chat_id}

        elif api_name in ("google-calendar", "gmail"):
            if client_id or client_secret:
                # OAuth2 client credentials flow
                cred_data = _google_oauth2_flow(api_name, client_id, client_secret, refresh_token)
            elif json_str or not click.confirm(
                "Use OAuth2 client credentials? (No = legacy service account)", default=True
            ):
                # Legacy service account path
                if not json_str:
                    json_str = click.prompt("Service account JSON")
                sa_json = json.loads(json_str)
                if not delegated_user:
                    delegated_user = click.prompt("Delegated user email")
                cred_data = {**sa_json, "delegated_user": delegated_user}
            else:
                # Interactive OAuth2 (no flags provided, user chose OAuth2)
                cred_data = _google_oauth2_flow(api_name, None, None, None)

        elif api_name == "slack":
            webhook_url = token or bot_token  # accept via --token for convenience
            if not webhook_url:
                webhook_url = click.prompt("Slack webhook URL")
            cred_data = {"webhook_url": webhook_url}

        elif api_name == "slackbot":
            if not bot_token:
                bot_token = click.prompt("Slack Bot User OAuth Token (xoxb-...)")
            if not app_token:
                app_token = click.prompt("Slack App-Level Token (xapp-...)")
            if not channel_id:
                channel_id = click.prompt("Slack Channel ID (e.g. C07...)")
            cred_data = {"bot_token": bot_token, "app_token": app_token, "channel_id": channel_id}

        elif api_name == "hubspot":
            access_token = key or token
            if not access_token:
                access_token = click.prompt("HubSpot private app access token")
            cred_data = {"access_token": access_token}

        elif api_name == "notion":
            api_token = key or token
            if not api_token:
                api_token = click.prompt("Notion integration token (ntn_...)")
            cred_data = {"api_token": api_token}

        elif api_name == "linear":
            api_key = key or token
            if not api_key:
                api_key = click.prompt("Linear API key (lin_api_...)")
            cred_data = {"api_key": api_key}

        elif api_name == "whatsapp":
            # wacli auth is handled interactively via `docker exec`.
            # Store phone number metadata for reference only.
            phone = key or click.prompt("WhatsApp phone number (e.g. +1234567890)")
            cred_data = {"phone_number": phone, "auth_method": "wacli"}

        else:
            # Generic: accept raw JSON
            raw = click.prompt("Credentials JSON")
            cred_data = json.loads(raw)

        # Encrypt with master key
        master_key = click.prompt("Master key", hide_input=True)
        from gateway.crypto import encrypt_with_passphrase
        encrypted = encrypt_with_passphrase(json.dumps(cred_data), master_key)

        from sqlalchemy import select
        result = session.execute(select(ApiCredential).where(ApiCredential.api_name == api_name))
        existing = result.scalar_one_or_none()

        if existing:
            existing.credentials = encrypted
            click.echo(f"Updated credentials for '{api_name}'.")
        else:
            session.add(ApiCredential(api_name=api_name, credentials=encrypted))
            click.echo(f"Added credentials for '{api_name}'.")

        session.commit()
    except Exception as e:
        session.rollback()
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        session.close()


@cli.command("list-credentials")
def list_credentials():
    """List stored API credentials (names only, never full values)."""
    session = get_session()
    try:
        from sqlalchemy import select
        result = session.execute(select(ApiCredential).order_by(ApiCredential.api_name))
        creds = result.scalars().all()
        if not creds:
            click.echo("No credentials stored.")
            return
        for cred in creds:
            # Don't try to decrypt — just show the name and whether it's encrypted
            is_encrypted = not cred.credentials.startswith("{")
            status = "encrypted" if is_encrypted else "plaintext"
            click.echo(f"  {cred.api_name}  [{status}]  [updated: {cred.updated_at}]")
    finally:
        session.close()


# --- API testing ---

@cli.command("test")
@click.argument("api_name")
def test_api(api_name):
    """Test API connectivity (openai, calendar, gmail, slack, all)."""
    import asyncio

    async def run_test():
        from shared.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            if api_name == "all":
                from gateway.services import openai_service, google_calendar_service, gmail_service, slack_service, hubspot_service, whatsapp_service, notion_service, linear_service
                for name, svc in [
                    ("openai", openai_service),
                    ("calendar", google_calendar_service),
                    ("gmail", gmail_service),
                    ("slack", slack_service),
                    ("hubspot", hubspot_service),
                    ("whatsapp", whatsapp_service),
                    ("notion", notion_service),
                    ("linear", linear_service),
                ]:
                    click.echo(f"\nTesting {name}...")
                    results = await svc.test_all(session)
                    for r in results:
                        status = "✓" if r.get("success") else "✗"
                        click.echo(f"  {status} {r.get('test', 'unknown')}: {r.get('detail', '')}")
            else:
                service_map = {
                    "openai": "gateway.services.openai_service",
                    "calendar": "gateway.services.google_calendar_service",
                    "gmail": "gateway.services.gmail_service",
                    "slack": "gateway.services.slack_service",
                    "hubspot": "gateway.services.hubspot_service",
                    "whatsapp": "gateway.services.whatsapp_service",
                    "notion": "gateway.services.notion_service",
                    "linear": "gateway.services.linear_service",
                }
                module_path = service_map.get(api_name)
                if not module_path:
                    click.echo(f"Unknown API: {api_name}")
                    return
                import importlib
                svc = importlib.import_module(module_path)
                click.echo(f"Testing {api_name}...")
                results = await svc.test_all(session)
                for r in results:
                    status = "✓" if r.get("success") else "✗"
                    click.echo(f"  {status} {r.get('test', 'unknown')}: {r.get('detail', '')}")

    asyncio.run(run_test())


# --- Nano management ---

@cli.command("list-types")
def list_types_cmd():
    """List all nano types discovered from /nanos/*/config.yaml."""
    from shared.nano_types import list_types
    types = list_types()
    if not types:
        click.echo("No types found in /nanos/.")
        return
    for t in types:
        perms = ", ".join(t.get("permissions", []))
        schedule = t.get("schedule", "manual")
        click.echo(f"  {t['type_name']}: {t.get('description', '')[:60]}")
        click.echo(f"    schedule={schedule} perms=[{perms}]")
        if t.get("parameter_schema"):
            click.echo(f"    parameters: {list(t['parameter_schema'].keys())}")


@cli.command("create-nano")
@click.option("--name", required=True, help="Nano name")
@click.option("--type", "type_name", help="Nano type (directory name in /nanos/)")
@click.option("--config", "config_path", help="Path to config.yaml")
@click.option("--script-path", help="Script path (relative to nanos mount)")
@click.option("--schedule", help="Cron schedule")
@click.option("--description", default="", help="Description")
@click.option("--parameters", "params_json", help="JSON parameters string")
def create_nano(name, type_name, config_path, script_path, schedule, description, params_json):
    """Register a nano instance and generate an API key."""
    session = get_session()
    try:
        permissions = []

        if config_path:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            name = config.get("name", name)
            description = config.get("description", description)
            schedule = config.get("schedule", schedule)
            permissions = config.get("permissions", [])
            if not type_name:
                type_name = os.path.basename(os.path.dirname(os.path.abspath(config_path)))
            if not script_path:
                script_path = f"{type_name}/nano.py"

        if type_name and not script_path:
            script_path = f"{type_name}/nano.py"

        if not type_name:
            type_name = script_path.split("/")[0] if script_path and "/" in script_path else name

        if not script_path:
            script_path = f"{name}/nano.py"

        # Parse parameters JSON if provided
        parameters = None
        if params_json:
            parameters = json.dumps(json.loads(params_json))

        import uuid
        nano = Nano(
            id=uuid.uuid4(),
            name=name,
            description=description,
            script_path=script_path,
            schedule=schedule,
            is_active=True,
            type_name=type_name,
            parameters=parameters,
        )
        session.add(nano)

        api_key = "nk_" + secrets.token_hex(16)
        session.add(NanoApiKey(nano_id=nano.id, key=api_key))

        for perm in permissions:
            session.add(NanoPermission(nano_id=nano.id, endpoint=perm))

        session.commit()
        click.echo(f"Nano '{name}' registered successfully.")
        click.echo(f"  ID:          {nano.id}")
        click.echo(f"  Type:        {type_name}")
        click.echo(f"  API Key:     {api_key}")
        click.echo(f"  Script:      {script_path}")
        click.echo(f"  Permissions: {', '.join(permissions) or 'none'}")
        if schedule:
            click.echo(f"  Schedule:    {schedule}")
        if parameters:
            click.echo(f"  Parameters:  {parameters}")
    except Exception as e:
        session.rollback()
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    finally:
        session.close()


@cli.command("list-nanos")
def list_nanos():
    """List all registered nanos."""
    session = get_session()
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = session.execute(
            select(Nano).options(selectinload(Nano.permissions)).order_by(Nano.name)
        )
        nanos = result.scalars().all()
        if not nanos:
            click.echo("No nanos registered.")
            return
        for n in nanos:
            status = "active" if n.is_active else "inactive"
            perms = ", ".join(p.endpoint for p in n.permissions)
            schedule = n.schedule or "manual"
            click.echo(f"  {n.name} [{status}] schedule={schedule} perms=[{perms}]")
    finally:
        session.close()


@cli.command("run-nano")
@click.argument("name")
def run_nano(name):
    """Manually trigger a nano execution."""
    import httpx

    gateway_url = os.environ.get("NANO_GATEWAY_URL", "http://localhost:8000")
    admin_key = os.environ.get("ADMIN_API_KEY", "admin_change_me")

    session = get_session()
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = session.execute(
            select(Nano).options(selectinload(Nano.api_keys)).where(Nano.name == name)
        )
        nano = result.scalar_one_or_none()
        if not nano:
            click.echo(f"Nano '{name}' not found.")
            sys.exit(1)

        # Trigger via Celery if available, otherwise just report
        try:
            from celery import Celery
            celery_app = Celery("nanos", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
            task = celery_app.send_task("tasks.run_nano_task", args=[str(nano.id), "manual"])
            click.echo(f"Triggered nano '{name}' — task ID: {task.id}")
        except Exception:
            click.echo(f"Celery not available. Nano '{name}' (ID: {nano.id}) would run: {nano.script_path}")
    finally:
        session.close()


if __name__ == "__main__":
    cli()
