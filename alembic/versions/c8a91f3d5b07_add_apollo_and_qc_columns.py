"""add_apollo_and_qc_columns

Revision ID: c8a91f3d5b07
Revises: b40588ef58fd
Create Date: 2026-05-23 12:00:00.000000

V1.5 — Apollo as both source and sink + LLM-based QC pass.

Schema changes:
  emails  : +apollo_sequence_id, +apollo_step_id, +apollo_enrollment_id,
            +apollo_pushed_at, +apollo_message_id
            +qc_status, +qc_checked_at, +qc_flags_json, +qc_model_used
  sends   : +sent_via (default 'apollo'), +apollo_mailbox_id
  leads   : +apollo_contact_id, +intake_source (default 'apollo_csv')
  +table  : apollo_sync_state
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "c8a91f3d5b07"
down_revision: Union[str, Sequence[str], None] = "b40588ef58fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # emails: Apollo tracking
    op.add_column("emails", sa.Column("apollo_sequence_id", sa.String(), nullable=True))
    op.add_column("emails", sa.Column("apollo_step_id", sa.String(), nullable=True))
    op.add_column("emails", sa.Column("apollo_enrollment_id", sa.String(), nullable=True))
    op.add_column("emails", sa.Column("apollo_pushed_at", sa.DateTime(), nullable=True))
    op.add_column("emails", sa.Column("apollo_message_id", sa.String(), nullable=True))

    # emails: QC tracking
    op.add_column(
        "emails",
        sa.Column("qc_status", sa.String(), nullable=True, server_default="pending"),
    )
    op.add_column("emails", sa.Column("qc_checked_at", sa.DateTime(), nullable=True))
    op.add_column("emails", sa.Column("qc_flags_json", sa.Text(), nullable=True))
    op.add_column("emails", sa.Column("qc_model_used", sa.String(), nullable=True))

    # sends: Apollo routing
    op.add_column(
        "sends",
        sa.Column("sent_via", sa.String(), nullable=False, server_default="apollo"),
    )
    op.add_column("sends", sa.Column("apollo_mailbox_id", sa.String(), nullable=True))

    # leads: Apollo contact + intake source
    op.add_column("leads", sa.Column("apollo_contact_id", sa.String(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "intake_source", sa.String(), nullable=True, server_default="apollo_csv"
        ),
    )

    # New table — per-campaign Apollo poll cursor.
    op.create_table(
        "apollo_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_event_cursor", sa.String(), nullable=True),
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
    )

    op.create_index("idx_emails_apollo_seq", "emails", ["apollo_sequence_id"])
    op.create_index("idx_emails_apollo_msg", "emails", ["apollo_message_id"])
    op.create_index(
        "idx_apollo_sync_campaign", "apollo_sync_state", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_apollo_sync_campaign", table_name="apollo_sync_state")
    op.drop_index("idx_emails_apollo_msg", table_name="emails")
    op.drop_index("idx_emails_apollo_seq", table_name="emails")
    op.drop_table("apollo_sync_state")

    op.drop_column("leads", "intake_source")
    op.drop_column("leads", "apollo_contact_id")
    op.drop_column("sends", "apollo_mailbox_id")
    op.drop_column("sends", "sent_via")

    op.drop_column("emails", "qc_model_used")
    op.drop_column("emails", "qc_flags_json")
    op.drop_column("emails", "qc_checked_at")
    op.drop_column("emails", "qc_status")
    op.drop_column("emails", "apollo_message_id")
    op.drop_column("emails", "apollo_pushed_at")
    op.drop_column("emails", "apollo_enrollment_id")
    op.drop_column("emails", "apollo_step_id")
    op.drop_column("emails", "apollo_sequence_id")
