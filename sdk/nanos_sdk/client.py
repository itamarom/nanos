"""Auto-generated typed client — regenerate with: python sdk/generate.py"""
# This file is auto-generated from the gateway's OpenAPI schema.
# Do not edit manually. Run `python sdk/generate.py` to regenerate.

from __future__ import annotations

from typing import Any

from nanos_sdk._base import NanosClient as _BaseClient
from nanos_sdk import models


class NanosTypedClient(_BaseClient):
    """Typed client auto-generated from the gateway OpenAPI schema."""


    def approval_status(self, approval_id: str) -> models.ApprovalStatusOut:
        """Check the status of a pending approval."""
        return models.ApprovalStatusOut(**self._get(f"/api/approvals/{approval_id}/status"))


    def list_events(self, start: str, end: str) -> list[models.CalendarEvent]:
        """List calendar events between start and end dates."""
        return [models.CalendarEvent(**item) for item in self._get_list("/api/calendar/events", start=start, end=end)]


    def create_event(self, summary: str, start: str, end: str, description: str | None = None, attendees: list[str] | None = None, location: str | None = None) -> models.ApprovalCreatedResponse:
        """Create a calendar event (SENSITIVE -- requires approval)."""
        payload = {"summary": summary, "start": start, "end": end, "description": description, "attendees": attendees, "location": location}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/calendar/events", payload))


    def update_event(self, event_id: str, summary: str | None = None, start: str | None = None, end: str | None = None, description: str | None = None, attendees: list[str] | None = None, location: str | None = None) -> models.ApprovalCreatedResponse:
        """Update a calendar event (SENSITIVE -- requires approval)."""
        payload = {"summary": summary, "start": start, "end": end, "description": description, "attendees": attendees, "location": location}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/calendar/events/{event_id}", payload))


    def delete_event(self, event_id: str) -> models.ApprovalCreatedResponse:
        """Delete a calendar event (SENSITIVE -- requires approval)."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/calendar/events/{event_id}"))


    def gmail_messages_list(self, q: str | None = '', max_results: int | None = 20) -> list[models.GmailMessage]:
        """Search and list emails."""
        return [models.GmailMessage(**item) for item in self._get_list("/api/gmail/messages", q=q, max_results=max_results)]


    def gmail_profile(self) -> models.GmailProfile:
        """Get the authenticated user's Gmail profile."""
        return models.GmailProfile(**self._get("/api/gmail/profile"))


    def gmail_messages_get(self, message_id: str) -> models.GmailMessage:
        """Get full email content by message ID."""
        return models.GmailMessage(**self._get(f"/api/gmail/messages/{message_id}"))


    def gmail_threads_get(self, thread_id: str) -> models.GmailThread:
        """Get an email thread by thread ID."""
        return models.GmailThread(**self._get(f"/api/gmail/threads/{thread_id}"))


    def gmail_messages_send(self, to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None) -> models.ApprovalCreatedResponse:
        """Send an email. Requires approval (sensitive endpoint)."""
        payload = {"to": to, "subject": subject, "body": body, "cc": cc, "bcc": bcc}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/gmail/messages/send", payload))


    def gmail_messages_reply(self, message_id: str, body: str) -> models.ApprovalCreatedResponse:
        """Reply to an email. Requires approval (sensitive endpoint)."""
        payload = {"body": body}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/gmail/messages/{message_id}/reply", payload))


    def send_message(self, text: str) -> models.SlackSendResponse:
        """Post a message to Slack via webhook (NOT sensitive -- called directly)."""
        payload = {"text": text}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.SlackSendResponse(**self._post("/api/slack/send", payload))


    def hubspot_contacts_list(self, limit: int | None = 20, after: str | None = None, properties: str | None = None) -> models.HubSpotListResponse:
        """List CRM contacts."""
        return models.HubSpotListResponse(**self._get("/api/hubspot/contacts", limit=limit, after=after, properties=properties))


    def hubspot_contacts_create(self, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Create a contact. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/hubspot/contacts", payload))


    def hubspot_contacts_get(self, contact_id: str, properties: str | None = None) -> models.HubSpotObjectResponse:
        """Get a single contact by ID."""
        return models.HubSpotObjectResponse(**self._get(f"/api/hubspot/contacts/{contact_id}", properties=properties))


    def hubspot_contacts_update(self, contact_id: str, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Update a contact. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/hubspot/contacts/{contact_id}", payload))


    def hubspot_contacts_delete(self, contact_id: str) -> models.ApprovalCreatedResponse:
        """Delete a contact. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/hubspot/contacts/{contact_id}"))


    def hubspot_contacts_search(self, filters: list[dict[str, Any]] | None = None, properties: list[str] | None = None, limit: int | None = None) -> models.HubSpotSearchResponse:
        """Search contacts using filters."""
        payload = {"filters": filters, "properties": properties, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.HubSpotSearchResponse(**self._post("/api/hubspot/contacts/search", payload))


    def hubspot_deals_list(self, limit: int | None = 20, after: str | None = None, properties: str | None = None) -> models.HubSpotListResponse:
        """List CRM deals."""
        return models.HubSpotListResponse(**self._get("/api/hubspot/deals", limit=limit, after=after, properties=properties))


    def hubspot_deals_create(self, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Create a deal. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/hubspot/deals", payload))


    def hubspot_deals_get(self, deal_id: str, properties: str | None = None) -> models.HubSpotObjectResponse:
        """Get a single deal by ID."""
        return models.HubSpotObjectResponse(**self._get(f"/api/hubspot/deals/{deal_id}", properties=properties))


    def hubspot_deals_update(self, deal_id: str, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Update a deal. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/hubspot/deals/{deal_id}", payload))


    def hubspot_deals_delete(self, deal_id: str) -> models.ApprovalCreatedResponse:
        """Delete a deal. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/hubspot/deals/{deal_id}"))


    def hubspot_deals_search(self, filters: list[dict[str, Any]] | None = None, properties: list[str] | None = None, limit: int | None = None) -> models.HubSpotSearchResponse:
        """Search deals using filters."""
        payload = {"filters": filters, "properties": properties, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.HubSpotSearchResponse(**self._post("/api/hubspot/deals/search", payload))


    def hubspot_tasks_list(self, limit: int | None = 20, after: str | None = None, properties: str | None = None) -> models.HubSpotListResponse:
        """List CRM tasks."""
        return models.HubSpotListResponse(**self._get("/api/hubspot/tasks", limit=limit, after=after, properties=properties))


    def hubspot_tasks_create(self, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Create a task. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/hubspot/tasks", payload))


    def hubspot_tasks_get(self, task_id: str, properties: str | None = None) -> models.HubSpotObjectResponse:
        """Get a single task by ID."""
        return models.HubSpotObjectResponse(**self._get(f"/api/hubspot/tasks/{task_id}", properties=properties))


    def hubspot_tasks_update(self, task_id: str, properties: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Update a task. Requires approval."""
        payload = {"properties": properties}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/hubspot/tasks/{task_id}", payload))


    def hubspot_tasks_delete(self, task_id: str) -> models.ApprovalCreatedResponse:
        """Delete a task. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/hubspot/tasks/{task_id}"))


    def hubspot_tasks_search(self, filters: list[dict[str, Any]] | None = None, properties: list[str] | None = None, limit: int | None = None) -> models.HubSpotSearchResponse:
        """Search tasks using filters."""
        payload = {"filters": filters, "properties": properties, "limit": limit}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.HubSpotSearchResponse(**self._post("/api/hubspot/tasks/search", payload))


    def hubspot_properties_list(self, object_type: str) -> list[dict[str, Any]]:
        """List all property definitions for a CRM object type (contacts, deals, tasks)."""
        return self._get_list(f"/api/hubspot/properties/{object_type}")


    def hubspot_properties_get(self, object_type: str, property_name: str) -> dict[str, Any]:
        """Get a single property definition including options for dropdowns."""
        return self._get(f"/api/hubspot/properties/{object_type}/{property_name}")


    def whatsapp_chats_list(self, limit: int | None = 20) -> dict[str, Any]:
        """List recent WhatsApp chats."""
        return self._get("/api/whatsapp/chats", limit=limit)


    def whatsapp_messages_list(self, chat_jid: str | None = None, limit: int | None = None, before: str | None = None, after: str | None = None) -> dict[str, Any]:
        """List WhatsApp messages, optionally filtered by chat and time range."""
        payload = {"chat_jid": chat_jid, "limit": limit, "before": before, "after": after}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._post("/api/whatsapp/messages/list", payload)


    def whatsapp_messages_search(self, query: str) -> dict[str, Any]:
        """Search WhatsApp messages (offline search of synced messages)."""
        payload = {"query": query}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._post("/api/whatsapp/messages/search", payload)


    def whatsapp_groups_list(self) -> dict[str, Any]:
        """List WhatsApp group chats."""
        return self._get("/api/whatsapp/groups")


    def whatsapp_messages_send_text(self, to: str, message: str) -> models.ApprovalCreatedResponse:
        """Send a WhatsApp text message. Requires approval (sensitive endpoint)."""
        payload = {"to": to, "message": message}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/whatsapp/messages/send", payload))


    def whatsapp_messages_send_file(self, to: str, file_path: str, caption: str | None = None) -> models.ApprovalCreatedResponse:
        """Send a file via WhatsApp. Requires approval (sensitive endpoint)."""
        payload = {"to": to, "file_path": file_path, "caption": caption}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/whatsapp/messages/send-file", payload))


    def whatsapp_media_download(self, chat_jid: str, message_id: str) -> dict[str, Any]:
        """Download a media attachment from a WhatsApp message."""
        payload = {"chat_jid": chat_jid, "message_id": message_id}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._post("/api/whatsapp/media/download", payload)


    def whatsapp_history_backfill(self, chat_jid: str, requests: int | None = None, count: int | None = None) -> dict[str, Any]:
        """Fetch older WhatsApp messages from the primary device."""
        payload = {"chat_jid": chat_jid, "requests": requests, "count": count}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._post("/api/whatsapp/history/backfill", payload)


    def notion_search(self, query: str | None = None, filter: dict[str, Any] | None = None, sort: dict[str, Any] | None = None, page_size: int | None = None, start_cursor: str | None = None) -> models.NotionListResponse:
        """Search the Notion workspace."""
        payload = {"query": query, "filter": filter, "sort": sort, "page_size": page_size, "start_cursor": start_cursor}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.NotionListResponse(**self._post("/api/notion/search", payload))


    def notion_databases_get(self, database_id: str) -> dict[str, Any]:
        """Get a database schema and properties."""
        return self._get(f"/api/notion/databases/{database_id}")


    def notion_databases_query(self, database_id: str, filter: dict[str, Any] | None = None, sorts: list[dict[str, Any]] | None = None, page_size: int | None = None, start_cursor: str | None = None) -> models.NotionListResponse:
        """Query a database with filters and sorts."""
        payload = {"filter": filter, "sorts": sorts, "page_size": page_size, "start_cursor": start_cursor}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.NotionListResponse(**self._post(f"/api/notion/databases/{database_id}/query", payload))


    def notion_pages_get(self, page_id: str) -> dict[str, Any]:
        """Get a page and its properties."""
        return self._get(f"/api/notion/pages/{page_id}")


    def notion_pages_update(self, page_id: str, properties: dict[str, Any] | None = None, icon: dict[str, Any] | None = None, cover: dict[str, Any] | None = None) -> models.ApprovalCreatedResponse:
        """Update a page. Requires approval."""
        payload = {"properties": properties, "icon": icon, "cover": cover}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/notion/pages/{page_id}", payload))


    def notion_pages_delete(self, page_id: str) -> models.ApprovalCreatedResponse:
        """Archive a page. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/notion/pages/{page_id}"))


    def notion_pages_create(self, parent: dict[str, Any], properties: dict[str, Any], children: list[dict[str, Any]] | None = None, icon: dict[str, Any] | None = None, cover: dict[str, Any] | None = None) -> models.ApprovalCreatedResponse:
        """Create a page. Requires approval."""
        payload = {"parent": parent, "properties": properties, "children": children, "icon": icon, "cover": cover}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/notion/pages", payload))


    def notion_blocks_list(self, block_id: str, page_size: int | None = None, start_cursor: str | None = None) -> models.NotionListResponse:
        """List child blocks of a page or block."""
        return models.NotionListResponse(**self._get(f"/api/notion/blocks/{block_id}/children", page_size=page_size, start_cursor=start_cursor))


    def notion_blocks_append(self, block_id: str, children: list[dict[str, Any]]) -> models.ApprovalCreatedResponse:
        """Append blocks to a page. Requires approval."""
        payload = {"children": children}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/notion/blocks/{block_id}/children", payload))


    def notion_blocks_update(self, block_id: str, block_data: dict[str, Any]) -> models.ApprovalCreatedResponse:
        """Update a block. Requires approval."""
        payload = {"block_data": block_data}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/notion/blocks/{block_id}", payload))


    def notion_blocks_delete(self, block_id: str) -> models.ApprovalCreatedResponse:
        """Archive a block. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/notion/blocks/{block_id}"))


    def notion_comments_list(self, block_id: str | None = None, page_size: int | None = None, start_cursor: str | None = None) -> models.NotionListResponse:
        """List comments on a page or block."""
        return models.NotionListResponse(**self._get("/api/notion/comments", block_id=block_id, page_size=page_size, start_cursor=start_cursor))


    def notion_comments_create(self, rich_text: list[dict[str, Any]], parent: dict[str, Any] | None = None, discussion_id: str | None = None) -> models.ApprovalCreatedResponse:
        """Create a comment. Requires approval."""
        payload = {"parent": parent, "discussion_id": discussion_id, "rich_text": rich_text}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/notion/comments", payload))


    def notion_users_list(self, page_size: int | None = None, start_cursor: str | None = None) -> models.NotionListResponse:
        """List workspace users."""
        return models.NotionListResponse(**self._get("/api/notion/users", page_size=page_size, start_cursor=start_cursor))


    def linear_issues_list(self, filter: dict[str, Any] | None = None, first: int | None = None, after: str | None = None) -> models.LinearPaginatedResponse:
        """List/search issues with optional filter."""
        payload = {"filter": filter, "first": first, "after": after}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.LinearPaginatedResponse(**self._post("/api/linear/issues/search", payload))


    def linear_issues_get(self, issue_id: str) -> dict[str, Any]:
        """Get a single issue by ID."""
        return self._get(f"/api/linear/issues/{issue_id}")


    def linear_issues_update(self, issue_id: str, title: str | None = None, description: str | None = None, stateId: str | None = None, assigneeId: str | None = None, priority: int | None = None, labelIds: list[str] | None = None, projectId: str | None = None, cycleId: str | None = None, parentId: str | None = None, estimate: int | None = None) -> models.ApprovalCreatedResponse:
        """Update an issue. Requires approval."""
        payload = {"title": title, "description": description, "stateId": stateId, "assigneeId": assigneeId, "priority": priority, "labelIds": labelIds, "projectId": projectId, "cycleId": cycleId, "parentId": parentId, "estimate": estimate}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/linear/issues/{issue_id}", payload))


    def linear_issues_delete(self, issue_id: str) -> models.ApprovalCreatedResponse:
        """Archive an issue. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/linear/issues/{issue_id}"))


    def linear_issues_create(self, title: str, teamId: str, description: str | None = None, assigneeId: str | None = None, stateId: str | None = None, priority: int | None = None, labelIds: list[str] | None = None, projectId: str | None = None, cycleId: str | None = None, parentId: str | None = None, estimate: int | None = None) -> models.ApprovalCreatedResponse:
        """Create an issue. Requires approval."""
        payload = {"title": title, "teamId": teamId, "description": description, "assigneeId": assigneeId, "stateId": stateId, "priority": priority, "labelIds": labelIds, "projectId": projectId, "cycleId": cycleId, "parentId": parentId, "estimate": estimate}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/linear/issues", payload))


    def linear_comments_list(self, issue_id: str, first: int | None = 50, after: str | None = None) -> models.LinearPaginatedResponse:
        """List comments on an issue."""
        return models.LinearPaginatedResponse(**self._get(f"/api/linear/issues/{issue_id}/comments", first=first, after=after))


    def linear_comments_create(self, issueId: str, body: str) -> models.ApprovalCreatedResponse:
        """Create a comment on an issue. Requires approval."""
        payload = {"issueId": issueId, "body": body}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post("/api/linear/comments", payload))


    def linear_comments_update(self, comment_id: str, body: str) -> models.ApprovalCreatedResponse:
        """Update a comment. Requires approval."""
        payload = {"body": body}
        payload = {k: v for k, v in payload.items() if v is not None}
        return models.ApprovalCreatedResponse(**self._post(f"/api/linear/comments/{comment_id}", payload))


    def linear_comments_delete(self, comment_id: str) -> models.ApprovalCreatedResponse:
        """Delete a comment. Requires approval."""
        return models.ApprovalCreatedResponse(**self._delete(f"/api/linear/comments/{comment_id}"))


    def linear_projects_list(self, first: int | None = 50, after: str | None = None) -> models.LinearPaginatedResponse:
        """List projects."""
        return models.LinearPaginatedResponse(**self._get("/api/linear/projects", first=first, after=after))


    def linear_projects_get(self, project_id: str) -> dict[str, Any]:
        """Get a single project by ID."""
        return self._get(f"/api/linear/projects/{project_id}")


    def linear_teams_list(self) -> dict[str, Any]:
        """List teams with their workflow states and labels."""
        return self._get("/api/linear/teams")


    def linear_cycles_list(self, team_id: str, first: int | None = 20, after: str | None = None) -> models.LinearPaginatedResponse:
        """List cycles for a team."""
        return models.LinearPaginatedResponse(**self._get(f"/api/linear/teams/{team_id}/cycles", first=first, after=after))


    def linear_users_list(self) -> dict[str, Any]:
        """List workspace users and get the authenticated viewer."""
        return self._get("/api/linear/users")


    def state_get(self, key: str) -> models.StateGetResponse:
        """Get a state value by key."""
        return models.StateGetResponse(**self._get(f"/api/state/{key}"))


    def state_set(self, key: str, value: Any, value_type: str) -> dict[str, Any]:
        """Set (upsert) a state value."""
        payload = {"value": value, "value_type": value_type}
        payload = {k: v for k, v in payload.items() if v is not None}
        return self._post(f"/api/state/{key}", payload)



# Public alias — __init__.py imports NanosClient from here
NanosClient = NanosTypedClient
