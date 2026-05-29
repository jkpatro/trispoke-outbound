"""add_auto_approval_and_followup

Revision ID: f3b8c2459d12
Revises: e4d2a17b09c1
Create Date: 2026-05-29 14:00:00.000000

Full-automation columns:
  campaigns      : +auto_approve_mode TEXT NOT NULL DEFAULT 'after_warmup'
                  +warmup_threshold INTEGER NOT NULL DEFAULT 20
  apollo_slots   : +followup_step_id, +followup_touch_id, +followup_template_id
                  (all nullable — existing 1-step slots stay valid)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "f3b8c2459d12"
down_revision: Union[str, Sequence[str], None] = "e4d2a17b09c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "auto_approve_mode", sa.String(), nullable=False,
            server_default="after_warmup",
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "warmup_threshold", sa.Integer(), nullable=False, server_default="20",
        ),
    )
    op.add_column("apollo_slots", sa.Column("followup_step_id", sa.String(), nullable=True))
    op.add_column("apollo_slots", sa.Column("followup_touch_id", sa.String(), nullable=True))
    op.add_column("apollo_slots", sa.Column("followup_template_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("apollo_slots", "followup_template_id")
    op.drop_column("apollo_slots", "followup_touch_id")
    op.drop_column("apollo_slots", "followup_step_id")
    op.drop_column("campaigns", "warmup_threshold")
    op.drop_column("campaigns", "auto_approve_mode")
