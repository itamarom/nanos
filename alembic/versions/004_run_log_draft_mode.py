"""Add draft_mode to run_logs.

Revision ID: 004_run_log_draft_mode
Revises: 003_approval_wait_until
Create Date: 2026-02-16
"""
from alembic import op
import sqlalchemy as sa

revision = "004_run_log_draft_mode"
down_revision = "003_approval_wait_until"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("run_logs", sa.Column("draft_mode", sa.Boolean, nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("run_logs", "draft_mode")
