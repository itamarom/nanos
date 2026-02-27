"""Auto-generated Pydantic models — regenerate with: python sdk/generate.py"""
# This file is auto-generated from the gateway's OpenAPI schema.
# Do not edit manually. Run `python sdk/generate.py` to regenerate.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field



class AgentResponse(BaseModel):
    """Auto-generated model for AgentResponse."""

    status: str
    messages: list[MessageOut]


class ApprovalCreatedResponse(BaseModel):
    """Returned by all sensitive/approval-requiring endpoints."""

    approval_id: str
    status: str


class ApprovalOut(BaseModel):
    """Auto-generated model for ApprovalOut."""

    id: str
    nano_name: str
    endpoint: str
    method: str
    request_body: str
    status: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None
    created_at: str
    resolved_at: str


class ApprovalStatusOut(BaseModel):
    """Auto-generated model for ApprovalStatusOut."""

    id: str
    status: str
    response_body: str


class CalendarAttendee(BaseModel):
    """Auto-generated model for CalendarAttendee."""

    email: str
    name: str | None = None
    response: str | None = None
    self: bool | None = None
    organizer: bool | None = None


class CalendarEvent(BaseModel):
    """Auto-generated model for CalendarEvent."""

    id: str | None = None
    summary: str
    start: str
    end: str
    description: str | None = None
    attendees: list[CalendarAttendee] | None = None
    location: str | None = None
    status: str | None = None
    html_link: str | None = None
    conference_link: str | None = None
    organizer: str | None = None
    creator: str | None = None
    visibility: str | None = None
    transparency: str | None = None
    color_id: str | None = None
    recurring_event_id: str | None = None
    recurrence: list[str] | None = None
    reminders: dict[str, Any] | None = None
    updated: str | None = None


class CalendarEventCreate(BaseModel):
    """Auto-generated model for CalendarEventCreate."""

    summary: str
    start: str
    end: str
    description: str | None = None
    attendees: list[str] | None = None
    location: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class CalendarEventUpdate(BaseModel):
    """Auto-generated model for CalendarEventUpdate."""

    summary: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None
    attendees: list[str] | None = None
    location: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class ChatRequest(BaseModel):
    """Auto-generated model for ChatRequest."""

    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Auto-generated model for ChatResponse."""

    content: str | None = None
    model: str
    usage: ChatUsage
    tool_calls: list[ToolCallOut] | None = None
    finish_reason: str | None = None


class ChatSend(BaseModel):
    """Auto-generated model for ChatSend."""

    message: str
    model: str | None = None
    enabled_apis: list[str] | None = None


class ChatUsage(BaseModel):
    """Auto-generated model for ChatUsage."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ConversationCreate(BaseModel):
    """Auto-generated model for ConversationCreate."""

    title: str | None = None
    model: str | None = None
    enabled_apis: list[str] | None = None


class ConversationOut(BaseModel):
    """Auto-generated model for ConversationOut."""

    id: str
    title: str
    model: str
    enabled_apis: list[str]
    status: str
    created_at: str
    updated_at: str


class ConversationUpdate(BaseModel):
    """Auto-generated model for ConversationUpdate."""

    title: str | None = None
    model: str | None = None
    enabled_apis: list[str] | None = None


class CredentialCreate(BaseModel):
    """Auto-generated model for CredentialCreate."""

    api_name: str
    credentials: dict[str, Any]


class CredentialOut(BaseModel):
    """Auto-generated model for CredentialOut."""

    api_name: str
    created_at: str
    updated_at: str


class EmbeddingRequest(BaseModel):
    """Auto-generated model for EmbeddingRequest."""

    input: str | list[str]
    model: str | None = None


class EmbeddingResponse(BaseModel):
    """Auto-generated model for EmbeddingResponse."""

    embeddings: list[list[float]]
    model: str
    usage: EmbeddingUsage


class EmbeddingUsage(BaseModel):
    """Auto-generated model for EmbeddingUsage."""

    prompt_tokens: int
    total_tokens: int


class GmailMessage(BaseModel):
    """Auto-generated model for GmailMessage."""
    model_config = {'populate_by_name': True}


    id: str
    thread_id: str
    subject: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    date: str | None = None
    snippet: str | None = None
    body: str | None = None


class GmailProfile(BaseModel):
    """Auto-generated model for GmailProfile."""

    email_address: str
    messages_total: int | None = None
    threads_total: int | None = None
    history_id: str | None = None


