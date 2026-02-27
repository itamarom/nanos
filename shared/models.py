from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, Integer,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Nano(Base):
    __tablename__ = "nanos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    script_path: Mapped[str] = mapped_column(String(512))
    schedule: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    type_name: Mapped[str | None] = mapped_column(String(255))
    parameters: Mapped[str | None] = mapped_column(Text)  # JSON string

    api_keys: Mapped[list[NanoApiKey]] = relationship("NanoApiKey", back_populates="nano", cascade="all, delete-orphan")
    permissions: Mapped[list[NanoPermission]] = relationship("NanoPermission", back_populates="nano", cascade="all, delete-orphan")
    approvals: Mapped[list[PendingApproval]] = relationship("PendingApproval", back_populates="nano")
    run_logs: Mapped[list[RunLog]] = relationship("RunLog", back_populates="nano")
    state_entries: Mapped[list[NanoState]] = relationship("NanoState", back_populates="nano", cascade="all, delete-orphan")


class NanoApiKey(Base):
    __tablename__ = "nano_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nano_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nanos.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(68), unique=True)  # nk_ + 32 hex = 36 chars
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    nano: Mapped[Nano] = relationship("Nano", back_populates="api_keys")


class NanoPermission(Base):
    __tablename__ = "nano_permissions"
    __table_args__ = (UniqueConstraint("nano_id", "endpoint", name="uq_nano_endpoint"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nano_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nanos.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(255))

    nano: Mapped[Nano] = relationship("Nano", back_populates="permissions")


class ApiCredential(Base):
    __tablename__ = "api_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_name: Mapped[str] = mapped_column(String(100), unique=True)
    credentials: Mapped[str] = mapped_column(Text)  # JSON string
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nano_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nanos.id", ondelete="CASCADE"))
    batch_id: Mapped[str | None] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(10))
    request_body: Mapped[str | None] = mapped_column(Text)  # JSON string
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected/executed/failed
    response_body: Mapped[str | None] = mapped_column(Text)  # JSON string
    slack_message_ts: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)

    run_log_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("run_logs.id", ondelete="SET NULL"))
    explanation: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    wait_until_date: Mapped[datetime | None] = mapped_column(DateTime)

    nano: Mapped[Nano] = relationship("Nano", back_populates="approvals")
    run_log: Mapped[RunLog] = relationship("RunLog", back_populates="approvals")


class NanoState(Base):
    __tablename__ = "nano_state"
    __table_args__ = (UniqueConstraint("nano_id", "key", name="uq_nano_state_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nano_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nanos.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(255))
    value_type: Mapped[str] = mapped_column(String(20))  # string/int/float/bool/json
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded value
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    nano: Mapped[Nano] = relationship("Nano", back_populates="state_entries")


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nano_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nanos.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(20))  # schedule/manual
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/error
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    log_file_path: Mapped[str | None] = mapped_column(String(512))
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    draft_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    state_before: Mapped[str | None] = mapped_column(Text)  # JSON snapshot of nano state before run
    state_after: Mapped[str | None] = mapped_column(Text)   # JSON snapshot of nano state after run

    nano: Mapped[Nano] = relationship("Nano", back_populates="run_logs")
    approvals: Mapped[list[PendingApproval]] = relationship("PendingApproval", back_populates="run_log")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    enabled_apis: Mapped[str] = mapped_column(Text, default="[]")  # JSON array: ["hubspot","gmail"]
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle/running/awaiting_approval/error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list[ChatMessage]] = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/tool
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[str | None] = mapped_column(Text)  # JSON array from LLM
    tool_call_id: Mapped[str | None] = mapped_column(String(100))
    tool_name: Mapped[str | None] = mapped_column(String(100))
    tool_args: Mapped[str | None] = mapped_column(Text)  # JSON of tool call arguments
    tool_status: Mapped[str | None] = mapped_column(String(20))  # pending_approval/executed/approved/rejected/blocked/error
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[ChatConversation] = relationship("ChatConversation", back_populates="messages")
