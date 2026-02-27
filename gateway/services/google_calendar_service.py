"""Google Calendar API service using service account with domain-wide delegation."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential
from gateway.schemas import ServiceTestEntry

logger = logging.getLogger(__name__)

CALENDAR_ID = "primary"


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    """Load Google Calendar credentials from the database."""
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "google-calendar")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("Google Calendar credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _build_service(cred_data: dict[str, Any]) -> Resource:  # type: ignore[no-any-unimported]
    """Build a Google Calendar API service client.

    Supports two credential types:
    - ``type: "oauth2"`` — OAuth2 client credentials with refresh_token
    - Otherwise — service account with domain-wide delegation (legacy)
    """
    from googleapiclient.discovery import build

    if cred_data.get("type") == "oauth2":
        from google.oauth2.credentials import Credentials as OAuth2Credentials

        credentials = OAuth2Credentials(
            token=None,
            refresh_token=cred_data["refresh_token"],
            client_id=cred_data["client_id"],
            client_secret=cred_data["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
        )
    else:
        from google.oauth2.service_account import Credentials as SACredentials

        delegated_user = cred_data.pop("delegated_user", None)
        credentials = SACredentials.from_service_account_info(
            cred_data,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        if delegated_user:
            credentials = credentials.with_subject(delegated_user)

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


async def list_events(start: str, end: str, session: AsyncSession) -> list[dict[str, Any]]:
    """List calendar events between *start* and *end* (RFC 3339 timestamps)."""
    cred_data = await _get_credentials(session)
    service = _build_service(cred_data)

    events_result = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = events_result.get("items", [])
    return [_format_event(e) for e in events]


def _format_event(e: dict[str, Any]) -> dict[str, Any]:
    """Extract all useful fields from a Google Calendar API event."""
    # Conference / video call link
    conference_link = e.get("hangoutLink", "")
    conf_data = e.get("conferenceData")
    if conf_data and not conference_link:
        for ep in conf_data.get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                conference_link = ep.get("uri", "")
                break

    # Attendees with response status
    attendees = []
    for a in e.get("attendees", []):
        att = {"email": a.get("email", "")}
        if a.get("displayName"):
            att["name"] = a["displayName"]
        if a.get("responseStatus"):
            att["response"] = a["responseStatus"]
        if a.get("self"):
            att["self"] = True
        if a.get("organizer"):
            att["organizer"] = True
        attendees.append(att)

    result = {
        "id": e.get("id"),
        "summary": e.get("summary", ""),
        "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
        "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
        "description": e.get("description", ""),
        "attendees": attendees,
        "location": e.get("location", ""),
        "status": e.get("status", ""),
        "html_link": e.get("htmlLink", ""),
        "conference_link": conference_link,
        "organizer": e.get("organizer", {}).get("email", ""),
        "creator": e.get("creator", {}).get("email", ""),
        "visibility": e.get("visibility", "default"),
        "transparency": e.get("transparency", "opaque"),
        "color_id": e.get("colorId", ""),
        "recurring_event_id": e.get("recurringEventId", ""),
        "recurrence": e.get("recurrence", []),
        "reminders": e.get("reminders", {}),
        "updated": e.get("updated", ""),
    }

    return result


async def create_event(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    """Create a calendar event. Called by approval_service after approval."""
    cred_data = await _get_credentials(session)
    service = _build_service(cred_data)

    # Build the event resource from the request body
    event_resource = {
        "summary": body.get("summary", ""),
        "description": body.get("description", ""),
        "location": body.get("location", ""),
        "start": {"dateTime": body["start"], "timeZone": body.get("timeZone", "UTC")},
        "end": {"dateTime": body["end"], "timeZone": body.get("timeZone", "UTC")},
    }

    attendees = body.get("attendees", [])
    if attendees:
        event_resource["attendees"] = [{"email": email} for email in attendees]

    created = service.events().insert(calendarId=CALENDAR_ID, body=event_resource).execute()

    return _format_event(created)


async def update_event(event_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    """Update a calendar event by ID."""
    cred_data = await _get_credentials(session)
    service = _build_service(cred_data)

    # Fetch existing event first
    existing = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()

    # Apply updates
    if "summary" in body:
        existing["summary"] = body["summary"]
    if "description" in body:
        existing["description"] = body["description"]
    if "location" in body:
        existing["location"] = body["location"]
    if "start" in body:
        existing["start"] = {"dateTime": body["start"], "timeZone": body.get("timeZone", "UTC")}
    if "end" in body:
        existing["end"] = {"dateTime": body["end"], "timeZone": body.get("timeZone", "UTC")}
    if "attendees" in body:
        existing["attendees"] = [{"email": email} for email in body["attendees"]]

    updated = (
        service.events()
        .update(calendarId=CALENDAR_ID, eventId=event_id, body=existing)
        .execute()
    )

    return _format_event(updated)


async def delete_event(event_id: str, session: AsyncSession) -> dict[str, Any]:
    """Delete a calendar event by ID."""
    cred_data = await _get_credentials(session)
    service = _build_service(cred_data)

    service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()

    return {"deleted": True, "event_id": event_id}


async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Test credential loading and list today's events."""
    tests: list[ServiceTestEntry] = []

    # Test 1: load credentials
    try:
        cred_data = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="load_credentials", success=True, detail="Credentials found"))
    except Exception as e:
        tests.append(ServiceTestEntry(name="load_credentials", success=False, detail=str(e)))
        return tests

    # Test 2: build service and list today's events
    try:
        service = _build_service(cred_data)
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=start_of_day,
                timeMax=end_of_day,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        event_count = len(events_result.get("items", []))
        tests.append(ServiceTestEntry(
            name="list_today_events", success=True,
            detail=f"Found {event_count} events today",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="list_today_events", success=False, detail=str(e)))

    return tests
