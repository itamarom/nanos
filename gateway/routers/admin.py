from __future__ import annotations

import json
import secrets
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, case, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import get_async_session
from shared.models import Nano, NanoApiKey, NanoPermission, NanoState, ApiCredential, PendingApproval, RunLog
from gateway.auth import verify_admin_key
from gateway.schemas import (
    NanoCreate, NanoUpdate, NanoOut, NanoCreatedOut, NanoTypeOut,
    CredentialCreate, CredentialOut,
    ApprovalOut, RunLogOut,
    RunNanoResponse, StopRunResponse,
)
from shared.nano_types import load_type, list_types
from gateway import crypto

router = APIRouter(dependencies=[Depends(verify_admin_key)])

# --- Lock / Unlock (no admin key required) ---
# These are mounted on a separate router without auth
lock_router = APIRouter()


@lock_router.get("/lock-status")
async def lock_status(session: AsyncSession = Depends(get_async_session)) -> dict[str, bool]:
    result = await session.execute(select(ApiCredential).limit(1))
    has_credentials = result.scalar_one_or_none() is not None
    return {"locked": not crypto.is_unlocked(), "has_credentials": has_credentials}


@lock_router.post("/unlock")
async def unlock(body: dict[str, str], session: AsyncSession = Depends(get_async_session)) -> dict[str, bool]:
    key = body.get("master_key", "")
    if not key:
        raise HTTPException(status_code=400, detail="master_key is required")

    # Verify the key by trying to decrypt an existing credential
    result = await session.execute(select(ApiCredential).limit(1))
    existing = result.scalar_one_or_none()
    if existing:
        # There are credentials — the key must decrypt them
        crypto.set_master_key(key)
        try:
            crypto.decrypt(existing.credentials)
        except (ValueError, Exception):
            crypto.clear_master_key()
            raise HTTPException(status_code=403, detail="Wrong master key")
    else:
        # No credentials yet — any key becomes the new key
        crypto.set_master_key(key)

    return {"locked": False}


@lock_router.post("/lock")
async def lock() -> dict[str, bool]:
    crypto.clear_master_key()
    return {"locked": True}


@lock_router.post("/reset")
async def reset_master_key(session: AsyncSession = Depends(get_async_session)) -> dict[str, bool]:
    """Delete ALL credentials and clear the master key. Requires confirmation."""
    await session.execute(delete(ApiCredential))
    await session.commit()
    crypto.clear_master_key()
    return {"reset": True}


async def _delete_nano_and_related(session: AsyncSession, nano: Nano) -> None:
    """Delete a nano and all its related records."""
    nano_id = nano.id
    await session.execute(delete(RunLog).where(RunLog.nano_id == nano_id))
    await session.execute(delete(PendingApproval).where(PendingApproval.nano_id == nano_id))
    await session.execute(delete(NanoState).where(NanoState.nano_id == nano_id))
    await session.execute(delete(NanoPermission).where(NanoPermission.nano_id == nano_id))
    await session.execute(delete(NanoApiKey).where(NanoApiKey.nano_id == nano_id))
    await session.delete(nano)


def _nano_to_out(nano: Nano) -> dict[str, Any]:
    """Build a NanoOut-compatible dict from a Nano ORM instance."""
    params = None
    if nano.parameters:
        try:
            params = json.loads(nano.parameters)
        except (json.JSONDecodeError, TypeError):
            params = None
    return {
        **{c.name: getattr(nano, c.name) for c in Nano.__table__.columns},
        "permissions": [p.endpoint for p in nano.permissions],
        "parameters": params,
    }


# --- Type discovery (read-only, from disk) ---

@router.get("/types", response_model=list[NanoTypeOut])
async def get_types() -> list[NanoTypeOut]:
    """List all nano types discovered from /nanos/*/config.yaml."""
    types = list_types()
    return [
        NanoTypeOut(
            type_name=t["type_name"],
            name=t.get("name", t["type_name"]),
            description=t.get("description", ""),
            schedule=t.get("schedule"),
            permissions=t.get("permissions", []),
            parameter_schema=t.get("parameter_schema"),
        )
        for t in types
    ]


