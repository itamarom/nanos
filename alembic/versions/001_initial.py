"""Initial migration — create all 6 tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-02-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nanos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("script_path", sa.String(512), nullable=False),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "nano_api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nano_id", UUID(as_uuid=True), sa.ForeignKey("nanos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(68), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "nano_permissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nano_id", UUID(as_uuid=True), sa.ForeignKey("nanos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.UniqueConstraint("nano_id", "endpoint", name="uq_nano_endpoint"),
    )

    op.create_table(
        "api_credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("api_name", sa.String(100), unique=True, nullable=False),
        sa.Column("credentials", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "pending_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nano_id", UUID(as_uuid=True), sa.ForeignKey("nanos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("request_body", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("slack_message_ts", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "run_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nano_id", UUID(as_uuid=True), sa.ForeignKey("nanos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("stdout", sa.Text, server_default=""),
        sa.Column("stderr", sa.Text, server_default=""),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("log_file_path", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_table("run_logs")
    op.drop_table("pending_approvals")
    op.drop_table("api_credentials")
    op.drop_table("nano_permissions")
    op.drop_table("nano_api_keys")
    op.drop_table("nanos")
