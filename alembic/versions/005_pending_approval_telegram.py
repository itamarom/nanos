"""Add telegram_message_id and finished_at to pending_approvals.

Revision ID: 005_pending_approval_telegram
Revises: 004_run_log_draft_mode
Create Date: 2026-02-20
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect
import sqlalchemy as sa

revision = "005_pending_approval_telegram"
down_revision = "004_run_log_draft_mode"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    """Check if a column already exists (safe for manual ALTER TABLE)."""
    conn = op.get_bind()
    cols = {c["name"] for c in sa_inspect(conn).get_columns(table)}
    return column in cols


def upgrade():
    if not _has_column("pending_approvals", "telegram_message_id"):
        op.add_column("pending_approvals", sa.Column("telegram_message_id", sa.Integer, nullable=True))
    if not _has_column("pending_approvals", "finished_at"):
        op.add_column("pending_approvals", sa.Column("finished_at", sa.DateTime, nullable=True))


def downgrade():
    op.drop_column("pending_approvals", "finished_at")
    op.drop_column("pending_approvals", "telegram_message_id")
