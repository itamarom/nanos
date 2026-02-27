from __future__ import annotations

import base64
import json
import logging
from email.mime.text import MIMEText
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential
from gateway.schemas import ServiceTestEntry

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "gmail")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("Gmail credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _get_gmail_service(credentials: dict[str, Any]) -> Resource:  # type: ignore[no-any-unimported]
    """Build an authenticated Gmail API service.

    Supports two credential types:
    - ``type: "oauth2"`` — OAuth2 client credentials with refresh_token
    - Otherwise — service account with domain-wide delegation (legacy)
    """
    if credentials.get("type") == "oauth2":
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=credentials["refresh_token"],
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
        )
    else:
        delegated_user = credentials.pop("delegated_user", None)
        creds = service_account.Credentials.from_service_account_info(
            credentials, scopes=SCOPES
        )
        if delegated_user:
            creds = creds.with_subject(delegated_user)

    return build("gmail", "v1", credentials=creds)


def _extract_body_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Recursively extract text/plain and text/html from a MIME payload.

    Returns (plain_text, html_text).
    """
    plain = ""
    html = ""
    mime = payload.get("mimeType", "")

    # Leaf part with data
    data = payload.get("body", {}).get("data")
    if data:
        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            return decoded, ""
        if mime == "text/html":
            return "", decoded

    # Recurse into sub-parts
    for part in payload.get("parts", []):
        p, h = _extract_body_parts(part)
        if p and not plain:
            plain = p
        if h and not html:
            html = h
        if plain and html:
            break

    return plain, html


def _html_to_text(html: str) -> str:
    """Crude HTML-to-text: strip tags and decode entities."""
    import re
    text = re.sub(r'<br\s*/?\s*>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|tr|li|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common entities
    for entity, char in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                          ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')]:
        text = text.replace(entity, char)
    # Collapse whitespace but keep newlines
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract useful fields from a Gmail API message resource."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

    payload = msg.get("payload", {})
    plain, html = _extract_body_parts(payload)
    body = plain if plain else _html_to_text(html) if html else ""

    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "body": body,
    }


async def list_messages(query: str, session: AsyncSession, max_results: int = 20) -> list[dict[str, Any]]:
    """Search and list emails matching a query, with pagination."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    all_refs: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(all_refs) < max_results:
        page_size = min(100, max_results - len(all_refs))
        kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": page_size}
        if page_token:
            kwargs["pageToken"] = page_token
        results = service.users().messages().list(**kwargs).execute()

        refs = results.get("messages", [])
        if not refs:
            break
        all_refs.extend(refs)

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    parsed = []
    for msg_ref in all_refs:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        parsed.append(_parse_message(msg))

    return parsed


async def get_profile(session: AsyncSession) -> dict[str, Any]:
    """Get the authenticated user's Gmail profile."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    profile = service.users().getProfile(userId="me").execute()
    return {
        "email_address": profile.get("emailAddress", ""),
        "messages_total": profile.get("messagesTotal", 0),
        "threads_total": profile.get("threadsTotal", 0),
        "history_id": profile.get("historyId", ""),
    }


async def get_message(message_id: str, session: AsyncSession) -> dict[str, Any]:
    """Get full email content by message ID."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    return _parse_message(msg)


async def get_thread(thread_id: str, session: AsyncSession) -> dict[str, Any]:
    """Get an email thread by thread ID."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()

    return {
        "id": thread["id"],
        "messages": [_parse_message(msg) for msg in thread.get("messages", [])],
    }


async def send_message(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    """Send an email. Called by approval_service after approval."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    message = MIMEText(body["body"])
    message["to"] = body["to"]
    message["subject"] = body["subject"]
    if body.get("cc"):
        message["cc"] = body["cc"]
    if body.get("bcc"):
        message["bcc"] = body["bcc"]

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    return {"id": sent["id"], "thread_id": sent.get("threadId", "")}


async def reply_to_message(message_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    """Reply to an existing email. Called by approval_service after approval."""
    credentials = await _get_credentials(session)
    service = _get_gmail_service(credentials)

    # Get the original message to extract headers for threading
    original = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Subject", "From", "To", "Message-ID", "References"],
    ).execute()

    headers = {h["name"].lower(): h["value"] for h in original.get("payload", {}).get("headers", [])}
    thread_id = original.get("threadId", "")

    reply = MIMEText(body["body"])
    reply["to"] = headers.get("from", "")
    reply["subject"] = "Re: " + headers.get("subject", "")
    reply["In-Reply-To"] = headers.get("message-id", "")
    reply["References"] = headers.get("references", "") + " " + headers.get("message-id", "")

    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread_id}
    ).execute()

    return {"id": sent["id"], "thread_id": sent.get("threadId", "")}


async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Run tests for Gmail integration."""
    tests: list[ServiceTestEntry] = []

    # Test 1: Check credentials exist
    try:
        credentials = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="gmail_credentials", success=True, detail="Credentials found"))
    except ValueError as e:
        tests.append(ServiceTestEntry(name="gmail_credentials", success=False, detail=str(e)))
        return tests

    # Test 2: Try listing messages (minimal query)
    try:
        service = _get_gmail_service(credentials)
        results = service.users().messages().list(
            userId="me", maxResults=1
        ).execute()
        count = results.get("resultSizeEstimate", 0)
        tests.append(ServiceTestEntry(
            name="gmail_list_messages", success=True,
            detail=f"Can access mailbox (approx {count} messages)",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="gmail_list_messages", success=False, detail=str(e)))

    return tests
