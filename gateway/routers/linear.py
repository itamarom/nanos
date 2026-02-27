"""Linear router — Issues, Projects, Comments, Teams, Cycles, Users."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission, get_run_log_id, get_draft_mode
from gateway.schemas import (
    LinearIssueSearchRequest,
    LinearIssueCreateRequest,
    LinearIssueUpdateRequest,
    LinearCommentCreateRequest,
    LinearCommentUpdateRequest,
    LinearDeleteRequest,
    LinearPaginatedResponse,
)
from gateway.services import linear_service
from gateway.services.approval_service import create_approval

router = APIRouter()


# ------------------------------------------------------------------ #
# Issues
# ------------------------------------------------------------------ #

@router.post("/issues/search", response_model=LinearPaginatedResponse, name="linear.issues.list")
async def search_issues(
    body: LinearIssueSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List/search issues with optional filter."""
    check_permission(nano, "linear.issues.list")
    try:
        return await linear_service.list_issues(
            session, filter=body.filter, first=body.first, after=body.after,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


@router.get("/issues/{issue_id}", name="linear.issues.get")
async def get_issue(
    issue_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single issue by ID."""
    check_permission(nano, "linear.issues.get")
    try:
        return await linear_service.get_issue(issue_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


@router.post("/issues", status_code=202, name="linear.issues.create")
async def create_issue(
    body: LinearIssueCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create an issue. Requires approval."""
    check_permission(nano, "linear.issues.create")
    request_body = body.model_dump(exclude_none=True)
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="linear.issues.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/issues/{issue_id}", status_code=202, name="linear.issues.update")
async def update_issue(
    issue_id: str,
    body: LinearIssueUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update an issue. Requires approval."""
    check_permission(nano, "linear.issues.update")
    request_body = body.model_dump(exclude_none=True)
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["issue_id"] = issue_id
    approval = await create_approval(
        nano=nano, endpoint="linear.issues.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/issues/{issue_id}", status_code=202, name="linear.issues.delete")
async def delete_issue(
    issue_id: str,
    body: LinearDeleteRequest | None = None,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Archive an issue. Requires approval."""
    check_permission(nano, "linear.issues.delete")
    request_body = {"issue_id": issue_id}
    explanation = reasoning = None
    wait_until = None
    if body:
        explanation = body.explanation
        reasoning = body.reasoning
        if body.wait_until_date:
            wait_until = datetime.fromisoformat(body.wait_until_date)
    approval = await create_approval(
        nano=nano, endpoint="linear.issues.delete", method="DELETE",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


# ------------------------------------------------------------------ #
# Comments
# ------------------------------------------------------------------ #

@router.get("/issues/{issue_id}/comments", response_model=LinearPaginatedResponse, name="linear.comments.list")
async def list_comments(
    issue_id: str,
    first: int = Query(50, ge=1, le=100),
    after: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List comments on an issue."""
    check_permission(nano, "linear.comments.list")
    try:
        return await linear_service.list_comments(issue_id, session, first=first, after=after)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


@router.post("/comments", status_code=202, name="linear.comments.create")
async def create_comment(
    body: LinearCommentCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a comment on an issue. Requires approval."""
    check_permission(nano, "linear.comments.create")
    request_body = body.model_dump(exclude_none=True)
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="linear.comments.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/comments/{comment_id}", status_code=202, name="linear.comments.update")
async def update_comment(
    comment_id: str,
    body: LinearCommentUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a comment. Requires approval."""
    check_permission(nano, "linear.comments.update")
    request_body = body.model_dump(exclude_none=True)
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["comment_id"] = comment_id
    approval = await create_approval(
        nano=nano, endpoint="linear.comments.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/comments/{comment_id}", status_code=202, name="linear.comments.delete")
async def delete_comment(
    comment_id: str,
    body: LinearDeleteRequest | None = None,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Delete a comment. Requires approval."""
    check_permission(nano, "linear.comments.delete")
    request_body = {"comment_id": comment_id}
    explanation = reasoning = None
    wait_until = None
    if body:
        explanation = body.explanation
        reasoning = body.reasoning
        if body.wait_until_date:
            wait_until = datetime.fromisoformat(body.wait_until_date)
    approval = await create_approval(
        nano=nano, endpoint="linear.comments.delete", method="DELETE",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


# ------------------------------------------------------------------ #
# Projects
# ------------------------------------------------------------------ #

@router.get("/projects", response_model=LinearPaginatedResponse, name="linear.projects.list")
async def list_projects(
    first: int = Query(50, ge=1, le=100),
    after: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List projects."""
    check_permission(nano, "linear.projects.list")
    try:
        return await linear_service.list_projects(session, first=first, after=after)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


@router.get("/projects/{project_id}", name="linear.projects.get")
async def get_project(
    project_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single project by ID."""
    check_permission(nano, "linear.projects.get")
    try:
        return await linear_service.get_project(project_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


# ------------------------------------------------------------------ #
# Teams
# ------------------------------------------------------------------ #

@router.get("/teams", name="linear.teams.list")
async def list_teams(
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List teams with their workflow states and labels."""
    check_permission(nano, "linear.teams.list")
    try:
        return await linear_service.list_teams(session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


# ------------------------------------------------------------------ #
# Cycles
# ------------------------------------------------------------------ #

@router.get("/teams/{team_id}/cycles", response_model=LinearPaginatedResponse, name="linear.cycles.list")
async def list_cycles(
    team_id: str,
    first: int = Query(20, ge=1, le=50),
    after: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List cycles for a team."""
    check_permission(nano, "linear.cycles.list")
    try:
        return await linear_service.list_cycles(team_id, session, first=first, after=after)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")


# ------------------------------------------------------------------ #
# Users
# ------------------------------------------------------------------ #

@router.get("/users", name="linear.users.list")
async def list_users(
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List workspace users and get the authenticated viewer."""
    check_permission(nano, "linear.users.list")
    try:
        return await linear_service.list_users(session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Linear API error: {e}")
