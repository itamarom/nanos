import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano
from gateway.schemas import ApprovalStatusOut, HealthResponse, TestResult, ServiceTestEntry

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(session: AsyncSession = Depends(get_async_session)) -> HealthResponse:
    services = {}

    # Check DB
    try:
        await session.execute(text("SELECT 1"))
        services["database"] = "ok"
    except Exception as e:
        services["database"] = f"error: {e}"

    overall = "ok" if all(v == "ok" for v in services.values()) else "degraded"
    return HealthResponse(status=overall, services=services)


@router.get("/approvals/{approval_id}/status", response_model=ApprovalStatusOut)
async def approval_status(
    approval_id: uuid.UUID,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> ApprovalStatusOut:
    """Check the status of a pending approval."""
    from gateway.services.approval_service import get_approval_status

    result = await get_approval_status(approval_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return result


@router.post("/test/{api_name}", response_model=TestResult)
async def test_api(api_name: str, session: AsyncSession = Depends(get_async_session)) -> TestResult:
    """Run test_all() for an API module."""
    from gateway.services import openai_service, google_calendar_service, gmail_service, slack_service, hubspot_service, whatsapp_service, notion_service, linear_service

    service_map = {
        "openai": openai_service,
        "calendar": google_calendar_service,
        "gmail": gmail_service,
        "slack": slack_service,
        "hubspot": hubspot_service,
        "whatsapp": whatsapp_service,
        "notion": notion_service,
        "linear": linear_service,
    }

    if api_name == "all":
        all_tests = []
        overall_success = True
        for name, svc in service_map.items():
            result = await svc.test_all(session)
            all_tests.extend(result)
            if any(not t.get("success") for t in result):
                overall_success = False
        return TestResult(api_name="all", success=overall_success, tests=all_tests)

    matched_svc = service_map.get(api_name)
    if matched_svc is None:
        return TestResult(
            api_name=api_name,
            success=False,
            tests=[ServiceTestEntry(name="unknown", success=False, detail=f"Unknown API: {api_name}")],
        )

    tests = await matched_svc.test_all(session)
    success = all(t.get("success", False) for t in tests)
    return TestResult(api_name=api_name, success=success, tests=tests)
