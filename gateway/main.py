from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sqlalchemy import update as sa_update
from sqlalchemy.engine import Inspector

from shared.database import async_engine, AsyncSessionLocal
from shared.models import Base, ChatConversation
from gateway.routers import health, admin, openai, google_calendar, gmail, slack, state, hubspot, whatsapp, notion, linear, chat_admin

logger = logging.getLogger(__name__)


def _check_sdk_coverage(app: FastAPI) -> None:
    """Log warnings for gateway API routes missing from the NanosClient SDK."""
    try:
        import sys, inspect
        sys.path.insert(0, "/app/sdk")
        from nanos_sdk._base import NanosClient as BaseClient
        from nanos_sdk.client import NanosClient as TypedClient

        sdk_methods = {
            name for name in dir(TypedClient)
            if not name.startswith("_") and callable(getattr(TypedClient, name))
        } | {
            name for name in dir(BaseClient)
            if not name.startswith("_") and callable(getattr(BaseClient, name))
        }

        # Collect nano-facing API routes (those requiring X-Nano-Key)
        skip_prefixes = ("/api/admin", "/api/test", "/api/health", "/api/nano-key",
                         "/api/whatsapp/auth", "/api/whatsapp/sync")
        missing = []
        for route in app.routes:
            path = getattr(route, "path", "")
            name = getattr(route, "name", "")
            if not path.startswith("/api/") or any(path.startswith(p) for p in skip_prefixes):
                continue
            # Derive expected SDK method name from route name: "gmail.messages.list" -> "gmail_messages_list"
            if not name or "." not in name:
                continue
            expected_method = name.replace(".", "_")
            if expected_method not in sdk_methods:
                missing.append(f"  {name} -> expected client.{expected_method}()")

        if missing:
            logger.warning(
                "SDK coverage gap — %d gateway route(s) have no corresponding SDK method.\n"
                "Run `python sdk/generate.py` to regenerate.\n%s",
                len(missing), "\n".join(missing),
            )
    except Exception:
        logger.debug("SDK coverage check skipped (import error)", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create tables if they don't exist (for dev; production uses alembic)
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Reset stale "running" conversations left over from previous shutdown
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_update(ChatConversation)
            .where(ChatConversation.status == "running")
            .values(status="idle")
        )
        await session.commit()

    # Check for schema drift: model columns missing from actual DB tables
    async with async_engine.connect() as conn:
        def _check_schema_drift(connection: object) -> list[str]:
            inspector = Inspector.from_engine(connection)  # type: ignore[arg-type]
            missing = []
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                db_cols = {c["name"] for c in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name not in db_cols:
                        missing.append(f"  {table.name}.{col.name}")
            return missing
        drift = await conn.run_sync(_check_schema_drift)
        if drift:
            logger.error(
                "SCHEMA DRIFT: %d model column(s) missing from DB. "
                "Run `alembic upgrade head` to apply migrations.\n%s",
                len(drift), "\n".join(drift),
            )

    _check_sdk_coverage(app)

    # Periodic cleanup of done StreamState objects from the in-memory registry
    async def _stream_cleanup_loop():
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            try:
                done_ids = [
                    cid for cid, state in chat_admin._active_streams.items()
                    if state.done
                ]
                for cid in done_ids:
                    chat_admin._active_streams.pop(cid, None)
                if done_ids:
                    logger.debug("Cleaned up %d done stream states", len(done_ids))
            except Exception:
                logger.debug("Stream cleanup error", exc_info=True)

    cleanup_task = asyncio.create_task(_stream_cleanup_loop())

    # Start Slack Bot (Socket Mode) for approval handling
    from gateway.services.slackbot_service import start_slackbot
    slackbot_task = asyncio.create_task(start_slackbot(AsyncSessionLocal))

    # Start periodic WhatsApp sync to keep messages up-to-date
    from gateway.services.whatsapp_service import run_periodic_sync
    wa_sync_task = asyncio.create_task(run_periodic_sync())

    yield

    cleanup_task.cancel()
    wa_sync_task.cancel()

    # Shutdown: cancel slackbot task and close socket client
    slackbot_task.cancel()
    from gateway.services import slackbot_service
    if slackbot_service._socket_client:
        try:
            await slackbot_service._socket_client.close()
        except Exception:
            pass
    await async_engine.dispose()


app = FastAPI(
    title="Nanos API Gateway",
    description="Secure API gateway for nano-agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(admin.lock_router, prefix="/api/admin", tags=["lock"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(openai.router, prefix="/api/openai", tags=["openai"])
app.include_router(google_calendar.router, prefix="/api/calendar", tags=["calendar"])
app.include_router(gmail.router, prefix="/api/gmail", tags=["gmail"])
app.include_router(slack.router, prefix="/api/slack", tags=["slack"])
app.include_router(hubspot.router, prefix="/api/hubspot", tags=["hubspot"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["whatsapp"])
app.include_router(notion.router, prefix="/api/notion", tags=["notion"])
app.include_router(linear.router, prefix="/api/linear", tags=["linear"])
app.include_router(state.router, prefix="/api", tags=["state"])
app.include_router(chat_admin.router, prefix="/api/admin", tags=["chat"])
