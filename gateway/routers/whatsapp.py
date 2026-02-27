"""WhatsApp API router — wraps wacli CLI for WhatsApp messaging."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission, get_run_log_id, get_draft_mode, verify_admin_key
from gateway.schemas import (
    WhatsAppSendTextRequest,
    WhatsAppSendFileRequest,
    WhatsAppListMessagesRequest,
    WhatsAppSearchRequest,
    WhatsAppBackfillRequest,
    WhatsAppMediaDownloadRequest,
)
from gateway.services import whatsapp_service
from gateway.services.whatsapp_service import SyncStatus
from gateway.services.approval_service import create_approval

router = APIRouter()


# ---------------------------------------------------------------------------
# Sync guard — blocks API usage while WhatsApp is syncing
# ---------------------------------------------------------------------------

async def check_wa_ready() -> None:
    """Raise 503 if WhatsApp is still syncing conversations."""
    state = whatsapp_service.get_sync_state()
    if state["status"] == SyncStatus.SYNCING.value:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp is syncing conversations. Please wait until sync completes.",
        )


# ---------------------------------------------------------------------------
# Admin-only auth endpoints (QR code flow)
# ---------------------------------------------------------------------------

@router.get("/auth/stream", dependencies=[Depends(verify_admin_key)])
async def auth_stream() -> StreamingResponse:
    """Start wacli auth and stream QR codes + status via SSE."""
    return StreamingResponse(
        whatsapp_service.auth_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/auth/status", dependencies=[Depends(verify_admin_key)])
async def auth_status() -> dict[str, Any]:
    """Check whether WhatsApp is authenticated."""
    try:
        return await whatsapp_service.auth_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/sync/status", dependencies=[Depends(verify_admin_key)])
async def sync_status() -> dict[str, str | None]:
    """Return the current WhatsApp sync state."""
    return whatsapp_service.get_sync_state()


@router.post("/auth/logout", dependencies=[Depends(verify_admin_key)])
async def auth_logout() -> dict[str, Any]:
    """Invalidate the WhatsApp session."""
    try:
        return await whatsapp_service.auth_logout()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/chats", name="whatsapp.chats.list", dependencies=[Depends(check_wa_ready)])
async def list_chats(
    limit: int = Query(20, ge=1, le=100, description="Max chats to return"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List recent WhatsApp chats."""
    check_permission(nano, "whatsapp.chats.list")
    try:
        result: dict[str, Any] = await whatsapp_service.list_chats(limit, session)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")


@router.post("/messages/list", name="whatsapp.messages.list", dependencies=[Depends(check_wa_ready)])
async def list_messages(
    body: WhatsAppListMessagesRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List WhatsApp messages, optionally filtered by chat and time range."""
    check_permission(nano, "whatsapp.messages.list")
    try:
        result: dict[str, Any] = await whatsapp_service.list_messages(
            body.chat_jid, body.limit, body.before, body.after, session
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")


@router.post("/messages/search", name="whatsapp.messages.search", dependencies=[Depends(check_wa_ready)])
async def search_messages(
    body: WhatsAppSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Search WhatsApp messages (offline search of synced messages)."""
    check_permission(nano, "whatsapp.messages.search")
    try:
        result: dict[str, Any] = await whatsapp_service.search_messages(body.query, session)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")


@router.get("/groups", name="whatsapp.groups.list", dependencies=[Depends(check_wa_ready)])
async def list_groups(
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List WhatsApp group chats."""
    check_permission(nano, "whatsapp.groups.list")
    try:
        result: dict[str, Any] = await whatsapp_service.list_groups(session)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")


@router.post("/messages/send", status_code=202, name="whatsapp.messages.send_text", dependencies=[Depends(check_wa_ready)])
async def send_text(
    body: WhatsAppSendTextRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Send a WhatsApp text message. Requires approval (sensitive endpoint)."""
    check_permission(nano, "whatsapp.messages.send_text")

    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano,
        endpoint="whatsapp.messages.send_text",
        method="POST",
        request_body=request_body,
        session=session,
        run_log_id=run_log_id,
        explanation=explanation,
        reasoning=reasoning,
        wait_until_date=wait_until,
        draft_mode=draft_mode,
    )

    return JSONResponse(
        status_code=202,
        content={"approval_id": str(approval.id), "status": approval.status},
    )


@router.post("/messages/send-file", status_code=202, name="whatsapp.messages.send_file", dependencies=[Depends(check_wa_ready)])
async def send_file(
    body: WhatsAppSendFileRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Send a file via WhatsApp. Requires approval (sensitive endpoint)."""
    check_permission(nano, "whatsapp.messages.send_file")

    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano,
        endpoint="whatsapp.messages.send_file",
        method="POST",
        request_body=request_body,
        session=session,
        run_log_id=run_log_id,
        explanation=explanation,
        reasoning=reasoning,
        wait_until_date=wait_until,
        draft_mode=draft_mode,
    )

    return JSONResponse(
        status_code=202,
        content={"approval_id": str(approval.id), "status": approval.status},
    )


@router.post("/media/download", name="whatsapp.media.download", dependencies=[Depends(check_wa_ready)])
async def download_media(
    body: WhatsAppMediaDownloadRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Download a media attachment from a WhatsApp message."""
    check_permission(nano, "whatsapp.media.download")
    try:
        result: dict[str, Any] = await whatsapp_service.download_media(body.chat_jid, body.message_id, session)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")


@router.post("/history/backfill", name="whatsapp.history.backfill", dependencies=[Depends(check_wa_ready)])
async def history_backfill(
    body: WhatsAppBackfillRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Fetch older WhatsApp messages from the primary device."""
    check_permission(nano, "whatsapp.history.backfill")
    try:
        result: dict[str, Any] = await whatsapp_service.history_backfill(
            body.chat_jid, body.requests, body.count, session
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp error: {e}")
