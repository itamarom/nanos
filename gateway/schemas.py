from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TypedDict

from pydantic import BaseModel, Field


class ServiceTestEntry(TypedDict):
    """Single test result from a service's test_all()."""
    name: str
    success: bool
    detail: str


# --- Nano schemas ---

class NanoCreate(BaseModel):
    name: str
    description: str = ""
    script_path: str = ""
    schedule: str | None = None
    permissions: list[str] = Field(default_factory=list)
    type_name: str = ""
    parameters: dict[str, Any] | None = None


class NanoUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    schedule: str | None = None
    is_active: bool | None = None
    permissions: list[str] | None = None
    parameters: dict[str, Any] | None = None


class NanoOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    script_path: str
    schedule: str | None
    is_active: bool
    permissions: list[str]
    type_name: str | None = None
    parameters: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NanoCreatedOut(NanoOut):
    api_key: str


class RunNanoResponse(BaseModel):
    task_id: str
    nano_name: str
    run_log_id: str


class StopRunResponse(BaseModel):
    status: str
    run_log_id: str


class NanoTypeOut(BaseModel):
    type_name: str
    name: str
    description: str = ""
    schedule: str | None = None
    permissions: list[str] = Field(default_factory=list)
    parameter_schema: dict[str, Any] | None = None


# --- Credential schemas ---

class CredentialCreate(BaseModel):
    api_name: str
    credentials: dict[str, Any]


class CredentialOut(BaseModel):
    api_name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Approval schemas ---

class ApprovalOut(BaseModel):
    id: uuid.UUID
    nano_name: str
    endpoint: str
    method: str
    request_body: str | None
    status: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: datetime | None = None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class ApprovalCreatedResponse(BaseModel):
    """Returned by all sensitive/approval-requiring endpoints."""
    approval_id: str
    status: str


class ApprovalStatusOut(BaseModel):
    id: uuid.UUID
    status: str
    response_body: str | None


# --- Run log schemas ---

class RunLogOut(BaseModel):
    id: uuid.UUID
    nano_name: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    exit_code: int | None
    log_file_path: str | None

    model_config = {"from_attributes": True}


# --- State schemas ---

class StateGetResponse(BaseModel):
    key: str
    value: Any | None = None
    value_type: str | None = None
    found: bool


class StateSetRequest(BaseModel):
    value: Any
    value_type: str  # string/int/float/bool/json


# --- OpenAI schemas ---

class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


class ChatUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ToolCallFunction(BaseModel):
    name: str
    arguments: str


class ToolCallOut(BaseModel):
    id: str
    type: str
    function: ToolCallFunction


class ChatResponse(BaseModel):
    content: str | None = None
    model: str
    usage: ChatUsage
    tool_calls: list[ToolCallOut] | None = None
    finish_reason: str | None = None


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str = "text-embedding-3-small"


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    usage: EmbeddingUsage


# --- Calendar schemas ---

class CalendarAttendee(BaseModel):
    email: str
    name: str = ""
    response: str = ""
    self: bool = False
    organizer: bool = False


class CalendarEvent(BaseModel):
    id: str | None = None
    summary: str
    start: str
    end: str
    description: str = ""
    attendees: list[CalendarAttendee] = Field(default_factory=list)
    location: str = ""
    status: str = ""
    html_link: str = ""
    conference_link: str = ""
    organizer: str = ""
    creator: str = ""
    visibility: str = "default"
    transparency: str = "opaque"
    color_id: str = ""
    recurring_event_id: str = ""
    recurrence: list[str] = Field(default_factory=list)
    reminders: dict[str, Any] = Field(default_factory=dict)
    updated: str = ""


class CalendarEventCreate(BaseModel):
    summary: str
    start: str
    end: str
    description: str = ""
    attendees: list[str] = Field(default_factory=list)
    location: str = ""
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class CalendarEventUpdate(BaseModel):
    summary: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None
    attendees: list[str] | None = None
    location: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


# --- Gmail schemas ---

class GmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str
    from_: str = Field(alias="from", default="")
    to: str = ""
    date: str = ""
    snippet: str = ""
    body: str = ""

    model_config = {"populate_by_name": True}


class GmailThread(BaseModel):
    id: str
    messages: list[GmailMessage]


