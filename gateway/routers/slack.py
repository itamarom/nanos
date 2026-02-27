"""FastAPI router for Slack endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_async_session
from shared.models import Nano
from gateway.auth import get_current_nano, check_permission
from gateway.schemas import SlackSendRequest, SlackSendResponse

router = APIRouter()


@router.post("/send", response_model=SlackSendResponse)
async def send_message(
    body: SlackSendRequest,
    nano: Nano = Depends(get_current_nano),
    session: AsyncSession = Depends(get_async_session),
) -> SlackSendResponse:
    """Post a message to Slack via webhook (NOT sensitive -- called directly)."""
    check_permission(nano, "slack.send_message")

    from gateway.services.slack_service import send_message as svc_send_message

    result = await svc_send_message(body.text, session)
    return SlackSendResponse(**result)
