"""Add wait_until_date to pending_approvals.

Revision ID: 003_approval_wait_until
Revises: 002_types_and_state
Create Date: 2026-02-15
"""
from alembic import op
import sqlalchemy as sa

revision = "003_approval_wait_until"
down_revision = "002_types_and_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pending_approvals", sa.Column("wait_until_date", sa.DateTime, nullable=True))


def downgrade():
    op.drop_column("pending_approvals", "wait_until_date")
