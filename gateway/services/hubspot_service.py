"""HubSpot CRM service — Contacts, Deals, Tasks (including custom properties).

Uses the HubSpot CRM API v3: https://developers.hubspot.com/docs/api/crm
Authentication via private app access token (Bearer).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ApiCredential
from gateway.schemas import ServiceTestEntry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "hubspot")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("HubSpot credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _headers(credentials: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credentials['access_token']}",
        "Content-Type": "application/json",
    }


async def _crm_request(
    method: str,
    path: str,
    credentials: dict[str, Any],
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """Make a request to the HubSpot CRM API."""
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url, headers=_headers(credentials),
            json=json_body, params=params,
        )
    if resp.status_code == 204:
        return None
    data: dict[str, Any] | list[Any] = resp.json()
    if resp.status_code >= 400:
        assert isinstance(data, dict)
        msg = data.get("message", resp.text)
        raise Exception(f"HubSpot API error {resp.status_code}: {msg}")
    return data


def _parse_object(obj: dict[str, Any]) -> dict[str, Any]:
    """Flatten a HubSpot CRM object into id + properties dict."""
    return {
        "id": obj["id"],
        **obj.get("properties", {}),
    }


# ------------------------------------------------------------------ #
# Contacts
# ------------------------------------------------------------------ #

async def list_contacts(
    session: AsyncSession,
    limit: int = 20,
    after: str | None = None,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {"limit": limit}
    if after:
        params["after"] = after
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", "/crm/v3/objects/contacts", credentials, params=params)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"results": results, "paging": data.get("paging")}


async def get_contact(
    contact_id: str,
    session: AsyncSession,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", f"/crm/v3/objects/contacts/{contact_id}", credentials, params=params)
    assert isinstance(data, dict)
    return _parse_object(data)


async def create_contact(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("POST", "/crm/v3/objects/contacts", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def update_contact(contact_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("PATCH", f"/crm/v3/objects/contacts/{contact_id}", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def delete_contact(contact_id: str, session: AsyncSession) -> None:
    credentials = await _get_credentials(session)
    await _crm_request("DELETE", f"/crm/v3/objects/contacts/{contact_id}", credentials)


async def search_contacts(filters: list[dict[str, Any]], properties: list[str] | None, limit: int, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    body: dict[str, Any] = {"filterGroups": [{"filters": filters}], "limit": limit}
    if properties:
        body["properties"] = properties
    data = await _crm_request("POST", "/crm/v3/objects/contacts/search", credentials, json_body=body)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"total": data.get("total", 0), "results": results}


# ------------------------------------------------------------------ #
# Deals
# ------------------------------------------------------------------ #

async def list_deals(
    session: AsyncSession,
    limit: int = 20,
    after: str | None = None,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {"limit": limit}
    if after:
        params["after"] = after
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", "/crm/v3/objects/deals", credentials, params=params)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"results": results, "paging": data.get("paging")}


async def get_deal(
    deal_id: str,
    session: AsyncSession,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", f"/crm/v3/objects/deals/{deal_id}", credentials, params=params)
    assert isinstance(data, dict)
    return _parse_object(data)


async def create_deal(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("POST", "/crm/v3/objects/deals", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def update_deal(deal_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("PATCH", f"/crm/v3/objects/deals/{deal_id}", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def delete_deal(deal_id: str, session: AsyncSession) -> None:
    credentials = await _get_credentials(session)
    await _crm_request("DELETE", f"/crm/v3/objects/deals/{deal_id}", credentials)


async def search_deals(filters: list[dict[str, Any]], properties: list[str] | None, limit: int, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    body: dict[str, Any] = {"filterGroups": [{"filters": filters}], "limit": limit}
    if properties:
        body["properties"] = properties
    data = await _crm_request("POST", "/crm/v3/objects/deals/search", credentials, json_body=body)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"total": data.get("total", 0), "results": results}


# ------------------------------------------------------------------ #
# Tasks
# ------------------------------------------------------------------ #

async def list_tasks(
    session: AsyncSession,
    limit: int = 20,
    after: str | None = None,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {"limit": limit}
    if after:
        params["after"] = after
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", "/crm/v3/objects/tasks", credentials, params=params)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"results": results, "paging": data.get("paging")}


async def get_task(
    task_id: str,
    session: AsyncSession,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if properties:
        params["properties"] = ",".join(properties)
    data = await _crm_request("GET", f"/crm/v3/objects/tasks/{task_id}", credentials, params=params)
    assert isinstance(data, dict)
    return _parse_object(data)


async def create_task(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("POST", "/crm/v3/objects/tasks", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def update_task(task_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _crm_request("PATCH", f"/crm/v3/objects/tasks/{task_id}", credentials, json_body={"properties": body})
    assert isinstance(data, dict)
    return _parse_object(data)


async def delete_task(task_id: str, session: AsyncSession) -> None:
    credentials = await _get_credentials(session)
    await _crm_request("DELETE", f"/crm/v3/objects/tasks/{task_id}", credentials)


async def search_tasks(filters: list[dict[str, Any]], properties: list[str] | None, limit: int, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    body: dict[str, Any] = {"filterGroups": [{"filters": filters}], "limit": limit}
    if properties:
        body["properties"] = properties
    data = await _crm_request("POST", "/crm/v3/objects/tasks/search", credentials, json_body=body)
    assert isinstance(data, dict)
    results = [_parse_object(o) for o in data.get("results", [])]
    return {"total": data.get("total", 0), "results": results}


# ------------------------------------------------------------------ #
# Properties (schema / metadata)
# ------------------------------------------------------------------ #

async def list_properties(
    object_type: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """List all property definitions for a CRM object type."""
    credentials = await _get_credentials(session)
    data = await _crm_request("GET", f"/crm/v3/properties/{object_type}", credentials)
    assert isinstance(data, dict)
    results: list[dict[str, Any]] = data.get("results", [])
    return results


async def get_property(
    object_type: str,
    property_name: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """Get a single property definition (name, type, options, etc.)."""
    credentials = await _get_credentials(session)
    data = await _crm_request("GET", f"/crm/v3/properties/{object_type}/{property_name}", credentials)
    assert isinstance(data, dict)
    return data


# ------------------------------------------------------------------ #
# Test
# ------------------------------------------------------------------ #

async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Run connectivity tests for HubSpot."""
    tests: list[ServiceTestEntry] = []

    try:
        credentials = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="hubspot_credentials", success=True, detail="Credentials found"))
    except ValueError as e:
        tests.append(ServiceTestEntry(name="hubspot_credentials", success=False, detail=str(e)))
        return tests

    try:
        data = await _crm_request("GET", "/crm/v3/objects/contacts", credentials, params={"limit": 1})
        assert isinstance(data, dict)
        count = data.get("total", len(data.get("results", [])))
        tests.append(ServiceTestEntry(
            name="hubspot_list_contacts", success=True,
            detail=f"Can access CRM ({count} contacts)",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="hubspot_list_contacts", success=False, detail=str(e)))

    return tests