@router.get("/types/{type_name}", response_model=NanoTypeOut)
async def get_type(type_name: str) -> NanoTypeOut:
    """Get a single nano type's config."""
    t = load_type(type_name)
    if not t:
        raise HTTPException(status_code=404, detail=f"Type '{type_name}' not found")
    return NanoTypeOut(
        type_name=t["type_name"],
        name=t.get("name", t["type_name"]),
        description=t.get("description", ""),
        schedule=t.get("schedule"),
        permissions=t.get("permissions", []),
        parameter_schema=t.get("parameter_schema"),
    )


@router.delete("/types/{type_name}")
async def delete_type(
    type_name: str,
    force: bool = Query(False),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Delete a nano type from disk. If force=True, also delete all DB instances."""
    type_dir = Path("/nanos") / type_name
    if not type_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Type '{type_name}' not found")

    # Find instances (excluding the internal __chat__ nano)
    result = await session.execute(
        select(Nano).where(Nano.type_name == type_name, Nano.name != "__chat__")
    )
    instances = result.scalars().all()

    if instances and not force:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"Type '{type_name}' has {len(instances)} instance(s). Use force=true to delete them.",
                "instances": [n.name for n in instances],
            },
        )

    # Delete all instances from DB
    for nano in instances:
        await _delete_nano_and_related(session, nano)
    if instances:
        await session.commit()

    # Remove type directory from disk
    shutil.rmtree(type_dir)
    return Response(status_code=204)


# --- Nano management ---

@router.get("/nanos", response_model=list[NanoOut])
async def list_nanos(session: AsyncSession = Depends(get_async_session)) -> list[NanoOut]:
    result = await session.execute(
        select(Nano).options(selectinload(Nano.permissions)).order_by(Nano.name)
    )
    nanos = result.scalars().all()
    return [NanoOut(**_nano_to_out(n)) for n in nanos]


@router.get("/nanos/export")
async def export_nanos(session: AsyncSession = Depends(get_async_session)) -> dict[str, int | list[dict[str, Any]]]:
    """Export all nanos as JSON (name, permissions, schedule, parameters, etc.)."""
    result = await session.execute(
        select(Nano).options(selectinload(Nano.permissions)).order_by(Nano.name)
    )
    nanos = result.scalars().all()
    return {
        "version": 1,
        "nanos": [
            {
                "name": n.name,
                "description": n.description,
                "type_name": n.type_name,
                "schedule": n.schedule,
                "is_active": n.is_active,
                "permissions": [p.endpoint for p in n.permissions],
                "parameters": json.loads(n.parameters) if n.parameters else None,
            }
            for n in nanos
        ],
    }


@router.post("/nanos/import")
async def import_nanos(body: dict[str, Any], session: AsyncSession = Depends(get_async_session)) -> dict[str, int | list[str]]:
    """Import nanos from an exported JSON. Skips names that already exist."""
    items: list[dict[str, Any]] = body.get("nanos", [])
    if not items:
        raise HTTPException(status_code=400, detail="No nanos in payload")

    # Get existing nano names
    result = await session.execute(select(Nano.name))
    existing_names = {row[0] for row in result.all()}

    imported = 0
    skipped: list[str] = []
    for item in items:
        name = item.get("name", "")
        if not name:
            continue
        if name in existing_names:
            skipped.append(name)
            continue

        type_name = item.get("type_name") or name
        script_path = f"{type_name}/nano.py"

        nano = Nano(
            id=uuid.uuid4(),
            name=name,
            description=item.get("description", ""),
            script_path=script_path,
            schedule=item.get("schedule"),
            is_active=item.get("is_active", True),
            type_name=type_name,
            parameters=json.dumps(item["parameters"]) if item.get("parameters") else None,
        )
        session.add(nano)
        await session.flush()

        # Add permissions
        for endpoint in item.get("permissions", []):
            session.add(NanoPermission(
                id=uuid.uuid4(),
                nano_id=nano.id,
                endpoint=endpoint,
            ))

        # Generate an API key
        key = f"nk_{secrets.token_hex(16)}"
        session.add(NanoApiKey(
            id=uuid.uuid4(),
            nano_id=nano.id,
            key=key,
            is_active=True,
        ))

        imported += 1

    await session.commit()
    return {"imported": imported, "skipped": skipped}


@router.post("/nanos", response_model=NanoCreatedOut, status_code=201)
async def create_nano(body: NanoCreate, session: AsyncSession = Depends(get_async_session)) -> NanoCreatedOut:
    # Resolve type_name and script_path
    type_name = body.type_name
    script_path = body.script_path

    if type_name and not script_path:
        script_path = f"{type_name}/nano.py"

    if not script_path and not type_name:
        script_path = f"{body.name}/nano.py"
        type_name = body.name

    if not type_name:
        type_name = script_path.split("/")[0] if "/" in script_path else body.name

    nano = Nano(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        script_path=script_path,
        schedule=body.schedule,
        is_active=True,
        type_name=type_name,
        parameters=json.dumps(body.parameters) if body.parameters else None,
    )
    session.add(nano)

    api_key = "nk_" + secrets.token_hex(16)
    session.add(NanoApiKey(nano_id=nano.id, key=api_key))

    for perm in body.permissions:
        session.add(NanoPermission(nano_id=nano.id, endpoint=perm))

    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"A nano named '{body.name}' already exists")
        raise HTTPException(status_code=500, detail=str(exc))
    await session.refresh(nano, attribute_names=["permissions"])

    return NanoCreatedOut(**_nano_to_out(nano), api_key=api_key)


@router.get("/nanos/{nano_id}", response_model=NanoOut)
async def get_nano(nano_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> NanoOut:
    result = await session.execute(
        select(Nano).options(selectinload(Nano.permissions)).where(Nano.id == nano_id)
    )
    nano = result.scalar_one_or_none()
    if not nano:
        raise HTTPException(status_code=404, detail="Nano not found")
    return NanoOut(**_nano_to_out(nano))


@router.put("/nanos/{nano_id}", response_model=NanoOut)
async def update_nano(
    nano_id: uuid.UUID,
    body: NanoUpdate,
    session: AsyncSession = Depends(get_async_session),
) -> NanoOut:
    result = await session.execute(
        select(Nano).options(selectinload(Nano.permissions)).where(Nano.id == nano_id)
    )
    nano = result.scalar_one_or_none()
    if not nano:
        raise HTTPException(status_code=404, detail="Nano not found")

    if body.name is not None:
        # Check uniqueness
        existing = await session.execute(
            select(Nano).where(Nano.name == body.name, Nano.id != nano_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="A nano with that name already exists")
        nano.name = body.name
    if body.description is not None:
        nano.description = body.description
    if body.is_active is not None:
        nano.is_active = body.is_active
    if body.schedule is not None:
        nano.schedule = body.schedule
    if body.parameters is not None:
        nano.parameters = json.dumps(body.parameters)
    if body.permissions is not None:
        await session.execute(
            delete(NanoPermission).where(NanoPermission.nano_id == nano_id)
        )
        for perm in body.permissions:
            session.add(NanoPermission(nano_id=nano_id, endpoint=perm))

    nano.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(nano, attribute_names=["permissions"])

    return NanoOut(**_nano_to_out(nano))


@router.delete("/nanos/{nano_id}")
async def delete_nano(nano_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> Response:
    result = await session.execute(select(Nano).where(Nano.id == nano_id))
    nano = result.scalar_one_or_none()
    if not nano:
        raise HTTPException(status_code=404, detail="Nano not found")
    await _delete_nano_and_related(session, nano)
    await session.commit()
    return Response(status_code=204)


@router.post("/nanos/{nano_id}/run", response_model=RunNanoResponse)
async def run_nano(
    nano_id: uuid.UUID,
    body: dict[str, bool] | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> RunNanoResponse:
    """Trigger a manual run of a nano via Celery."""
    result = await session.execute(select(Nano).where(Nano.id == nano_id))
    nano = result.scalar_one_or_none()
    if not nano:
        raise HTTPException(status_code=404, detail="Nano not found")

    draft_mode = bool(body.get("draft_mode")) if body else False

    # Create RunLog upfront so we can return its ID immediately
    run_log = RunLog(
        id=uuid.uuid4(),
        nano_id=nano.id,
        trigger="manual",
        started_at=datetime.utcnow(),
        status="running",
        draft_mode=draft_mode,
    )
    session.add(run_log)
    await session.commit()

    from celery import Celery
    from shared.config import REDIS_URL

    celery_app = Celery(broker=REDIS_URL)
    task = celery_app.send_task(
        "tasks.run_nano_task",
        args=[str(nano.id), "manual", str(run_log.id), draft_mode],
    )

    # Store the Celery task ID so we can revoke it later
    run_log.celery_task_id = task.id
    await session.commit()

    return RunNanoResponse(task_id=task.id, nano_name=nano.name, run_log_id=str(run_log.id))


@router.post("/runs/{run_log_id}/stop")
async def stop_run(run_log_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> StopRunResponse:
    """Stop a running nano by revoking its Celery task."""
    result = await session.execute(select(RunLog).where(RunLog.id == run_log_id))
    run_log = result.scalar_one_or_none()
    if not run_log:
        raise HTTPException(status_code=404, detail="Run not found")

    if run_log.status not in ("running", "awaiting_approval"):
        raise HTTPException(status_code=400, detail="Run is not active")

    if run_log.celery_task_id:
        from celery import Celery
        from shared.config import REDIS_URL
        celery_app = Celery(broker=REDIS_URL)
        celery_app.control.revoke(run_log.celery_task_id, terminate=True, signal="SIGTERM")

    run_log.status = "error"
    run_log.stderr = (run_log.stderr or "") + "\n[Stopped by user]"
    run_log.finished_at = datetime.utcnow()
    run_log.exit_code = -15
    await session.commit()

    return StopRunResponse(status="stopped", run_log_id=str(run_log.id))


# --- Credential management ---

@router.get("/credentials", response_model=list[CredentialOut])
async def list_credentials(session: AsyncSession = Depends(get_async_session)) -> Sequence[ApiCredential]:
    result = await session.execute(select(ApiCredential).order_by(ApiCredential.api_name))
    return result.scalars().all()


@router.post("/credentials", response_model=CredentialOut, status_code=201)
async def add_credential(body: CredentialCreate, session: AsyncSession = Depends(get_async_session)) -> ApiCredential:
    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked — unlock with master key first")

    encrypted = crypto.encrypt(json.dumps(body.credentials))

    # Upsert: if exists, update
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == body.api_name)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.credentials = encrypted
        existing.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(existing)
        return existing

    cred = ApiCredential(
        api_name=body.api_name,
        credentials=encrypted,
    )
    session.add(cred)
    await session.commit()
    await session.refresh(cred)
    return cred


@router.get("/credentials/export")
async def export_credentials(session: AsyncSession = Depends(get_async_session)) -> dict[str, int | list[dict[str, str]]]:
    """Export all credentials as encrypted JSON (Fernet tokens, not plaintext)."""
    result = await session.execute(select(ApiCredential).order_by(ApiCredential.api_name))
    creds = result.scalars().all()
    return {
        "version": 1,
        "credentials": [
            {"api_name": c.api_name, "credentials": c.credentials}
            for c in creds
        ],
    }


@router.post("/credentials/import")
async def import_credentials(body: dict[str, Any], session: AsyncSession = Depends(get_async_session)) -> dict[str, int]:
    """Import credentials from an exported JSON.

    If `password` is provided, each credential is decrypted with that
    passphrase and re-encrypted with the current master key.  This allows
    importing from a different instance or after changing the master key.
    Without a password the values are assumed to match the current key.
    """
    if not crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="System is locked — unlock with master key first")

    items = body.get("credentials", [])
    if not items:
        raise HTTPException(status_code=400, detail="No credentials in payload")

    password: str | None = body.get("password")

    if password:
        # Verify the password can decrypt the first entry
        try:
            crypto.decrypt_with_passphrase(items[0]["credentials"], password)
        except (ValueError, Exception):
            raise HTTPException(
                status_code=403,
                detail="Wrong password — cannot decrypt the exported credentials",
            )
    else:
        # No password — try decrypting with the current master key
        try:
            crypto.decrypt(items[0]["credentials"])
        except (ValueError, Exception):
            raise HTTPException(
                status_code=403,
                detail="Cannot decrypt — the export was made with a different master key. "
                       "Please provide the export password.",
            )

    imported = 0
    for item in items:
        api_name = item.get("api_name")
        encrypted = item.get("credentials")
        if not api_name or not encrypted:
            continue

        if password:
            # Decrypt with the export password, re-encrypt with current master key
            plaintext = crypto.decrypt_with_passphrase(encrypted, password)
            encrypted = crypto.encrypt(plaintext)

        result = await session.execute(
            select(ApiCredential).where(ApiCredential.api_name == api_name)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.credentials = encrypted
            existing.updated_at = datetime.utcnow()
        else:
            session.add(ApiCredential(api_name=api_name, credentials=encrypted))
        imported += 1

    await session.commit()
    return {"imported": imported}


@router.delete("/credentials/{api_name}")
async def delete_credential(api_name: str, session: AsyncSession = Depends(get_async_session)) -> Response:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == api_name)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    await session.delete(cred)
    await session.commit()
    return Response(status_code=204)


# --- Approval management ---

@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_async_session),
) -> list[ApprovalOut]:
    query = select(PendingApproval).options(selectinload(PendingApproval.nano))
    if status:
        query = query.where(PendingApproval.status == status)
    query = query.order_by(
        case((PendingApproval.status == "pending", 0), else_=1),
        case(
            (PendingApproval.wait_until_date.is_(None), 0),
            (PendingApproval.wait_until_date <= func.now(), 0),
            else_=1,
        ),
        PendingApproval.created_at.desc(),
    )
    result = await session.execute(query)
    approvals = result.scalars().all()
    return [
        ApprovalOut(
            id=a.id,
            nano_name=a.nano.name,
            endpoint=a.endpoint,
            method=a.method,
            request_body=a.request_body,
            status=a.status,
            explanation=a.explanation,
            reasoning=a.reasoning,
            wait_until_date=a.wait_until_date,
            created_at=a.created_at,
            resolved_at=a.resolved_at,
        )
        for a in approvals
    ]


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(approval_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> ApprovalOut:
    result = await session.execute(
        select(PendingApproval).options(selectinload(PendingApproval.nano)).where(PendingApproval.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval already {approval.status}")

    approval.status = "approved"
    approval.resolved_at = datetime.utcnow()
    await session.commit()

    # Execute the approved action (also calls maybe_complete_run)
    from gateway.services.approval_service import execute_approved_action
    await execute_approved_action(approval, session)

    await session.refresh(approval)
    return ApprovalOut(
        id=approval.id,
        nano_name=approval.nano.name,
        endpoint=approval.endpoint,
        method=approval.method,
        request_body=approval.request_body,
        status=approval.status,
        explanation=approval.explanation,
        reasoning=approval.reasoning,
        wait_until_date=approval.wait_until_date,
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
    )


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalOut)
async def reject(approval_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)) -> ApprovalOut:
    result = await session.execute(
        select(PendingApproval).options(selectinload(PendingApproval.nano)).where(PendingApproval.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval already {approval.status}")

    approval.status = "rejected"
    approval.resolved_at = datetime.utcnow()
    await session.commit()

    from gateway.services.approval_service import maybe_complete_run
    await maybe_complete_run(approval, session)

    return ApprovalOut(
        id=approval.id,
        nano_name=approval.nano.name,
        endpoint=approval.endpoint,
        method=approval.method,
        request_body=approval.request_body,
        status=approval.status,
        explanation=approval.explanation,
        reasoning=approval.reasoning,
        wait_until_date=approval.wait_until_date,
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
    )


# --- Run logs ---

@router.get("/logs", response_model=list[RunLogOut])
async def list_logs(
    nano_name: str | None = Query(None),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_async_session),
) -> list[RunLogOut]:
    query = select(RunLog).options(selectinload(RunLog.nano))
    if nano_name:
        query = query.join(Nano).where(Nano.name == nano_name)
    query = query.order_by(RunLog.started_at.desc()).limit(limit)
    result = await session.execute(query)
    logs = result.scalars().all()
    return [
        RunLogOut(
            id=log.id,
            nano_name=log.nano.name,
            trigger=log.trigger,
            started_at=log.started_at,
            finished_at=log.finished_at,
            status=log.status,
            exit_code=log.exit_code,
            log_file_path=log.log_file_path,
        )
        for log in logs
    ]
