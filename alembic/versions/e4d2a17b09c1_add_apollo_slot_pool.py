"""add_apollo_slot_pool

Revision ID: e4d2a17b09c1
Revises: c8a91f3d5b07
Create Date: 2026-05-29 08:00:00.000000

V1.5.1 — pre-activated Apollo sequence slot pool + per-email send_mode.

Why: Apollo's API silently ignores `active: true` on emailer_campaigns PUT,
so we can't activate sequences from code. Solution: pre-create N sequences
in Apollo (one-time setup), activate them manually in the UI, then reuse
them as a "slot pool" — overwrite the template content per send.

Apollo reads templates LIVE at send time, so a slot can only be reused
when its currently-bound message is in a terminal state (completed/failed).

Adds:
  emails       : +send_mode TEXT NOT NULL DEFAULT 'scheduled'
  + new table  : apollo_slots
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "e4d2a17b09c1"
down_revision: Union[str, Sequence[str], None] = "c8a91f3d5b07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "emails",
        sa.Column("send_mode", sa.String(), nullable=False, server_default="scheduled"),
    )

    op.create_table(
        "apollo_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slot_name", sa.String(), nullable=False),
        sa.Column("sequence_id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("touch_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("mailbox_id", sa.String(), nullable=True),
        sa.Column("schedule_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="free"),
        sa.Column("last_message_id", sa.String(), nullable=True),
        sa.Column("last_message_status", sa.String(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_freed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slot_name", name="uq_apollo_slots_slot_name"),
        sa.UniqueConstraint("sequence_id", name="uq_apollo_slots_sequence_id"),
    )

    op.create_index(
        "idx_apollo_slots_status",
        "apollo_slots",
        ["status", "last_message_status"],
    )
    op.create_index(
        "idx_apollo_slots_last_used",
        "apollo_slots",
        ["last_used_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_apollo_slots_last_used", table_name="apollo_slots")
    op.drop_index("idx_apollo_slots_status", table_name="apollo_slots")
    op.drop_table("apollo_slots")
    op.drop_column("emails", "send_mode")
