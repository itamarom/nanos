"""Notion router — Databases, Pages, Blocks, Comments, Users, Search."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission, get_run_log_id, get_draft_mode
from gateway.schemas import (
    NotionSearchRequest,
    NotionDatabaseQueryRequest,
    NotionPageCreateRequest,
    NotionPageUpdateRequest,
    NotionDeleteRequest,
    NotionBlocksAppendRequest,
    NotionBlockUpdateRequest,
    NotionCommentCreateRequest,
    NotionListResponse,
)
from gateway.services import notion_service
from gateway.services.approval_service import create_approval

router = APIRouter()


# ------------------------------------------------------------------ #
# Search
# ------------------------------------------------------------------ #

@router.post("/search", response_model=NotionListResponse, name="notion.search")
async def search(
    body: NotionSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Search the Notion workspace."""
    check_permission(nano, "notion.search")
    try:
        return await notion_service.search(
            session, query=body.query, filter=body.filter, sort=body.sort,
            page_size=body.page_size, start_cursor=body.start_cursor,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


# ------------------------------------------------------------------ #
# Databases
# ------------------------------------------------------------------ #

@router.get("/databases/{database_id}", name="notion.databases.get")
async def get_database(
    database_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a database schema and properties."""
    check_permission(nano, "notion.databases.get")
    try:
        return await notion_service.get_database(database_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


@router.post("/databases/{database_id}/query", response_model=NotionListResponse, name="notion.databases.query")
async def query_database(
    database_id: str,
    body: NotionDatabaseQueryRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Query a database with filters and sorts."""
    check_permission(nano, "notion.databases.query")
    try:
        return await notion_service.query_database(
            database_id, session, filter=body.filter, sorts=body.sorts,
            page_size=body.page_size, start_cursor=body.start_cursor,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


# ------------------------------------------------------------------ #
# Pages
# ------------------------------------------------------------------ #

@router.get("/pages/{page_id}", name="notion.pages.get")
async def get_page(
    page_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a page and its properties."""
    check_permission(nano, "notion.pages.get")
    try:
        return await notion_service.get_page(page_id, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


@router.post("/pages", status_code=202, name="notion.pages.create")
async def create_page(
    body: NotionPageCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a page. Requires approval."""
    check_permission(nano, "notion.pages.create")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="notion.pages.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/pages/{page_id}", status_code=202, name="notion.pages.update")
async def update_page(
    page_id: str,
    body: NotionPageUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a page. Requires approval."""
    check_permission(nano, "notion.pages.update")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["page_id"] = page_id
    approval = await create_approval(
        nano=nano, endpoint="notion.pages.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/pages/{page_id}", status_code=202, name="notion.pages.delete")
async def delete_page(
    page_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Archive a page. Requires approval."""
    check_permission(nano, "notion.pages.delete")
    approval = await create_approval(
        nano=nano, endpoint="notion.pages.delete", method="DELETE",
        request_body={"page_id": page_id}, session=session, run_log_id=run_log_id,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


# ------------------------------------------------------------------ #
# Blocks
# ------------------------------------------------------------------ #

@router.get("/blocks/{block_id}/children", response_model=NotionListResponse, name="notion.blocks.list")
async def list_blocks(
    block_id: str,
    page_size: int | None = Query(None),
    start_cursor: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List child blocks of a page or block."""
    check_permission(nano, "notion.blocks.list")
    try:
        return await notion_service.list_blocks(block_id, session, page_size=page_size, start_cursor=start_cursor)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


@router.patch("/blocks/{block_id}/children", status_code=202, name="notion.blocks.append")
async def append_blocks(
    block_id: str,
    body: NotionBlocksAppendRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Append blocks to a page. Requires approval."""
    check_permission(nano, "notion.blocks.append")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["block_id"] = block_id
    approval = await create_approval(
        nano=nano, endpoint="notion.blocks.append", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/blocks/{block_id}", status_code=202, name="notion.blocks.update")
async def update_block(
    block_id: str,
    body: NotionBlockUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a block. Requires approval."""
    check_permission(nano, "notion.blocks.update")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["block_id"] = block_id
    approval = await create_approval(
        nano=nano, endpoint="notion.blocks.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/blocks/{block_id}", status_code=202, name="notion.blocks.delete")
async def delete_block(
    block_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Archive a block. Requires approval."""
    check_permission(nano, "notion.blocks.delete")
    approval = await create_approval(
        nano=nano, endpoint="notion.blocks.delete", method="DELETE",
        request_body={"block_id": block_id}, session=session, run_log_id=run_log_id,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


# ------------------------------------------------------------------ #
# Comments
# ------------------------------------------------------------------ #

@router.get("/comments", response_model=NotionListResponse, name="notion.comments.list")
async def list_comments_endpoint(
    block_id: str | None = Query(None),
    page_size: int | None = Query(None),
    start_cursor: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List comments on a page or block."""
    check_permission(nano, "notion.comments.list")
    try:
        return await notion_service.list_comments(session, block_id=block_id, page_size=page_size, start_cursor=start_cursor)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")


@router.post("/comments", status_code=202, name="notion.comments.create")
async def create_comment(
    body: NotionCommentCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a comment. Requires approval."""
    check_permission(nano, "notion.comments.create")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="notion.comments.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


# ------------------------------------------------------------------ #
# Users
# ------------------------------------------------------------------ #

@router.get("/users", response_model=NotionListResponse, name="notion.users.list")
async def list_users(
    page_size: int | None = Query(None),
    start_cursor: str | None = Query(None),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List workspace users."""
    check_permission(nano, "notion.users.list")
    try:
        return await notion_service.list_users(session, page_size=page_size, start_cursor=start_cursor)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Notion API error: {e}")