class GmailProfile(BaseModel):
    email_address: str
    messages_total: int = 0
    threads_total: int = 0
    history_id: str = ""


class GmailSendRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str = ""
    bcc: str = ""
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class GmailReplyRequest(BaseModel):
    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


# --- Slack schemas ---

class SlackSendRequest(BaseModel):
    text: str


class SlackSendResponse(BaseModel):
    ok: bool


# --- Health schemas ---

class HealthResponse(BaseModel):
    status: str
    services: dict[str, str]


class TestResult(BaseModel):
    api_name: str
    success: bool
    tests: list[ServiceTestEntry]


# --- HubSpot CRM schemas ---

class HubSpotObjectResponse(BaseModel):
    """Flattened CRM object: id + all properties at top level."""
    id: str
    model_config = {"extra": "allow"}


class HubSpotListResponse(BaseModel):
    results: list[dict[str, Any]]
    paging: dict[str, Any] | None = None


class HubSpotSearchResponse(BaseModel):
    total: int = 0
    results: list[dict[str, Any]]


class HubSpotCreateRequest(BaseModel):
    """Create a CRM object. All properties as flat key/value pairs."""
    properties: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class HubSpotUpdateRequest(BaseModel):
    """Update a CRM object. Properties to change."""
    properties: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class HubSpotSearchRequest(BaseModel):
    """Search CRM objects using HubSpot filter syntax."""
    filters: list[dict[str, Any]] = Field(default_factory=list)
    properties: list[str] | None = None
    limit: int = 20


# --- WhatsApp schemas ---

class WhatsAppSendTextRequest(BaseModel):
    to: str  # phone number or JID
    message: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class WhatsAppSendFileRequest(BaseModel):
    to: str
    file_path: str
    caption: str | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class WhatsAppListMessagesRequest(BaseModel):
    chat_jid: str | None = None
    limit: int = 20
    before: str | None = None  # ISO-8601 timestamp
    after: str | None = None   # ISO-8601 timestamp


class WhatsAppSearchRequest(BaseModel):
    query: str


class WhatsAppBackfillRequest(BaseModel):
    chat_jid: str
    requests: int = 1
    count: int = 50


class WhatsAppMediaDownloadRequest(BaseModel):
    chat_jid: str
    message_id: str


# --- Notion schemas ---

class NotionSearchRequest(BaseModel):
    query: str | None = None
    filter: dict[str, Any] | None = None
    sort: dict[str, Any] | None = None
    page_size: int | None = None
    start_cursor: str | None = None


class NotionDatabaseQueryRequest(BaseModel):
    filter: dict[str, Any] | None = None
    sorts: list[dict[str, Any]] | None = None
    page_size: int | None = None
    start_cursor: str | None = None


class NotionPageCreateRequest(BaseModel):
    parent: dict[str, Any]
    properties: dict[str, Any]
    children: list[dict[str, Any]] | None = None
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionPageUpdateRequest(BaseModel):
    properties: dict[str, Any] | None = None
    icon: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionDeleteRequest(BaseModel):
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionBlocksAppendRequest(BaseModel):
    children: list[dict[str, Any]]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionBlockUpdateRequest(BaseModel):
    block_data: dict[str, Any]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionCommentCreateRequest(BaseModel):
    parent: dict[str, Any] | None = None
    discussion_id: str | None = None
    rich_text: list[dict[str, Any]]
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class NotionListResponse(BaseModel):
    results: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool = False


# --- Linear schemas ---

class LinearIssueSearchRequest(BaseModel):
    filter: dict[str, Any] | None = None
    first: int = 50
    after: str | None = None


class LinearIssueCreateRequest(BaseModel):
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


class LinearIssueUpdateRequest(BaseModel):
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


class LinearCommentCreateRequest(BaseModel):
    issueId: str
    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearCommentUpdateRequest(BaseModel):
    body: str
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearDeleteRequest(BaseModel):
    explanation: str | None = None
    reasoning: str | None = None
    wait_until_date: str | None = None


class LinearPaginatedResponse(BaseModel):
    nodes: list[dict[str, Any]]
    pageInfo: dict[str, Any] = Field(default_factory=dict)