class GmailReplyRequest(BaseModel):
    """Auto-generated model for GmailReplyRequest."""

    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class GmailSendRequest(BaseModel):
    """Auto-generated model for GmailSendRequest."""

    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class GmailThread(BaseModel):
    """Auto-generated model for GmailThread."""

    id: str
    messages: list[GmailMessage]


class HTTPValidationError(BaseModel):
    """Auto-generated model for HTTPValidationError."""

    detail: list[ValidationError] | None = None


class HealthResponse(BaseModel):
    """Auto-generated model for HealthResponse."""

    status: str
    services: dict[str, Any]


class HubSpotCreateRequest(BaseModel):
    """Create a CRM object. All properties as flat key/value pairs."""

    properties: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class HubSpotListResponse(BaseModel):
    """Auto-generated model for HubSpotListResponse."""

    results: list[dict[str, Any]]
    paging: dict[str, Any] | None = None


class HubSpotObjectResponse(BaseModel):
    """Flattened CRM object: id + all properties at top level."""

    id: str


class HubSpotSearchRequest(BaseModel):
    """Search CRM objects using HubSpot filter syntax."""

    filters: list[dict[str, Any]] | None = None
    properties: list[str] | None = None
    limit: int | None = None


class HubSpotSearchResponse(BaseModel):
    """Auto-generated model for HubSpotSearchResponse."""

    total: int | None = None
    results: list[dict[str, Any]]


class HubSpotUpdateRequest(BaseModel):
    """Update a CRM object. Properties to change."""

    properties: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearCommentCreateRequest(BaseModel):
    """Auto-generated model for LinearCommentCreateRequest."""

    issueId: str
    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearCommentUpdateRequest(BaseModel):
    """Auto-generated model for LinearCommentUpdateRequest."""

    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearDeleteRequest(BaseModel):
    """Auto-generated model for LinearDeleteRequest."""

    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearIssueCreateRequest(BaseModel):
    """Auto-generated model for LinearIssueCreateRequest."""

    title: str
    teamId: str
    description: str | None = None
    assigneeId: str | None = None
    stateId: str | None = None
    priority: int | None = None
    labelIds: list[str] | None = None
    projectId: str | None = None
    cycleId: str | None = None
    parentId: str | None = None
    estimate: int | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearIssueSearchRequest(BaseModel):
    """Auto-generated model for LinearIssueSearchRequest."""

    filter: dict[str, Any] | None = None
    first: int | None = None
    after: str | None = None


class LinearIssueUpdateRequest(BaseModel):
    """Auto-generated model for LinearIssueUpdateRequest."""

    title: str | None = None
    description: str | None = None
    stateId: str | None = None
    assigneeId: str | None = None
    priority: int | None = None
    labelIds: list[str] | None = None
    projectId: str | None = None
    cycleId: str | None = None
    parentId: str | None = None
    estimate: int | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearPaginatedResponse(BaseModel):
    """Auto-generated model for LinearPaginatedResponse."""

    nodes: list[dict[str, Any]]
    pageInfo: dict[str, Any] | None = None


class MessageOut(BaseModel):
    """Auto-generated model for MessageOut."""

    id: str
    role: str
    content: str | None = None
    tool_calls: Any | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: Any | None = None
    tool_status: str | None = None
    approval_id: str | None = None
    created_at: str


class NanoCreate(BaseModel):
    """Auto-generated model for NanoCreate."""

    name: str
    description: str | None = None
    script_path: str | None = None
    schedule: str | None = None
    permissions: list[str] | None = None
    type_name: str | None = None
    parameters: dict[str, Any] | None = None


class NanoCreatedOut(BaseModel):
    """Auto-generated model for NanoCreatedOut."""

    id: str
    name: str
    description: str
    script_path: str
    schedule: str
    is_active: bool
    permissions: list[str]
    type_name: str | None = None
    parameters: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    api_key: str


class NanoOut(BaseModel):
    """Auto-generated model for NanoOut."""

    id: str
    name: str
    description: str
    script_path: str
    schedule: str
    is_active: bool
    permissions: list[str]
    type_name: str | None = None
    parameters: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class NanoTypeOut(BaseModel):
    """Auto-generated model for NanoTypeOut."""

    type_name: str
    name: str
    description: str | None = None
    schedule: str | None = None
    permissions: list[str] | None = None
    parameter_schema: dict[str, Any] | None = None


