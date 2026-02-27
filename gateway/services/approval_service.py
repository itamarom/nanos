from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Any

from shared.models import PendingApproval, Nano, RunLog
from gateway.config import SENSITIVE_ENDPOINTS, APPROVAL_BATCH_WINDOW_SECONDS
from gateway.schemas import ApprovalStatusOut

# ---------------------------------------------------------------------------
# Approval handler type + registry
# ---------------------------------------------------------------------------

ApprovalHandler = Callable[[dict[str, Any], AsyncSession], Awaitable[dict[str, Any] | None]]


# --- Calendar handlers ---

async def _exec_calendar_events_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.google_calendar_service import create_event
    return await create_event(body, session)


async def _exec_calendar_events_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.google_calendar_service import update_event
    event_id = body.pop("event_id", None)
    return await update_event(event_id, body, session)


async def _exec_calendar_events_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.google_calendar_service import delete_event
    return await delete_event(body["event_id"], session)


# --- Gmail handlers ---

async def _exec_gmail_messages_send(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.gmail_service import send_message
    return await send_message(body, session)


async def _exec_gmail_messages_reply(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.gmail_service import reply_to_message
    message_id = body.pop("message_id", None)
    return await reply_to_message(message_id, body, session)


# --- HubSpot handlers ---

async def _exec_hubspot_contacts_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import create_contact
    return await create_contact(body.get("properties", body), session)


async def _exec_hubspot_contacts_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import update_contact
    contact_id = body.pop("contact_id", None)
    return await update_contact(contact_id, body.get("properties", body), session)


async def _exec_hubspot_contacts_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import delete_contact
    await delete_contact(body["contact_id"], session)
    return None


async def _exec_hubspot_deals_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import create_deal
    return await create_deal(body.get("properties", body), session)


async def _exec_hubspot_deals_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import update_deal
    deal_id = body.pop("deal_id", None)
    return await update_deal(deal_id, body.get("properties", body), session)


async def _exec_hubspot_deals_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import delete_deal
    await delete_deal(body["deal_id"], session)
    return None


async def _exec_hubspot_tasks_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import create_task
    return await create_task(body.get("properties", body), session)


async def _exec_hubspot_tasks_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import update_task
    task_id = body.pop("task_id", None)
    return await update_task(task_id, body.get("properties", body), session)


async def _exec_hubspot_tasks_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.hubspot_service import delete_task
    await delete_task(body["task_id"], session)
    return None


# --- WhatsApp handlers ---

async def _exec_whatsapp_messages_send_text(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.whatsapp_service import send_text
    result: dict[str, Any] | None = await send_text(body["to"], body["message"], session)
    return result


async def _exec_whatsapp_messages_send_file(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.whatsapp_service import send_file
    result: dict[str, Any] | None = await send_file(body["to"], body["file_path"], body.get("caption"), session)
    return result


# --- Notion handlers ---

async def _exec_notion_pages_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import create_page
    # Notion rejects explicit null for optional fields — strip them
    clean = {k: v for k, v in body.items() if v is not None}
    return await create_page(clean, session)


async def _exec_notion_pages_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import update_page
    page_id = body.pop("page_id", "")
    return await update_page(page_id, body, session)


async def _exec_notion_pages_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import delete_page
    return await delete_page(body["page_id"], session)


async def _exec_notion_blocks_append(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import append_blocks
    return await append_blocks(body["block_id"], body.get("children", []), session)


async def _exec_notion_blocks_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import update_block
    block_id = body.pop("block_id", "")
    return await update_block(block_id, body, session)


async def _exec_notion_blocks_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import delete_block
    return await delete_block(body["block_id"], session)


async def _exec_notion_comments_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.notion_service import create_comment
    return await create_comment(body, session)


# --- Linear handlers ---

async def _exec_linear_issues_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import create_issue
    return await create_issue(body, session)


async def _exec_linear_issues_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import update_issue
    issue_id = body.pop("issue_id", "")
    return await update_issue(issue_id, body, session)


async def _exec_linear_issues_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import delete_issue
    return await delete_issue(body["issue_id"], session)


async def _exec_linear_comments_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import create_comment as linear_create_comment
    return await linear_create_comment(body, session)


async def _exec_linear_comments_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import update_comment as linear_update_comment
    comment_id = body.pop("comment_id", "")
    return await linear_update_comment(comment_id, body, session)


async def _exec_linear_comments_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.services.linear_service import delete_comment as linear_delete_comment
    return await linear_delete_comment(body["comment_id"], session)


# --- Nano management handlers ---

async def _exec_nano_types_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _create_nano_type
    return await _create_nano_type(
        body["name"], body["description"],
        body["script_code"], body["permissions"],
        body.get("schedule"),
    )


async def _exec_nano_types_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _update_nano_type
    return await _update_nano_type(body)


async def _exec_nano_types_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _delete_nano_type
    return await _delete_nano_type(body["name"], session)


async def _exec_nanos_run_once(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _run_nano
    return await _run_nano(body["name"], session)


async def _exec_nanos_create(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _create_nano
    return await _create_nano(body["name"], body["permissions"], session)


async def _exec_nanos_update(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _update_nano
    return await _update_nano(body, session)


async def _exec_nanos_delete(body: dict[str, Any], session: AsyncSession) -> dict[str, Any] | None:
    from gateway.routers.chat_admin import _delete_nano
    return await _delete_nano(body["name"], session)


# ---------------------------------------------------------------------------
# Registry: every sensitive endpoint must have exactly one handler
# ---------------------------------------------------------------------------

APPROVAL_HANDLERS: dict[str, ApprovalHandler] = {
    "calendar.events.create": _exec_calendar_events_create,
    "calendar.events.update": _exec_calendar_events_update,
    "calendar.events.delete": _exec_calendar_events_delete,
    "gmail.messages.send": _exec_gmail_messages_send,
    "gmail.messages.reply": _exec_gmail_messages_reply,
    "hubspot.contacts.create": _exec_hubspot_contacts_create,
    "hubspot.contacts.update": _exec_hubspot_contacts_update,
    "hubspot.contacts.delete": _exec_hubspot_contacts_delete,
    "hubspot.deals.create": _exec_hubspot_deals_create,
    "hubspot.deals.update": _exec_hubspot_deals_update,
    "hubspot.deals.delete": _exec_hubspot_deals_delete,
    "hubspot.tasks.create": _exec_hubspot_tasks_create,
    "hubspot.tasks.update": _exec_hubspot_tasks_update,
    "hubspot.tasks.delete": _exec_hubspot_tasks_delete,
    "whatsapp.messages.send_text": _exec_whatsapp_messages_send_text,
    "whatsapp.messages.send_file": _exec_whatsapp_messages_send_file,
    "notion.pages.create": _exec_notion_pages_create,
    "notion.pages.update": _exec_notion_pages_update,
    "notion.pages.delete": _exec_notion_pages_delete,
    "notion.blocks.append": _exec_notion_blocks_append,
    "notion.blocks.update": _exec_notion_blocks_update,
    "notion.blocks.delete": _exec_notion_blocks_delete,
    "notion.comments.create": _exec_notion_comments_create,
    "linear.issues.create": _exec_linear_issues_create,
    "linear.issues.update": _exec_linear_issues_update,
    "linear.issues.delete": _exec_linear_issues_delete,
    "linear.comments.create": _exec_linear_comments_create,
    "linear.comments.update": _exec_linear_comments_update,
    "linear.comments.delete": _exec_linear_comments_delete,
    "nano_types.create": _exec_nano_types_create,
    "nano_types.update": _exec_nano_types_update,
    "nano_types.delete": _exec_nano_types_delete,
    "nanos.run_once": _exec_nanos_run_once,
    "nanos.create": _exec_nanos_create,
    "nanos.update": _exec_nanos_update,
    "nanos.delete": _exec_nanos_delete,
}

# Gateway won't start if these diverge
_missing_handlers = SENSITIVE_ENDPOINTS - set(APPROVAL_HANDLERS)
_extra_handlers = set(APPROVAL_HANDLERS) - SENSITIVE_ENDPOINTS
assert not _missing_handlers and not _extra_handlers, (
    f"APPROVAL_HANDLERS / SENSITIVE_ENDPOINTS mismatch: "
    f"missing handlers={_missing_handlers}, extra handlers={_extra_handlers}"
)


def is_sensitive(endpoint: str) -> bool:
    return endpoint in SENSITIVE_ENDPOINTS


async def create_approval(
    nano: Nano,
    endpoint: str,
    method: str,
    request_body: dict[str, Any] | None,
    session: AsyncSession,
    run_log_id: str | None = None,
    explanation: str | None = None,
    reasoning: str | None = None,
    wait_until_date: datetime | None = None,
    draft_mode: bool = False,
) -> PendingApproval:
    """Create a pending approval and optionally batch with recent ones.

    If draft_mode is True, the approval is created with status="draft"
    instead of "pending". No Slack notification is sent and the run
    will not block waiting for approval resolution.
    """
    if draft_mode:
        # Draft mode: log the approval with full params but don't send
        # to Slack or block the run.
        approval = PendingApproval(
            nano_id=nano.id,
            endpoint=endpoint,
            method=method,
            request_body=json.dumps(request_body, ensure_ascii=False) if request_body else None,
            run_log_id=uuid.UUID(run_log_id) if run_log_id else None,
            explanation=explanation,
            reasoning=reasoning,
            wait_until_date=wait_until_date,
            status="draft",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        return approval

    # Check for recent pending approvals from same nano to batch
    cutoff = datetime.utcnow() - timedelta(seconds=APPROVAL_BATCH_WINDOW_SECONDS)
    result = await session.execute(
        select(PendingApproval).where(
            PendingApproval.nano_id == nano.id,
            PendingApproval.status == "pending",
            PendingApproval.created_at >= cutoff,
            PendingApproval.batch_id.is_not(None),
        ).order_by(PendingApproval.created_at.desc()).limit(1)
    )
    recent = result.scalar_one_or_none()
    batch_id = recent.batch_id if recent else str(uuid.uuid4())[:8]

    approval = PendingApproval(
        nano_id=nano.id,
        batch_id=batch_id,
        endpoint=endpoint,
        method=method,
        request_body=json.dumps(request_body, ensure_ascii=False) if request_body else None,
        run_log_id=uuid.UUID(run_log_id) if run_log_id else None,
        explanation=explanation,
        reasoning=reasoning,
        wait_until_date=wait_until_date,
    )
    session.add(approval)
    await session.commit()
    await session.refresh(approval)

    # Send Slack approval notification
    from gateway.services.slackbot_service import send_approval_request
    await send_approval_request(approval, nano, session)

    return approval


async def execute_approved_action(approval: PendingApproval, session: AsyncSession) -> None:
    """Execute an approved action by calling the appropriate service."""
    try:
        request_body = json.loads(approval.request_body) if approval.request_body else {}
        handler = APPROVAL_HANDLERS.get(approval.endpoint)
        if handler is None:
            raise ValueError(f"No handler for endpoint: {approval.endpoint}")
        result = await handler(request_body, session)

        approval.status = "executed"
        approval.response_body = json.dumps(result, default=str, ensure_ascii=False) if result else None
    except Exception as e:
        approval.status = "failed"
        approval.response_body = json.dumps({"error": str(e)}, ensure_ascii=False)

    approval.resolved_at = datetime.utcnow()
    await session.commit()

    await maybe_complete_run(approval, session)


async def maybe_complete_run(approval: PendingApproval, session: AsyncSession) -> None:
    """After an approval is resolved, check if the parent run can transition to success."""
    if not approval.run_log_id:
        return

    # Count remaining pending approvals for this run
    result = await session.execute(
        select(PendingApproval).where(
            PendingApproval.run_log_id == approval.run_log_id,
            PendingApproval.status == "pending",
        )
    )
    if result.scalars().first() is not None:
        return  # still has pending approvals

    # All resolved — transition run from awaiting_approval to success
    log_result = await session.execute(
        select(RunLog).where(RunLog.id == approval.run_log_id)
    )
    run_log = log_result.scalar_one_or_none()
    if run_log and run_log.status == "awaiting_approval":
        run_log.status = "success"
        run_log.finished_at = datetime.utcnow()
        await session.commit()


async def get_approval_status(approval_id: uuid.UUID, session: AsyncSession) -> ApprovalStatusOut | None:
    result = await session.execute(
        select(PendingApproval).where(PendingApproval.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if not approval:
        return None
    return ApprovalStatusOut(
        id=approval.id,
        status=approval.status,
        response_body=approval.response_body,
    )
