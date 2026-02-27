from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential, PendingApproval, Nano

logger = logging.getLogger(__name__)

# Socket mode client reference for shutdown
_socket_client: Any = None


async def _get_slackbot_config(session: AsyncSession) -> dict[str, Any] | None:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "slackbot")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return None
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


async def send_approval_request(approval: PendingApproval, nano: Nano, session: AsyncSession) -> None:
    """Post a Slack Block Kit message with Approve/Reject buttons."""
    config = await _get_slackbot_config(session)
    if not config:
        logger.warning("Slackbot credentials not configured — skipping approval notification")
        return

    try:
        from slack_sdk.web.async_client import AsyncWebClient

        client = AsyncWebClient(token=config["bot_token"])
        channel = config["channel_id"]

        request_preview = ""
        if approval.request_body:
            try:
                body = json.loads(approval.request_body)
                request_preview = json.dumps(body, indent=2, ensure_ascii=False)[:500]
            except (json.JSONDecodeError, TypeError):
                request_preview = str(approval.request_body)[:500]

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Approval Required", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Nano:*\n{nano.name}"},
                    {"type": "mrkdwn", "text": f"*Action:*\n{approval.endpoint}"},
                    {"type": "mrkdwn", "text": f"*Method:*\n{approval.method}"},
                ],
            },
        ]

        if approval.explanation:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*What:* {approval.explanation}"},
            })
        if approval.reasoning:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Why:* {approval.reasoning}"},
            })
        if approval.wait_until_date:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Wait until:* {approval.wait_until_date.strftime('%Y-%m-%d %H:%M')}"},
            })

        if request_preview:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{request_preview}```"},
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "action_id": "approve_action",
                    "value": str(approval.id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "style": "danger",
                    "action_id": "reject_action",
                    "value": str(approval.id),
                },
            ],
        })

        result = await client.chat_postMessage(
            channel=channel,
            text=f"Approval required: {nano.name} wants to {approval.endpoint}",
            blocks=blocks,
        )

        approval.slack_message_ts = result["ts"]
        await session.commit()

    except Exception as e:
        logger.error(f"Failed to send Slack approval notification: {e}")


async def start_slackbot(session_factory):
    """Start a Slack Socket Mode client for handling approval button clicks."""
    from slack_sdk.web.async_client import AsyncWebClient
    from slack_sdk.socket_mode.async_client import AsyncBaseSocketModeClient
    from slack_sdk.socket_mode.websockets import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.socket_mode.request import SocketModeRequest

    # Get config using a fresh session
    async with session_factory() as session:
        config = await _get_slackbot_config(session)

    if not config:
        logger.warning("Slackbot credentials not configured — bot not started")
        return

    app_token = config.get("app_token")
    bot_token = config.get("bot_token")
    channel_id = config.get("channel_id")

    if not app_token or not bot_token:
        logger.warning("Slackbot app_token or bot_token missing — bot not started")
        return

    web_client = AsyncWebClient(token=bot_token)

    global _socket_client
    client = SocketModeClient(app_token=app_token)
    _socket_client = client

    async def handle_interactive(client: AsyncBaseSocketModeClient, req: SocketModeRequest) -> None:
        if req.type != "interactive":
            return

        # Acknowledge immediately
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        payload = req.payload
        actions = payload.get("actions", [])
        if not actions:
            return

        action = actions[0]
        action_id = action.get("action_id", "")
        approval_id = action.get("value", "")
        message_channel = payload.get("channel", {}).get("id", channel_id)
        message_ts = payload.get("message", {}).get("ts", "")

        if action_id not in ("approve_action", "reject_action"):
            return

        async with session_factory() as session:
            result = await session.execute(
                select(PendingApproval).where(PendingApproval.id == approval_id)
            )
            approval = result.scalar_one_or_none()

            if not approval:
                await web_client.chat_update(
                    channel=message_channel, ts=message_ts,
                    text="Approval not found.", blocks=[],
                )
                return

            if approval.status != "pending":
                await web_client.chat_update(
                    channel=message_channel, ts=message_ts,
                    text=f"Already {approval.status}.", blocks=[],
                )
                return

            if action_id == "approve_action":
                approval.status = "approved"
                await session.commit()

                from gateway.services.approval_service import execute_approved_action, maybe_complete_run
                await execute_approved_action(approval, session)

                await web_client.chat_update(
                    channel=message_channel, ts=message_ts,
                    text=f"Approved and executed ({approval.endpoint})", blocks=[],
                )
            elif action_id == "reject_action":
                approval.status = "rejected"
                await session.commit()

                from gateway.services.approval_service import maybe_complete_run
                await maybe_complete_run(approval, session)

                await web_client.chat_update(
                    channel=message_channel, ts=message_ts,
                    text=f"Rejected ({approval.endpoint})", blocks=[],
                )

    client.socket_mode_request_listeners.append(handle_interactive)

    logger.info("Starting Slack Bot (Socket Mode)...")
    await client.connect()
