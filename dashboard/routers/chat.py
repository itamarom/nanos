"""Dashboard router for live chat — page routes + JSON proxy to gateway."""

from __future__ import annotations

import os
import json
from typing import Any, Sequence

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from shared.database import get_async_session
from shared.models import ChatConversation, ChatMessage, PendingApproval
from shared.config import ADMIN_API_KEY, SENSITIVE_ENDPOINTS

router = APIRouter()

template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=template_dir)

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
GATEWAY_TIMEOUT = 120.0  # Agent loop can take a while
_SSE_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)


def _pretty_json(value):
    try:
        return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return value


def _from_json(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError):
        return {}


templates.env.filters.setdefault("tojson_pretty", _pretty_json)
templates.env.filters.setdefault("from_json", _from_json)
templates.env.globals["SENSITIVE_ENDPOINTS"] = SENSITIVE_ENDPOINTS


# ---------------------------------------------------------------------------
# Page routes — read DB directly for initial render
# ---------------------------------------------------------------------------

@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Render chat page with no active conversation."""
    result = await session.execute(
        select(ChatConversation).order_by(ChatConversation.updated_at.desc()).limit(50)
    )
    conversations = result.scalars().all()
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "conversations": conversations,
        "active_conversation": None,
        "messages": [],
        "enabled_apis": [],
        "approval_wait": {},
        "batch_approval_ids": [],
        "batch_id": None,
    })


@router.get("/chat/{conv_id}", response_class=HTMLResponse)
async def chat_conversation_page(conv_id: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Render chat page with a specific conversation loaded."""
    result = await session.execute(
        select(ChatConversation).where(ChatConversation.id == conv_id)
    )
    conv = result.scalar_one_or_none()

    result = await session.execute(
        select(ChatConversation).order_by(ChatConversation.updated_at.desc()).limit(50)
    )
    conversations = result.scalars().all()

    messages: Sequence[ChatMessage] = []
    enabled_apis: list[Any] = []
    approval_wait: dict[str, str] = {}
    needs_attach = False
    if conv:
        # If conversation is still "running", the background agent task may
        # still be active — mark for auto-attach instead of resetting.
        if conv.status == "running":
            needs_attach = True

        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.created_at)
        )
        messages = result.scalars().all()  # type: ignore[assignment]
        try:
            enabled_apis = json.loads(conv.enabled_apis) if conv.enabled_apis else []
        except (json.JSONDecodeError, TypeError):
            enabled_apis = []

        # Build lookup for approval wait_until_date
        approval_ids = [m.approval_id for m in messages if m.approval_id and m.tool_status == 'pending_approval']
        if approval_ids:
            result = await session.execute(
                select(PendingApproval.id, PendingApproval.wait_until_date)
                .where(PendingApproval.id.in_(approval_ids))
            )
            for row in result:
                if row.wait_until_date:
                    approval_wait[str(row.id)] = row.wait_until_date.isoformat()

    # Build batch info for page-load JS initialization
    batch_approval_ids = []
    batch_id = None
    if conv and conv.status == "awaiting_approval":
        pending_approval_msgs = [m for m in messages if m.tool_status == 'pending_approval' and m.approval_id]
        if pending_approval_msgs:
            pa_ids = [m.approval_id for m in pending_approval_msgs]
            result = await session.execute(
                select(PendingApproval).where(PendingApproval.id.in_(pa_ids))
            )
            approvals: dict[Any, PendingApproval] = {a.id: a for a in result.scalars().all()}  # type: ignore[misc]
            for m in pending_approval_msgs:
                appr = approvals.get(m.approval_id)
                if appr and appr.status == "pending":
                    batch_approval_ids.append(str(appr.id))
                    if not batch_id and appr.batch_id:
                        batch_id = appr.batch_id

    # Also attach if the last message is from the user with no reply yet
    # (background task may still be running even though page was refreshed)
    if not needs_attach and conv is not None and conv.status == "idle" and messages and messages[-1].role == "user":
        needs_attach = True

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "conversations": conversations,
        "active_conversation": conv,
        "messages": messages,
        "enabled_apis": enabled_apis,
        "approval_wait": approval_wait,
        "batch_approval_ids": batch_approval_ids,
        "batch_id": batch_id,
        "needs_attach": needs_attach,
    })


