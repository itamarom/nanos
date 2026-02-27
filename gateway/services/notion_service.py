"""Notion API service — Databases, Pages, Blocks, Comments, Users, Search.

Uses the Notion API: https://developers.notion.com/reference
Authentication via internal integration token (Bearer).
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

BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "notion")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("Notion credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _headers(credentials: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credentials['api_token']}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


async def _notion_request(
    method: str,
    path: str,
    credentials: dict[str, Any],
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """Make a request to the Notion API."""
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
        raise Exception(f"Notion API error {resp.status_code}: {msg}")
    return data


# ------------------------------------------------------------------ #
# Search
# ------------------------------------------------------------------ #

async def search(
    session: AsyncSession,
    query: str | None = None,
    filter: dict[str, Any] | None = None,
    sort: dict[str, Any] | None = None,
    page_size: int | None = None,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    body: dict[str, Any] = {}
    if query:
        body["query"] = query
    if filter:
        body["filter"] = filter
    if sort:
        body["sort"] = sort
    if page_size:
        body["page_size"] = page_size
    if start_cursor:
        body["start_cursor"] = start_cursor
    data = await _notion_request("POST", "/search", credentials, json_body=body)
    assert isinstance(data, dict)
    return {
        "results": data.get("results", []),
        "next_cursor": data.get("next_cursor"),
        "has_more": data.get("has_more", False),
    }


# ------------------------------------------------------------------ #
# Databases
# ------------------------------------------------------------------ #

async def get_database(database_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("GET", f"/databases/{database_id}", credentials)
    assert isinstance(data, dict)
    return data


async def query_database(
    database_id: str,
    session: AsyncSession,
    filter: dict[str, Any] | None = None,
    sorts: list[Any] | None = None,
    page_size: int | None = None,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    body: dict[str, Any] = {}
    if filter:
        body["filter"] = filter
    if sorts:
        body["sorts"] = sorts
    if page_size:
        body["page_size"] = page_size
    if start_cursor:
        body["start_cursor"] = start_cursor
    data = await _notion_request("POST", f"/databases/{database_id}/query", credentials, json_body=body)
    assert isinstance(data, dict)
    return {
        "results": data.get("results", []),
        "next_cursor": data.get("next_cursor"),
        "has_more": data.get("has_more", False),
    }


# ------------------------------------------------------------------ #
# Pages
# ------------------------------------------------------------------ #

async def get_page(page_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("GET", f"/pages/{page_id}", credentials)
    assert isinstance(data, dict)
    return data


async def create_page(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("POST", "/pages", credentials, json_body=body)
    assert isinstance(data, dict)
    return data


async def update_page(page_id: str, body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("PATCH", f"/pages/{page_id}", credentials, json_body=body)
    assert isinstance(data, dict)
    return data


async def delete_page(page_id: str, session: AsyncSession) -> dict[str, Any]:
    """Archive a page (Notion's 'delete' is archive)."""
    credentials = await _get_credentials(session)
    data = await _notion_request("PATCH", f"/pages/{page_id}", credentials, json_body={"archived": True})
    assert isinstance(data, dict)
    return data


# ------------------------------------------------------------------ #
# Blocks
# ------------------------------------------------------------------ #

async def list_blocks(
    block_id: str,
    session: AsyncSession,
    page_size: int | None = None,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if page_size:
        params["page_size"] = page_size
    if start_cursor:
        params["start_cursor"] = start_cursor
    data = await _notion_request("GET", f"/blocks/{block_id}/children", credentials, params=params)
    assert isinstance(data, dict)
    return {
        "results": data.get("results", []),
        "next_cursor": data.get("next_cursor"),
        "has_more": data.get("has_more", False),
    }


async def append_blocks(block_id: str, children: list[Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("PATCH", f"/blocks/{block_id}/children", credentials, json_body={"children": children})
    assert isinstance(data, dict)
    return data


async def update_block(block_id: str, block_data: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("PATCH", f"/blocks/{block_id}", credentials, json_body=block_data)
    assert isinstance(data, dict)
    return data


async def delete_block(block_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("DELETE", f"/blocks/{block_id}", credentials)
    assert isinstance(data, dict)
    return data


# ------------------------------------------------------------------ #
# Comments
# ------------------------------------------------------------------ #

async def list_comments(
    session: AsyncSession,
    block_id: str | None = None,
    page_size: int | None = None,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if block_id:
        params["block_id"] = block_id
    if page_size:
        params["page_size"] = page_size
    if start_cursor:
        params["start_cursor"] = start_cursor
    data = await _notion_request("GET", "/comments", credentials, params=params)
    assert isinstance(data, dict)
    return {
        "results": data.get("results", []),
        "next_cursor": data.get("next_cursor"),
        "has_more": data.get("has_more", False),
    }


async def create_comment(body: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    data = await _notion_request("POST", "/comments", credentials, json_body=body)
    assert isinstance(data, dict)
    return data


# ------------------------------------------------------------------ #
# Users
# ------------------------------------------------------------------ #

async def list_users(
    session: AsyncSession,
    page_size: int | None = None,
    start_cursor: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    params: dict[str, Any] = {}
    if page_size:
        params["page_size"] = page_size
    if start_cursor:
        params["start_cursor"] = start_cursor
    data = await _notion_request("GET", "/users", credentials, params=params)
    assert isinstance(data, dict)
    return {
        "results": data.get("results", []),
        "next_cursor": data.get("next_cursor"),
        "has_more": data.get("has_more", False),
    }


# ------------------------------------------------------------------ #
# Test
# ------------------------------------------------------------------ #

async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Run connectivity tests for Notion."""
    tests: list[ServiceTestEntry] = []

    try:
        credentials = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="notion_credentials", success=True, detail="Credentials found"))
    except ValueError as e:
        tests.append(ServiceTestEntry(name="notion_credentials", success=False, detail=str(e)))
        return tests

    try:
        data = await _notion_request("GET", "/users", credentials, params={"page_size": 1})
        assert isinstance(data, dict)
        count = len(data.get("results", []))
        tests.append(ServiceTestEntry(
            name="notion_list_users", success=True,
            detail=f"Can access workspace ({count} user(s) returned)",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="notion_list_users", success=False, detail=str(e)))

    return tests
