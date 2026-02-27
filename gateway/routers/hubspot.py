"""HubSpot CRM router — Contacts, Deals, Tasks, Properties."""

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
    HubSpotObjectResponse,
    HubSpotListResponse,
    HubSpotSearchResponse,
    HubSpotCreateRequest,
    HubSpotUpdateRequest,
    HubSpotSearchRequest,
)
from gateway.services import hubspot_service
from gateway.services.approval_service import create_approval

router = APIRouter()


# ------------------------------------------------------------------ #
# Contacts
# ------------------------------------------------------------------ #

@router.get("/contacts", response_model=HubSpotListResponse, name="hubspot.contacts.list")
async def list_contacts(
    limit: int = Query(20, ge=1, le=100),
    after: str | None = Query(None),
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List CRM contacts."""
    check_permission(nano, "hubspot.contacts.list")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.list_contacts(session, limit=limit, after=after, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.get("/contacts/{contact_id}", response_model=HubSpotObjectResponse, name="hubspot.contacts.get")
async def get_contact(
    contact_id: str,
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single contact by ID."""
    check_permission(nano, "hubspot.contacts.get")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.get_contact(contact_id, session, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.post("/contacts", status_code=202, name="hubspot.contacts.create")
async def create_contact(
    body: HubSpotCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a contact. Requires approval."""
    check_permission(nano, "hubspot.contacts.create")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="hubspot.contacts.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/contacts/{contact_id}", status_code=202, name="hubspot.contacts.update")
async def update_contact(
    contact_id: str,
    body: HubSpotUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a contact. Requires approval."""
    check_permission(nano, "hubspot.contacts.update")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["contact_id"] = contact_id
    approval = await create_approval(
        nano=nano, endpoint="hubspot.contacts.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/contacts/{contact_id}", status_code=202, name="hubspot.contacts.delete")
async def delete_contact(
    contact_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Delete a contact. Requires approval."""
    check_permission(nano, "hubspot.contacts.delete")
    approval = await create_approval(
        nano=nano, endpoint="hubspot.contacts.delete", method="DELETE",
        request_body={"contact_id": contact_id}, session=session, run_log_id=run_log_id,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.post("/contacts/search", response_model=HubSpotSearchResponse, name="hubspot.contacts.search")
async def search_contacts(
    body: HubSpotSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Search contacts using filters."""
    check_permission(nano, "hubspot.contacts.search")
    try:
        return await hubspot_service.search_contacts(
            body.filters, body.properties, body.limit, session,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


# ------------------------------------------------------------------ #
# Deals
# ------------------------------------------------------------------ #

@router.get("/deals", response_model=HubSpotListResponse, name="hubspot.deals.list")
async def list_deals(
    limit: int = Query(20, ge=1, le=100),
    after: str | None = Query(None),
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List CRM deals."""
    check_permission(nano, "hubspot.deals.list")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.list_deals(session, limit=limit, after=after, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.get("/deals/{deal_id}", response_model=HubSpotObjectResponse, name="hubspot.deals.get")
async def get_deal(
    deal_id: str,
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single deal by ID."""
    check_permission(nano, "hubspot.deals.get")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.get_deal(deal_id, session, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.post("/deals", status_code=202, name="hubspot.deals.create")
async def create_deal(
    body: HubSpotCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a deal. Requires approval."""
    check_permission(nano, "hubspot.deals.create")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="hubspot.deals.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/deals/{deal_id}", status_code=202, name="hubspot.deals.update")
async def update_deal(
    deal_id: str,
    body: HubSpotUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a deal. Requires approval."""
    check_permission(nano, "hubspot.deals.update")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["deal_id"] = deal_id
    approval = await create_approval(
        nano=nano, endpoint="hubspot.deals.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/deals/{deal_id}", status_code=202, name="hubspot.deals.delete")
async def delete_deal(
    deal_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Delete a deal. Requires approval."""
    check_permission(nano, "hubspot.deals.delete")
    approval = await create_approval(
        nano=nano, endpoint="hubspot.deals.delete", method="DELETE",
        request_body={"deal_id": deal_id}, session=session, run_log_id=run_log_id,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.post("/deals/search", response_model=HubSpotSearchResponse, name="hubspot.deals.search")
async def search_deals(
    body: HubSpotSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Search deals using filters."""
    check_permission(nano, "hubspot.deals.search")
    try:
        return await hubspot_service.search_deals(
            body.filters, body.properties, body.limit, session,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


# ------------------------------------------------------------------ #
# Tasks
# ------------------------------------------------------------------ #

@router.get("/tasks", response_model=HubSpotListResponse, name="hubspot.tasks.list")
async def list_tasks(
    limit: int = Query(20, ge=1, le=100),
    after: str | None = Query(None),
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """List CRM tasks."""
    check_permission(nano, "hubspot.tasks.list")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.list_tasks(session, limit=limit, after=after, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.get("/tasks/{task_id}", response_model=HubSpotObjectResponse, name="hubspot.tasks.get")
async def get_task(
    task_id: str,
    properties: str | None = Query(None, description="Comma-separated property names"),
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single task by ID."""
    check_permission(nano, "hubspot.tasks.get")
    props = [p.strip() for p in properties.split(",")] if properties else None
    try:
        return await hubspot_service.get_task(task_id, session, properties=props)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.post("/tasks", status_code=202, name="hubspot.tasks.create")
async def create_task(
    body: HubSpotCreateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Create a task. Requires approval."""
    check_permission(nano, "hubspot.tasks.create")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    approval = await create_approval(
        nano=nano, endpoint="hubspot.tasks.create", method="POST",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.patch("/tasks/{task_id}", status_code=202, name="hubspot.tasks.update")
async def update_task(
    task_id: str,
    body: HubSpotUpdateRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Update a task. Requires approval."""
    check_permission(nano, "hubspot.tasks.update")
    request_body = body.model_dump()
    explanation = request_body.pop("explanation", None)
    reasoning = request_body.pop("reasoning", None)
    wait_str = request_body.pop("wait_until_date", None)
    wait_until = datetime.fromisoformat(wait_str) if wait_str else None
    request_body["task_id"] = task_id
    approval = await create_approval(
        nano=nano, endpoint="hubspot.tasks.update", method="PATCH",
        request_body=request_body, session=session, run_log_id=run_log_id,
        explanation=explanation, reasoning=reasoning, wait_until_date=wait_until,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.delete("/tasks/{task_id}", status_code=202, name="hubspot.tasks.delete")
async def delete_task(
    task_id: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
    run_log_id: str | None = Depends(get_run_log_id),
    draft_mode: bool = Depends(get_draft_mode),
) -> JSONResponse:
    """Delete a task. Requires approval."""
    check_permission(nano, "hubspot.tasks.delete")
    approval = await create_approval(
        nano=nano, endpoint="hubspot.tasks.delete", method="DELETE",
        request_body={"task_id": task_id}, session=session, run_log_id=run_log_id,
        draft_mode=draft_mode,
    )
    return JSONResponse(status_code=202, content={"approval_id": str(approval.id), "status": approval.status})


@router.post("/tasks/search", response_model=HubSpotSearchResponse, name="hubspot.tasks.search")
async def search_tasks(
    body: HubSpotSearchRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Search tasks using filters."""
    check_permission(nano, "hubspot.tasks.search")
    try:
        return await hubspot_service.search_tasks(
            body.filters, body.properties, body.limit, session,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


# ------------------------------------------------------------------ #
# Properties (schema / metadata)
# ------------------------------------------------------------------ #

@router.get("/properties/{object_type}", name="hubspot.properties.list")
async def list_properties(
    object_type: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> list[dict[str, Any]]:
    """List all property definitions for a CRM object type (contacts, deals, tasks)."""
    check_permission(nano, "hubspot.properties.list")
    try:
        return await hubspot_service.list_properties(object_type, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")


@router.get("/properties/{object_type}/{property_name}", name="hubspot.properties.get")
async def get_property(
    object_type: str,
    property_name: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Get a single property definition including options for dropdowns."""
    check_permission(nano, "hubspot.properties.get")
    try:
        return await hubspot_service.get_property(object_type, property_name, session)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HubSpot API error: {e}")
