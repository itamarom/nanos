# Nanos Framework

Secure gateway + runtime for nano-agents — small Python automation scripts that access external APIs through a controlled proxy.

## Architecture

```
                    ┌──────────────┐
                    │  Slack Bot   │
                    │  (approvals) │
                    └──────┬───────┘
                           │
┌──────────┐    ┌──────────┴───────┐    ┌──────────────┐
│  Nanos   │───>│   API Gateway    │───>│ External APIs │
│ (worker) │    │   (FastAPI)      │    │ OpenAI, Gmail │
└──────────┘    │   Port 8000      │    │ Calendar,Slack│
                └──────────────────┘    └──────────────┘
┌──────────┐    ┌──────────────────┐
│  Celery  │    │    Dashboard     │
│  Beat    │    │   (FastAPI)      │
└──────────┘    │   Port 8001      │
                └──────────────────┘
┌──────────┐    ┌──────────────────┐
│  Redis   │    │   PostgreSQL 16  │
│  6379    │    │      5432        │
└──────────┘    └──────────────────┘
```

6 Docker containers via `docker-compose.yml`:
- **api-gateway** (FastAPI, port 8000) — API proxy, auth, approval flow
- **dashboard** (FastAPI + Jinja2, port 8001) — monitoring UI
- **worker** (Celery) — runs nano scripts in sandboxed Docker containers
- **beat** (Celery beat) — triggers scheduled nanos
- **redis** — Celery broker
- **db** — PostgreSQL 16

## Quick Start

```bash
cp .env.example .env          # Edit DB_PASSWORD and ADMIN_API_KEY
docker compose up -d
docker compose exec api-gateway python cli.py add-credential openai --key sk-...
docker compose exec api-gateway python cli.py test openai
```

## Key Files

| Path | Purpose |
|------|---------|
| `shared/models.py` | SQLAlchemy ORM models (6 tables) |
| `shared/database.py` | Engine + session factory |
| `shared/config.py` | Shared settings from env vars |
| `gateway/main.py` | FastAPI app entry point |
| `gateway/auth.py` | X-Nano-Key and X-Admin-Key validation |
| `gateway/config.py` | SENSITIVE_ENDPOINTS set |
| `gateway/schemas.py` | All Pydantic request/response models |
| `gateway/cli.py` | Click CLI for management |
| `gateway/routers/` | API route handlers |
| `gateway/services/` | External API integrations |
| `worker/tasks.py` | Celery task definitions |
| `worker/runner.py` | Subprocess nano execution |
| `sdk/nanos_sdk/_base.py` | Hand-written SDK client |
| `sdk/generate.py` | Auto-generate typed client from OpenAPI |
| `alembic/` | Database migrations |

## Database Schema

6 tables: `nanos`, `nano_api_keys`, `nano_permissions`, `api_credentials`, `pending_approvals`, `run_logs`

## How to Add a New API

**Every new API must be registered in ALL of these locations.** Missing any one of them causes bugs (broken tests, missing UI entries, permission errors). Use this as a strict checklist.

### Gateway (backend)

| # | File | What to add |
|---|------|-------------|
| 1 | `gateway/services/{name}_service.py` | **Create** service module. Must include `async def test_all(session) -> list[ServiceTestEntry]`. Use `from gateway.crypto import decrypt_json` for credentials. |
| 2 | `gateway/routers/{name}.py` | **Create** FastAPI router. Endpoints named `"{name}.{operation}"`. Call `check_permission(nano, "{name}.operation")`. |
| 3 | `gateway/schemas.py` | Add Pydantic request/response models for the API. |
| 4 | `gateway/main.py` | **Import** router and **register** with `app.include_router({name}.router, prefix="/api/{name}", tags=["{name}"])`. |
| 5 | `gateway/routers/health.py` | Add service to **both** the import line AND the `service_map` dict in `test_api()`. This powers `/api/test/{name}`. |
| 6 | `gateway/cli.py` | Add credential prompts in `add-credential` command. Add to **both** the import AND `service_map` in the `test` command. |
| 7 | `shared/config.py` → `ALL_PERMISSIONS` | Add **every** endpoint permission string (e.g. `"{name}.items.list"`). |
| 8 | `shared/config.py` → `SENSITIVE_ENDPOINTS` | Add write operations (create/update/delete/send) that require approval. |

### Dashboard (frontend)

| # | File | What to add |
|---|------|-------------|
| 9 | `dashboard/routers/pages.py` → `API_CATALOG` | Add catalog entry with `name`, `label`, `test_name`, `endpoints`, `fields` (credential form), and `help` (setup instructions HTML). |
| 10 | `dashboard/templates/chat.html` | Add `chat-api-group` block with checkboxes for all endpoints. Mark sensitive ones with `class="sensitive"`. |

### Chat integration

