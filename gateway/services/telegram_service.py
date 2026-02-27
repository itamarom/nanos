from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential, PendingApproval, Nano

logger = logging.getLogger(__name__)

# Telegram bot instance (initialized lazily)
_bot_app: Any = None


async def _get_telegram_config(session: AsyncSession) -> dict[str, Any] | None:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "telegram")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return None
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


async def send_approval_request(approval: PendingApproval, nano: Nano, session: AsyncSession) -> None:
    """Send a Telegram message with approve/reject buttons for a pending approval."""
    config = await _get_telegram_config(session)
    if not config:
        logger.warning("Telegram credentials not configured — skipping approval notification")
        return

    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

        bot = Bot(token=config["token"])
        chat_id = config["chat_id"]

        request_preview = ""
        if approval.request_body:
            try:
                body = json.loads(approval.request_body)
                request_preview = f"\n\n```json\n{json.dumps(body, indent=2)[:500]}\n```"
            except (json.JSONDecodeError, TypeError):
                request_preview = f"\n\nBody: {str(approval.request_body)[:500]}"

        text = (
            f"🔐 **Approval Required**\n\n"
            f"**Nano:** {nano.name}\n"
            f"**Action:** {approval.endpoint}\n"
            f"**Method:** {approval.method}"
            f"{request_preview}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{approval.id}"),
            ]
        ])

        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        approval.telegram_message_id = message.message_id
        await session.commit()

    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


async def start_telegram_bot(session_factory):
    """Start the Telegram bot for handling approval callbacks. Run as background task."""
    from telegram.ext import Application, CallbackQueryHandler

    # Get config using a fresh session
    async with session_factory() as session:
        config = await _get_telegram_config(session)

    if not config:
        logger.warning("Telegram credentials not configured — bot not started")
        return

    async def handle_callback(update, context):
        query = update.callback_query
        await query.answer()

        data = query.data
        action, approval_id = data.split(":", 1)

        async with session_factory() as session:
            result = await session.execute(
                select(PendingApproval).where(PendingApproval.id == approval_id)
            )
            approval = result.scalar_one_or_none()

            if not approval:
                await query.edit_message_text("Approval not found.")
                return

            if approval.status != "pending":
                await query.edit_message_text(f"Already {approval.status}.")
                return

            if action == "approve":
                approval.status = "approved"
                await session.commit()

                from gateway.services.approval_service import execute_approved_action
                await execute_approved_action(approval, session)
                await query.edit_message_text(f"✅ Approved and executed ({approval.endpoint})")
            elif action == "reject":
                approval.status = "rejected"
                await session.commit()
                await query.edit_message_text(f"❌ Rejected ({approval.endpoint})")

    app = Application.builder().token(config["token"]).build()
    app.add_handler(CallbackQueryHandler(handle_callback))

    global _bot_app
    _bot_app = app

    logger.info("Starting Telegram approval bot...")
    await app.initialize()
    await app.start()
    if app.updater is None:
        raise RuntimeError("Telegram Application has no updater — cannot start polling")
    await app.updater.start_polling()