# ---------------------------------------------------------------------------
# JSON proxy routes — forward to gateway admin API
# ---------------------------------------------------------------------------

@router.post("/chat/new")
async def new_conversation(request: Request) -> JSONResponse:
    """Create a new conversation via gateway."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/chat/conversations",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json=body,
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/chat/{conv_id}/send")
async def send_message(conv_id: str, request: Request) -> JSONResponse:
    """Proxy send to gateway — runs agent loop synchronously."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}/send",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json=body,
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/chat/{conv_id}/continue")
async def continue_conversation(conv_id: str, request: Request) -> JSONResponse:
    """Proxy continue to gateway — resumes after approval."""
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}/continue",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json={},
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.post("/chat/{conv_id}/send-stream")
async def send_message_stream(conv_id: str, request: Request) -> StreamingResponse:
    """Proxy SSE stream from gateway send-stream endpoint."""
    body = await request.json()

    async def proxy_stream():
        async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}/send-stream",
                headers={"X-Admin-Key": ADMIN_API_KEY},
                json=body,
            ) as resp:
                async for line in resp.aiter_lines():
                    yield line + "\n"

    return StreamingResponse(proxy_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat/{conv_id}/continue-stream")
async def continue_conversation_stream(conv_id: str, request: Request) -> StreamingResponse:
    """Proxy SSE stream from gateway continue-stream endpoint."""
    async def proxy_stream():
        async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}/continue-stream",
                headers={"X-Admin-Key": ADMIN_API_KEY},
                json={},
            ) as resp:
                async for line in resp.aiter_lines():
                    yield line + "\n"

    return StreamingResponse(proxy_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/{conv_id}/attach-stream")
async def attach_conversation_stream(conv_id: str, request: Request) -> StreamingResponse:
    """Proxy SSE stream from gateway attach-stream endpoint (re-attach to running stream)."""
    async def proxy_stream():
        async with httpx.AsyncClient(timeout=_SSE_TIMEOUT) as client:
            async with client.stream(
                "GET",
                f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}/attach-stream",
                headers={"X-Admin-Key": ADMIN_API_KEY},
            ) as resp:
                async for line in resp.aiter_lines():
                    yield line + "\n"

    return StreamingResponse(proxy_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/{conv_id}/nano-prompt")
async def get_nano_prompt(conv_id: str) -> JSONResponse:
    """Proxy nano teaching prompt request to gateway."""
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/admin/chat/nano-prompt/{conv_id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.get("/chat/nano-type-instances/{name}")
async def get_nano_type_instances(name: str) -> JSONResponse:
    """Proxy nano type instances request to gateway."""
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.get(
            f"{GATEWAY_URL}/api/admin/chat/nano-type-instances/{name}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.patch("/chat/{conv_id}")
async def update_conversation(conv_id: str, request: Request) -> JSONResponse:
    """Proxy update to gateway."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.patch(
            f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
            json=body,
        )
    return JSONResponse(resp.json(), status_code=resp.status_code)


@router.delete("/chat/{conv_id}")
async def delete_conversation(conv_id: str, request: Request) -> JSONResponse:
    """Proxy delete to gateway."""
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.delete(
            f"{GATEWAY_URL}/api/admin/chat/conversations/{conv_id}",
            headers={"X-Admin-Key": ADMIN_API_KEY},
        )
    if resp.status_code == 204:
        return JSONResponse({"ok": True})
    return JSONResponse(resp.json(), status_code=resp.status_code)
