"""Add nano types/instances and state store.

Revision ID: 002_types_and_state
Revises: 001_initial
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002_types_and_state"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade():
    # --- Nano types/instances columns ---
    op.add_column("nanos", sa.Column("type_name", sa.String(255), nullable=True))
    op.add_column("nanos", sa.Column("parameters", sa.Text, nullable=True))

    # Data migration: derive type_name from script_path (e.g. "email-attention/nano.py" -> "email-attention")
    op.execute(
        "UPDATE nanos SET type_name = split_part(script_path, '/', 1) WHERE type_name IS NULL"
    )

    # Make type_name NOT NULL after backfill
    op.alter_column("nanos", "type_name", nullable=False)

    # --- Nano state table ---
    op.create_table(
        "nano_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("nano_id", UUID(as_uuid=True), sa.ForeignKey("nanos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("nano_id", "key", name="uq_nano_state_key"),
    )


def downgrade():
    op.drop_table("nano_state")
    op.drop_column("nanos", "parameters")
    op.drop_column("nanos", "type_name")
