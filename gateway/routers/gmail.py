from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission, get_run_log_id, get_draft_mode
from gateway.schemas import GmailMessage, GmailProfile, GmailThread, GmailSendRequest, GmailReplyRequest
from gateway.services import gmail_service
from gateway.services.approval_service import is_sensitive, create_approval

router = APIRouter()


@router.get("/messages", response_model=list[GmailMessage], name="gmail.messages.list")
async def list_messages(
    q: str = Query("", description="Gmail search query"),
    max_results: int = Query(20, ge=1, le=2000, description="Maximum messages to return"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> list[GmailMessage]:
    """Search and list emails."""
    check_permission(nano, "gmail.messages.list")

    try:
        messages = await gmail_service.list_messages(q, session, max_results=max_results)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gmail API error: {e}")

    return [GmailMessage(**msg) for msg in messages]


@router.get("/profile", response_model=GmailProfile, name="gmail.profile")
async def get_profile(
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> GmailProfile:
    """Get the authenticated user's Gmail profile."""
    check_permission(nano, "gmail.messages.list")

    try:
        profile = await gmail_service.get_profile(session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gmail API error: {e}")

    return GmailProfile(**profile)


@router.get("/messages/{message_id}", response_model=GmailMessage, name="gmail.messages.get")
async def get_message(
    message_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> GmailMessage:
    """Get full email content by message ID."""
    check_permission(nano, "gmail.messages.get")

    try:
        msg = await gmail_service.get_message(message_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gmail API error: {e}")

    return GmailMessage(**msg)


@router.get("/threads/{thread_id}", response_model=GmailThread, name="gmail.threads.get")
async def get_thread(
    thread_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> GmailThread:
    """Get an email thread by thread ID."""
    check_permission(nano, "gmail.threads.get")

    try:
        thread = await gmail_service.get_thread(thread_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gmail API error: {e}")

    return GmailThread(**thread)


@router.post("/messages/send", status_code=202, name="gmail.messages.send")
async def send_message(
    body: GmailSendRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Send an email. Requires approval (sensitive endpoint)."""
    check_permission(nano, "gmail.messages.send")

    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano,
        endpoint="gmail.messages.send",
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


@router.post(
    "/messages/{message_id}/reply",
    status_code=202,
    name="gmail.messages.reply",
)
async def reply_to_message(
    message_id: str,
    body: GmailReplyRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Reply to an email. Requires approval (sensitive endpoint)."""
    check_permission(nano, "gmail.messages.reply")

    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["message_id"] = message_id
    approval = await create_approval(
        nano=nano,
        endpoint="gmail.messages.reply",
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
