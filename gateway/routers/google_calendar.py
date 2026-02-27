"""FastAPI router for Google Calendar endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission, get_run_log_id, get_draft_mode
from gateway.schemas import (
    ApprovalCreatedResponse, CalendarEvent, CalendarEventCreate, CalendarEventUpdate,
)
from gateway.services.approval_service import create_approval

_APPROVAL_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {"model": ApprovalCreatedResponse},
}

router = APIRouter()


@router.get("/events", response_model=list[CalendarEvent])
async def list_events(
    start: str = Query(..., description="RFC 3339 start time"),
    end: str = Query(..., description="RFC 3339 end time"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> list[CalendarEvent]:
    """List calendar events between start and end dates."""
    check_permission(nano, "calendar.events.list")

    from gateway.services.google_calendar_service import list_events as svc_list_events

    events = await svc_list_events(start, end, session)
    return [CalendarEvent(**e) for e in events]


@router.post("/events", responses=_APPROVAL_RESPONSES, status_code=202)
async def create_event(
    body: CalendarEventCreate,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a calendar event (SENSITIVE -- requires approval)."""
    check_permission(nano, "calendar.events.create")

    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano,
        endpoint="calendar.events.create",
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


@router.put("/events/{event_id}", responses=_APPROVAL_RESPONSES, status_code=202)
async def update_event(
    event_id: str,
    body: CalendarEventUpdate,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a calendar event (SENSITIVE -- requires approval)."""
    check_permission(nano, "calendar.events.update")

    request_body = body.model_dump(exclude_none=True)
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["event_id"] = event_id

    approval = await create_approval(
        nano=nano,
        endpoint="calendar.events.update",
        method="PUT",
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


@router.delete("/events/{event_id}", responses=_APPROVAL_RESPONSES, status_code=202)
async def delete_event(
    event_id: str,
    request: Request,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Delete a calendar event (SENSITIVE -- requires approval)."""
    check_permission(nano, "calendar.events.delete")

    approval = await create_approval(
        nano=nano,
        endpoint="calendar.events.delete",
        method="DELETE",
        request_body={"event_id": event_id},
        session=session,
        run_log_id=run_log_id,
        explanation=request.headers.get("x-approval-explanation"),
        reasoning=request.headers.get("x-approval-reasoning"),
        draft_mode=draft_mode,
    )
    return JSONResponse(
        status_code=202,
        content={"approval_id": str(approval.id), "status": approval.status},
    )
