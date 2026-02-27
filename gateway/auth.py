from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import get_async_session
from shared.models import Nano, NanoApiKey
from gateway.config import ADMIN_API_KEY


async def get_current_nano(
    x_nano_key: str = Header(..., alias="X-Nano-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> Nano:
    """Validate X-Nano-Key header and return the associated Nano."""
    result = await session.execute(
        select(NanoApiKey)
        .options(selectinload(NanoApiKey.nano).selectinload(Nano.permissions))
        .where(NanoApiKey.key == x_nano_key, NanoApiKey.is_active.is_(True))
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    return api_key.nano


def check_permission(nano: Nano, endpoint: str) -> None:
    """Check if a nano has permission for the given endpoint.

    Effective permissions = instance DB permissions UNION type config permissions.
    """
    # Instance permissions from DB
    instance_perms = {p.endpoint for p in nano.permissions}

    # Type permissions from config.yaml on disk
    type_perms = set()
    if nano.type_name:
        from shared.nano_types import load_type
        type_config = load_type(nano.type_name)
        if type_config:
            type_perms = set(type_config.get("permissions", []))

    allowed = instance_perms | type_perms
    if endpoint not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Nano '{nano.name}' lacks permission for '{endpoint}'",
        )


def get_run_log_id(
    x_nano_run_log_id: Optional[str] = Header(None, alias="X-Nano-Run-Log-Id"),
) -> str | None:
    """Extract the optional run log ID header sent by nano subprocesses."""
    return x_nano_run_log_id


def get_draft_mode(
    x_draft_mode: Optional[str] = Header(None, alias="X-Draft-Mode"),
) -> bool:
    """Extract X-Draft-Mode header. Returns True when the nano is running in draft mode."""
    return x_draft_mode == "true"


async def verify_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> str:
    """Validate the admin API key."""
    if not hmac.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return x_admin_key
