"""Slack API service — sends messages via the Slack Bot (bot_token + channel_id)."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential
from gateway.schemas import ServiceTestEntry

logger = logging.getLogger(__name__)


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    """Load Slack Bot credentials from the database."""
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "slackbot")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("Slack Bot credentials not configured — run: add-credential slackbot")
    from gateway.crypto import decrypt_json
    data = decrypt_json(cred.credentials)
    if not data.get("bot_token"):
        raise ValueError("Slack Bot credentials missing bot_token")
    if not data.get("channel_id"):
        raise ValueError("Slack Bot credentials missing channel_id")
    return data


async def send_message(text: str, session: AsyncSession) -> dict[str, Any]:
    """Post a message to Slack via chat.postMessage using the bot token."""
    creds = await _get_credentials(session)

    from slack_sdk.web.async_client import AsyncWebClient

    client = AsyncWebClient(token=creds["bot_token"])
    result = await client.chat_postMessage(
        channel=creds["channel_id"],
        text=text,
    )
    return {"ok": result.get("ok", False)}


async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Test Slack Bot connectivity."""
    tests: list[ServiceTestEntry] = []

    try:
        creds = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="load_credentials", success=True, detail="Credentials found"))
    except Exception as e:
        tests.append(ServiceTestEntry(name="load_credentials", success=False, detail=str(e)))
        return tests

    try:
        result = await send_message("Nanos Slack test", session)
        ok = result.get("ok", False)
        tests.append(ServiceTestEntry(
            name="send_message", success=ok,
            detail="Message sent" if ok else "Send failed",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="send_message", success=False, detail=str(e)))

    return tests