class NanoUpdate(BaseModel):
    """Auto-generated model for NanoUpdate."""

    name: str | None = None
    description: str | None = None
    schedule: str | None = None
    is_active: bool | None = None
    permissions: list[str] | None = None
    parameters: dict[str, Any] | None = None


class NotionBlockUpdateRequest(BaseModel):
    """Auto-generated model for NotionBlockUpdateRequest."""

    block_data: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionBlocksAppendRequest(BaseModel):
    """Auto-generated model for NotionBlocksAppendRequest."""

    children: list[dict[str, Any]]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionCommentCreateRequest(BaseModel):
    """Auto-generated model for NotionCommentCreateRequest."""

    parent: dict[str, Any] | None = None
    discussion_id: str | None = None
    rich_text: list[dict[str, Any]]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionDatabaseQueryRequest(BaseModel):
    """Auto-generated model for NotionDatabaseQueryRequest."""

    filter: dict[str, Any] | None = None
    sorts: list[dict[str, Any]] | None = None
    page_size: int | None = None
    start_cursor: str | None = None


class NotionListResponse(BaseModel):
    """Auto-generated model for NotionListResponse."""

    results: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool | None = None


class NotionPageCreateRequest(BaseModel):
    """Auto-generated model for NotionPageCreateRequest."""

    parent: dict[str, Any]
    properties: dict[str, Any]
    children: list[dict[str, Any]] | None = None
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionPageUpdateRequest(BaseModel):
    """Auto-generated model for NotionPageUpdateRequest."""

    properties: dict[str, Any] | None = None
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionSearchRequest(BaseModel):
    """Auto-generated model for NotionSearchRequest."""

    query: str | None = None
    filter: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None
    page_size: int | None = None
    start_cursor: str | None = None


class RunLogOut(BaseModel):
    """Auto-generated model for RunLogOut."""

    id: str
    nano_name: str
    trigger: str
    started_at: str
    finished_at: str
    status: str
    exit_code: int
    log_file_path: str


class RunNanoResponse(BaseModel):
    """Auto-generated model for RunNanoResponse."""

    task_id: str
    nano_name: str
    run_log_id: str


class ServiceTestEntry(BaseModel):
    """Single test result from a service's test_all()."""

    name: str
    success: bool
    detail: str


class SlackSendRequest(BaseModel):
    """Auto-generated model for SlackSendRequest."""

    text: str


class SlackSendResponse(BaseModel):
    """Auto-generated model for SlackSendResponse."""

    ok: bool


class StateGetResponse(BaseModel):
    """Auto-generated model for StateGetResponse."""

    key: str
    value: Any | None = None
    value_type: str | None = None
    found: bool


class StateSetRequest(BaseModel):
    """Auto-generated model for StateSetRequest."""

    value: Any
    value_type: str


class StopRunResponse(BaseModel):
    """Auto-generated model for StopRunResponse."""

    status: str
    run_log_id: str


class TestResult(BaseModel):
    """Auto-generated model for TestResult."""

    api_name: str
    success: bool
    tests: list[ServiceTestEntry]


class ToolCallFunction(BaseModel):
    """Auto-generated model for ToolCallFunction."""

    name: str
    arguments: str


class ToolCallOut(BaseModel):
    """Auto-generated model for ToolCallOut."""

    id: str
    type: str
    function: ToolCallFunction


class ValidationError(BaseModel):
    """Auto-generated model for ValidationError."""

    loc: list[str | int]
    msg: str
    type: str


class WhatsAppBackfillRequest(BaseModel):
    """Auto-generated model for WhatsAppBackfillRequest."""

    chat_jid: str
    requests: int | None = None
    count: int | None = None


class WhatsAppListMessagesRequest(BaseModel):
    """Auto-generated model for WhatsAppListMessagesRequest."""

    chat_jid: str | None = None
    limit: int | None = None
    before: str | None = None
    after: str | None = None


class WhatsAppMediaDownloadRequest(BaseModel):
    """Auto-generated model for WhatsAppMediaDownloadRequest."""

    chat_jid: str
    message_id: str


class WhatsAppSearchRequest(BaseModel):
    """Auto-generated model for WhatsAppSearchRequest."""

    query: str


class WhatsAppSendFileRequest(BaseModel):
    """Auto-generated model for WhatsAppSendFileRequest."""

    to: str
    file_path: str
    caption: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class WhatsAppSendTextRequest(BaseModel):
    """Auto-generated model for WhatsAppSendTextRequest."""

    to: str
    message: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None

