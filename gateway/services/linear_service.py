"""Linear API service — Issues, Projects, Comments, Teams, Cycles, Users.

Uses the Linear GraphQL API: https://api.linear.app/graphql
Authentication via personal API key (no Bearer prefix).
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

GRAPHQL_URL = "https://api.linear.app/graphql"


async def _get_credentials(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(ApiCredential).where(ApiCredential.api_name == "linear")
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise ValueError("Linear credentials not configured")
    from gateway.crypto import decrypt_json
    return decrypt_json(cred.credentials)


def _headers(credentials: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": credentials["api_key"],
        "Content-Type": "application/json",
    }


async def _graphql(
    query: str,
    variables: dict[str, Any] | None,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Execute a GraphQL query/mutation against the Linear API."""
    body: dict[str, Any] = {"query": query}
    if variables:
        body["variables"] = variables
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(GRAPHQL_URL, headers=_headers(credentials), json=body)
    data = resp.json()
    if resp.status_code >= 400:
        msg = data.get("errors", [{}])[0].get("message", resp.text) if data.get("errors") else resp.text
        raise Exception(f"Linear API error {resp.status_code}: {msg}")
    if data.get("errors"):
        msg = data["errors"][0].get("message", str(data["errors"]))
        raise Exception(f"Linear GraphQL error: {msg}")
    assert isinstance(data, dict)
    result: dict[str, Any] = data.get("data", {})
    return result


# ------------------------------------------------------------------ #
# Issues
# ------------------------------------------------------------------ #

