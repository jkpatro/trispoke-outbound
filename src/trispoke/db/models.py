from enum import Enum as PyEnum
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class CampaignMode(PyEnum):
    local_only = "local_only"
    claude_only = "claude_only"
    abacus_only = "abacus_only"
    hybrid = "hybrid"            # legacy: Claude → Local
    hybrid_smart = "hybrid_smart"  # recommended: Claude → Abacus → Local


class LeadStatus(PyEnum):
    new = "new"
    enriched = "enriched"
    drafted = "drafted"
    qc_pending = "qc_pending"
    qc_flagged = "qc_flagged"
    approved = "approved"
    queued_in_apollo = "queued_in_apollo"
    sent = "sent"
    replied = "replied"
    bounced = "bounced"
    follow_up_pending = "follow_up_pending"
    closed_no_reply = "closed_no_reply"
    unsubscribed = "unsubscribed"


class IntakeSource(PyEnum):
    apollo_csv = "apollo_csv"
    apollo_search = "apollo_search"
    manual_form = "manual_form"


class SendingMode(PyEnum):
    apollo = "apollo"
    smtp_direct = "smtp_direct"


class QCStatus(PyEnum):
    pending = "pending"
    passed = "passed"
    flagged = "flagged"


class SendStatus(PyEnum):
    queued = "queued"
    sent = "sent"
    bounced = "bounced"
    failed = "failed"


class ReplyClassification(PyEnum):
    positive = "positive"
    negative = "negative"
    unsubscribe = "unsubscribe"
    auto_reply = "auto_reply"
    unknown = "unknown"


class EventType(PyEnum):
    lead_imported = "lead_imported"
    apollo_enriched = "apollo_enriched"
    pain_analyzed = "pain_analyzed"
    email_generated = "email_generated"
    email_edited = "email_edited"
    email_approved = "email_approved"
    email_rejected = "email_rejected"
    email_sent = "email_sent"            # legacy (SMTP-direct path)
    reply_received = "reply_received"    # legacy
    bounce_received = "bounce_received"  # legacy
    unsubscribed = "unsubscribed"
    # V1.5 additions
    qc_passed = "qc_passed"
    qc_flagged = "qc_flagged"
    apollo_contact_created = "apollo_contact_created"
    apollo_sequence_created = "apollo_sequence_created"
    apollo_contact_enrolled = "apollo_contact_enrolled"
    apollo_message_sent = "apollo_message_sent"
    apollo_message_opened = "apollo_message_opened"
    apollo_message_replied = "apollo_message_replied"
    apollo_message_bounced = "apollo_message_bounced"
    apollo_message_unsubscribed = "apollo_message_unsubscribed"


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    mode: Mapped[CampaignMode] = mapped_column(String)  # Using String for enum
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    started_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"))
    email: Mapped[str] = mapped_column(String)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    company_size: Mapped[str | None] = mapped_column(String, nullable=True)
    company_industry: Mapped[str | None] = mapped_column(String, nullable=True)
    company_location: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    apollo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    apollo_contact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    intake_source: Mapped[str | None] = mapped_column(String, nullable=True, default="apollo_csv")
    source_row_json: Mapped[str] = mapped_column(Text)
    status: Mapped[LeadStatus] = mapped_column(String)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class PainAnalysis(Base):
    __tablename__ = "pain_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id"), unique=True)
    chronic: Mapped[str | None] = mapped_column(Text, nullable=True)
    acute: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id"))
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"))
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    parent_email_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("emails.id"), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    # V1.5 — Apollo sequence tracking
    apollo_sequence_id: Mapped[str | None] = mapped_column(String, nullable=True)
    apollo_step_id: Mapped[str | None] = mapped_column(String, nullable=True)
    apollo_enrollment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    apollo_pushed_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    apollo_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # V1.5 — QC tracking
    qc_status: Mapped[str | None] = mapped_column(String, nullable=True, default="pending")
    qc_checked_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    qc_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    qc_model_used: Mapped[str | None] = mapped_column(String, nullable=True)
    # V1.5.1 — per-email send mode ('now' = bypass schedule via /send_now;
    # 'scheduled' = let Apollo's slot schedule fire it at the next window).
    send_mode: Mapped[str] = mapped_column(String, default="scheduled")


class Send(Base):
    __tablename__ = "sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_id: Mapped[int] = mapped_column(Integer, ForeignKey("emails.id"))
    sent_from_inbox: Mapped[str] = mapped_column(String)
    sent_at: Mapped[DateTime] = mapped_column(DateTime)
    smtp_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[SendStatus] = mapped_column(String)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    # V1.5 — track which sending path delivered each message
    sent_via: Mapped[str] = mapped_column(String, default="apollo")
    apollo_mailbox_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id"))
    email_id: Mapped[int] = mapped_column(Integer, ForeignKey("emails.id"))
    received_at: Mapped[DateTime] = mapped_column(DateTime)
    from_address: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    classification: Mapped[ReplyClassification] = mapped_column(String)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id"))
    event_type: Mapped[EventType] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())


class ApolloSlot(Base):
    """V1.5.1 — one row per pre-activated Apollo sequence in the slot pool.

    Each slot is a sequence whose template content is rewritten just before
    each send. Recycling is gated on `last_message_status` because Apollo
    reads templates LIVE at send time, so a slot can only be reassigned
    when its currently-bound message is fully terminal (completed/failed).
    """

    __tablename__ = "apollo_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_name: Mapped[str] = mapped_column(String, unique=True)
    sequence_id: Mapped[str] = mapped_column(String, unique=True)
    step_id: Mapped[str] = mapped_column(String)
    touch_id: Mapped[str] = mapped_column(String)
    template_id: Mapped[str] = mapped_column(String)
    mailbox_id: Mapped[str | None] = mapped_column(String, nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 'free' | 'in_use' | 'disabled'
    status: Mapped[str] = mapped_column(String, default="free")
    last_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # mirror of Apollo's emailer_message.status: scheduled | completed | failed | ...
    last_message_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_used_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    last_freed_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


class ApolloSyncState(Base):
    """One row per campaign that's syncing with Apollo. Tracks the last-poll
    cursor so the events poller doesn't double-process old events."""

    __tablename__ = "apollo_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id"), unique=True
    )
    last_synced_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    last_event_cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


# Indexes
Index("idx_leads_campaign_id", Lead.campaign_id)
Index("idx_leads_status", Lead.status)
Index("idx_emails_lead_id", Email.lead_id)
Index("idx_emails_apollo_seq", Email.apollo_sequence_id)
Index("idx_emails_apollo_msg", Email.apollo_message_id)
Index("idx_sends_email_id", Send.email_id)
Index("idx_replies_lead_id", Reply.lead_id)
Index("idx_events_lead_id", Event.lead_id)
Index("idx_apollo_sync_campaign", ApolloSyncState.campaign_id)
Index("idx_apollo_slots_status", ApolloSlot.status, ApolloSlot.last_message_status)
Index("idx_apollo_slots_last_used", ApolloSlot.last_used_at)