| # | File | What to add |
|---|------|-------------|
| 11 | `gateway/routers/chat_admin.py` | Add tool dispatch handlers — the `if tool_name == "{name}_{operation}":` blocks that import and call service functions. |
| 12 | `nanos/<your-nanos>/nano-harness/tools.py` → `_API_GUIDELINES` | **Add or update API guidelines** that teach the LLM how to use the API correctly — which tools to use for what, correct workflows, parameter formats, and common pitfalls. This is CRITICAL — without good guidelines the chat agent will misuse the tools. |

### Post-registration

| # | Step |
|---|------|
| 13 | Rebuild: `sudo docker compose up -d --build api-gateway dashboard` |
| 14 | Test: `docker compose exec api-gateway python cli.py test {name}` |
| 15 | Regenerate SDK: `python sdk/generate.py` (if nanos need typed client access) |

### Common mistakes (learned the hard way)
- **Forgetting `health.py` service_map** → "Unknown API: {name}" when saving credentials from dashboard (dashboard runs test after save)
- **Forgetting `API_CATALOG` in pages.py** → API doesn't appear on the APIs page at all
- **Forgetting `chat.html` toggles** → API permissions missing from chat settings
- **Using `json.loads(cred.credentials)` instead of `decrypt_json(cred.credentials)`** → crashes when credentials are encrypted
- **Forgetting `_API_GUIDELINES` in tools.py** → chat agent misuses the API (wrong tools for the job, bad parameter formats, inefficient workflows)

## How to Register a Nano

```bash
docker compose exec api-gateway python cli.py create-nano --name my-nano --config /nanos/my-nano/config.yaml
```

## How to Add Credentials

**Credentials must NEVER be stored in files. Always pass them as direct values (strings/tokens) to the CLI. They are stored only in the PostgreSQL `api_credentials` table.**

### Google APIs (OAuth2 — recommended)

```bash
# Step 1: Add google-calendar with interactive OAuth2 flow
docker compose exec api-gateway python cli.py add-credential google-calendar \
  --client-id CLIENT_ID --client-secret CLIENT_SECRET
# Opens a URL to authorize; paste the code back. Prints a ready-to-paste command for gmail.

# Step 2: Add gmail reusing the same refresh token (no browser needed)
docker compose exec api-gateway python cli.py add-credential gmail \
  --client-id CLIENT_ID --client-secret CLIENT_SECRET --refresh-token TOKEN
```

### Google APIs (service account — legacy)

```bash
docker compose exec api-gateway python cli.py add-credential google-calendar --json '{"type":"service_account",...}' --delegated-user user@domain.com
docker compose exec api-gateway python cli.py add-credential gmail --json '{"type":"service_account",...}' --delegated-user user@domain.com
```

### Other APIs

```bash
docker compose exec api-gateway python cli.py add-credential openai --key sk-...
docker compose exec api-gateway python cli.py add-credential slack --token https://hooks.slack.com/services/T.../B.../...
docker compose exec api-gateway python cli.py add-credential slackbot \
  --bot-token xoxb-... --app-token xapp-... --channel-id C07...
docker compose exec api-gateway python cli.py list-credentials
```

## Type Checking (MANDATORY)

**Run mypy before every build and commit.** The project must pass with zero errors.

```bash
python3 -m mypy gateway/ shared/ worker/ sdk/
```

This is configured via `mypy.ini` with strict settings. Key rules:
- **All functions must have return type annotations** (`disallow_incomplete_defs`)
- **Avoid `Any`** — use actual types from libraries (e.g. `Resource`, `Session`, `Nano`)
- **Avoid `dict[str, Any]`** — use TypedDicts for known structures, Pydantic models for API responses
- **Use `from __future__ import annotations`** in every file for PEP 604 `X | None` syntax
- **Use `TYPE_CHECKING` guard** for imports only needed at type-check time
- Per-module overrides exist for SDK HTTP client files where `response.json()` returns `Any`

If mypy fails, fix the errors before proceeding with the build.

## How to Test

```bash
docker compose exec api-gateway python cli.py test all
docker compose exec api-gateway python cli.py test openai
docker compose exec api-gateway python cli.py test calendar
```

## Sensitive Endpoints (Require Approval)

These trigger Slack Bot approval before execution:
- `calendar.events.create`, `calendar.events.update`, `calendar.events.delete`
- `gmail.messages.send`, `gmail.messages.reply`

## Code Conventions

- **Routers**: FastAPI APIRouter, one per API, mounted in main.py
- **Services**: One module per API, loads credentials from DB, has `test_all()`
- **Schemas**: All Pydantic models in `gateway/schemas.py`
- **Auth**: X-Nano-Key for nanos, X-Admin-Key for admin endpoints
- **CLI**: Click-based, commands in `gateway/cli.py`
- **DB**: SQLAlchemy async for FastAPI, sync for Celery/CLI
- **Credentials**: Stored in PostgreSQL `api_credentials` table, NEVER in files. All secrets are passed as direct CLI arguments or env vars — never read from or written to files on disk

## Logging

Nanos use Python's `logging` module. Logs are captured to `/var/log/nanos/{nano-name}/{run-id}.log` via the shared `nano-logs` Docker volume. The dashboard reads these files directly.
