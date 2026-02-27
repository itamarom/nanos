"""State store endpoints — per-nano key-value store with type tracking."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano, NanoState
from gateway.auth import get_current_nano, check_permission
from gateway.schemas import StateGetResponse, StateSetRequest

router = APIRouter()


@router.get("/state/{key}", response_model=StateGetResponse, name="state.get")
async def state_get(
    key: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> StateGetResponse:
    """Get a state value by key."""
    check_permission(nano, "state.get")

    result = await session.execute(
        select(NanoState).where(NanoState.nano_id == nano.id, NanoState.key == key)
    )
    entry = result.scalar_one_or_none()

    if entry is None:
        return StateGetResponse(key=key, value=None, value_type=None, found=False)

    return StateGetResponse(
        key=key,
        value=json.loads(entry.value),
        value_type=entry.value_type,
        found=True,
    )


@router.put("/state/{key}", name="state.set")
async def state_set(
    key: str,
    body: StateSetRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str | None]:
    """Set (upsert) a state value."""
    check_permission(nano, "state.set")

    result = await session.execute(
        select(NanoState).where(NanoState.nano_id == nano.id, NanoState.key == key)
    )
    entry = result.scalar_one_or_none()

    value_json = json.dumps(body.value)

    if entry:
        entry.value = value_json
        entry.value_type = body.value_type
        entry.updated_at = datetime.utcnow()
    else:
        entry = NanoState(
            nano_id=nano.id,
            key=key,
            value_type=body.value_type,
            value=value_json,
        )
        session.add(entry)

    await session.commit()
    return {"key": key, "value": body.value, "value_type": body.value_type}


@router.delete("/state/{key}", name="state.delete")
async def state_delete(
    key: str,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str | bool]:
    """Delete a state value."""
    check_permission(nano, "state.delete")

    result = await session.execute(
        select(NanoState).where(NanoState.nano_id == nano.id, NanoState.key == key)
    )
    entry = result.scalar_one_or_none()

    if entry:
        await session.delete(entry)
        await session.commit()
        return {"key": key, "deleted": True}

    return {"key": key, "deleted": False}