async def list_issues(
    session: AsyncSession,
    filter: dict[str, Any] | None = None,
    first: int = 50,
    after: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListIssues($filter: IssueFilter, $first: Int, $after: String) {
      issues(filter: $filter, first: $first, after: $after) {
        nodes {
          id
          identifier
          title
          description
          priority
          priorityLabel
          state { id name type }
          assignee { id name email }
          team { id name key }
          project { id name }
          labels { nodes { id name color } }
          createdAt
          updatedAt
          url
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    variables: dict[str, Any] = {"first": first}
    if filter:
        variables["filter"] = filter
    if after:
        variables["after"] = after
    data = await _graphql(query, variables, credentials)
    issues = data.get("issues", {})
    return {"nodes": issues.get("nodes", []), "pageInfo": issues.get("pageInfo", {})}


async def get_issue(issue_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query GetIssue($id: String!) {
      issue(id: $id) {
        id
        identifier
        title
        description
        priority
        priorityLabel
        estimate
        state { id name type }
        assignee { id name email }
        team { id name key }
        project { id name }
        cycle { id name number }
        labels { nodes { id name color } }
        parent { id identifier title }
        children { nodes { id identifier title state { name } } }
        relations { nodes { id type relatedIssue { id identifier title } } }
        createdAt
        updatedAt
        completedAt
        url
      }
    }
    """
    data = await _graphql(query, {"id": issue_id}, credentials)
    result: dict[str, Any] = data.get("issue", {})
    return result


async def create_issue(input: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue {
          id
          identifier
          title
          url
          state { id name }
        }
      }
    }
    """
    data = await _graphql(query, {"input": input}, credentials)
    result: dict[str, Any] = data.get("issueCreate", {})
    return result


async def update_issue(issue_id: str, input: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue {
          id
          identifier
          title
          url
          state { id name }
        }
      }
    }
    """
    data = await _graphql(query, {"id": issue_id, "input": input}, credentials)
    result: dict[str, Any] = data.get("issueUpdate", {})
    return result


async def delete_issue(issue_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation ArchiveIssue($id: String!) {
      issueArchive(id: $id) {
        success
      }
    }
    """
    data = await _graphql(query, {"id": issue_id}, credentials)
    result: dict[str, Any] = data.get("issueArchive", {})
    return result


# ------------------------------------------------------------------ #
# Comments
# ------------------------------------------------------------------ #

async def list_comments(
    issue_id: str,
    session: AsyncSession,
    first: int = 50,
    after: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListComments($issueId: String!, $first: Int, $after: String) {
      issue(id: $issueId) {
        comments(first: $first, after: $after) {
          nodes {
            id
            body
            user { id name email }
            createdAt
            updatedAt
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    variables: dict[str, Any] = {"issueId": issue_id, "first": first}
    if after:
        variables["after"] = after
    data = await _graphql(query, variables, credentials)
    comments = data.get("issue", {}).get("comments", {})
    return {"nodes": comments.get("nodes", []), "pageInfo": comments.get("pageInfo", {})}


async def create_comment(input: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation CreateComment($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment {
          id
          body
          user { id name }
          createdAt
        }
      }
    }
    """
    data = await _graphql(query, {"input": input}, credentials)
    result: dict[str, Any] = data.get("commentCreate", {})
    return result


async def update_comment(comment_id: str, input: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation UpdateComment($id: String!, $input: CommentUpdateInput!) {
      commentUpdate(id: $id, input: $input) {
        success
        comment {
          id
          body
          updatedAt
        }
      }
    }
    """
    data = await _graphql(query, {"id": comment_id, "input": input}, credentials)
    result: dict[str, Any] = data.get("commentUpdate", {})
    return result


async def delete_comment(comment_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    mutation DeleteComment($id: String!) {
      commentDelete(id: $id) {
        success
      }
    }
    """
    data = await _graphql(query, {"id": comment_id}, credentials)
    result: dict[str, Any] = data.get("commentDelete", {})
    return result


# ------------------------------------------------------------------ #
# Projects
# ------------------------------------------------------------------ #

async def list_projects(
    session: AsyncSession,
    first: int = 50,
    after: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListProjects($first: Int, $after: String) {
      projects(first: $first, after: $after) {
        nodes {
          id
          name
          description
          state
          progress
          startDate
          targetDate
          lead { id name email }
          teams { nodes { id name key } }
          createdAt
          updatedAt
          url
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    variables: dict[str, Any] = {"first": first}
    if after:
        variables["after"] = after
    data = await _graphql(query, variables, credentials)
    projects = data.get("projects", {})
    return {"nodes": projects.get("nodes", []), "pageInfo": projects.get("pageInfo", {})}


async def get_project(project_id: str, session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query GetProject($id: String!) {
      project(id: $id) {
        id
        name
        description
        state
        progress
        startDate
        targetDate
        lead { id name email }
        members { nodes { id name email } }
        teams { nodes { id name key } }
        issues { nodes { id identifier title state { name } } }
        createdAt
        updatedAt
        url
      }
    }
    """
    data = await _graphql(query, {"id": project_id}, credentials)
    result: dict[str, Any] = data.get("project", {})
    return result


# ------------------------------------------------------------------ #
# Teams
# ------------------------------------------------------------------ #

async def list_teams(session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListTeams {
      teams {
        nodes {
          id
          name
          key
          description
          states { nodes { id name type position color } }
          labels { nodes { id name color } }
        }
      }
    }
    """
    data = await _graphql(query, None, credentials)
    return {"nodes": data.get("teams", {}).get("nodes", [])}


# ------------------------------------------------------------------ #
# Cycles
# ------------------------------------------------------------------ #

async def list_cycles(
    team_id: str,
    session: AsyncSession,
    first: int = 20,
    after: str | None = None,
) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListCycles($teamId: String!, $first: Int, $after: String) {
      cycles(filter: { team: { id: { eq: $teamId } } }, first: $first, after: $after) {
        nodes {
          id
          number
          name
          startsAt
          endsAt
          progress
          scopeCount: issueCountHistory
          completedScopeCount: completedIssueCountHistory
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    variables: dict[str, Any] = {"teamId": team_id, "first": first}
    if after:
        variables["after"] = after
    data = await _graphql(query, variables, credentials)
    cycles = data.get("cycles", {})
    return {"nodes": cycles.get("nodes", []), "pageInfo": cycles.get("pageInfo", {})}


# ------------------------------------------------------------------ #
# Users
# ------------------------------------------------------------------ #

async def list_users(session: AsyncSession) -> dict[str, Any]:
    credentials = await _get_credentials(session)
    query = """
    query ListUsers {
      users {
        nodes {
          id
          name
          email
          displayName
          active
          admin
        }
      }
      viewer {
        id
        name
        email
      }
    }
    """
    data = await _graphql(query, None, credentials)
    return {
        "users": data.get("users", {}).get("nodes", []),
        "viewer": data.get("viewer", {}),
    }


# ------------------------------------------------------------------ #
# Test
# ------------------------------------------------------------------ #

async def test_all(session: AsyncSession) -> list[ServiceTestEntry]:
    """Run connectivity tests for Linear."""
    tests: list[ServiceTestEntry] = []

    try:
        credentials = await _get_credentials(session)
        tests.append(ServiceTestEntry(name="linear_credentials", success=True, detail="Credentials found"))
    except ValueError as e:
        tests.append(ServiceTestEntry(name="linear_credentials", success=False, detail=str(e)))
        return tests

    try:
        query = "query { viewer { id name email } }"
        data = await _graphql(query, None, credentials)
        viewer = data.get("viewer", {})
        tests.append(ServiceTestEntry(
            name="linear_viewer", success=True,
            detail=f"Authenticated as {viewer.get('name', '?')} ({viewer.get('email', '?')})",
        ))
    except Exception as e:
        tests.append(ServiceTestEntry(name="linear_viewer", success=False, detail=str(e)))

    return tests
