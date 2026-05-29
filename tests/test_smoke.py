"""End-to-end smoke test of the trispoke-outbound pipeline.

Mocks every external dependency (Apollo, Ollama, SMTP, IMAP) and the
in-memory SQLite session is shared across every module via patches.

Pipeline:
  import → enrich → generate → approve → send → simulate replies
  → classify → schedule follow-ups

Asserts final lead statuses and event-type coverage.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import (
    Base,
    Email,
    Event,
    Lead,
    LeadStatus,
    PainAnalysis,
    Reply,
    Send,
    SendStatus,
)


# --- Synthetic lead fixture ----------------------------------------------------

# 10 leads. Industry/title/size chosen so the pain analyzer matches with
# confidence 0.85 (staffing+owner+small).
SYNTHETIC_LEADS = [
    {
        "Email": f"lead{i}@company{i}.com",
        "First Name": f"User{i}",
        "Last Name": f"Test",
        "Title": "Owner",
        "Company": f"Acme Staffing {i}",
        "Company Domain": f"company{i}.com",
        "Industry": "Staffing & Recruiting",
        "Company Size": 25,
    }
    for i in range(10)
]


@pytest.fixture
def xlsx_path(tmp_path: Path) -> str:
    """Write the synthetic leads to a temporary .xlsx and return its path."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    headers = list(SYNTHETIC_LEADS[0].keys())
    sheet.append(headers)
    for row in SYNTHETIC_LEADS:
        sheet.append([row[h] for h in headers])
    out = tmp_path / "leads.xlsx"
    wb.save(out)
    return str(out)


@pytest.fixture
def db():
    """Shared in-memory SQLite session, used by every module under test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


# --- Mock IMAP -----------------------------------------------------------------

class _MockMessage:
    """Synthetic imap-tools-like message."""

    def __init__(
        self,
        uid: str,
        from_: str,
        subject: str,
        text: str,
        in_reply_to: str | None = None,
    ):
        self.uid = uid
        self.from_ = from_
        self.subject = subject
        self.text = text
        self.date = datetime.utcnow()
        self.headers = {}
        if in_reply_to:
            self.headers["In-Reply-To"] = in_reply_to


class _MockMailBox:
    """Stand-in for imap_tools.MailBox.

    Holds a per-class queue of messages (keyed by host) so the patched
    constructor can hand them out to whichever inbox poll fetches first.
    """

    _queue: list[_MockMessage] = []

    def __init__(self, host: str):
        self.host = host

    def login(self, *_args, **_kwargs):
        return self

    def logout(self):
        return self

    def fetch(self, *_args, **_kwargs):
        # Drain the queue once per fetch.
        msgs = list(_MockMailBox._queue)
        _MockMailBox._queue.clear()
        return msgs

    def flag(self, *_args, **_kwargs):
        return None


# --- Helper: patch every get_session in the codebase ---------------------------

_GET_SESSION_TARGETS = (
    "trispoke.import_apollo.get_session",
    "trispoke.enrich.get_session",
    "trispoke.generate.get_session",
    "trispoke.sender.approve.get_session",
    "trispoke.sender.sender_loop.get_session",
    "trispoke.sender.inbox_manager.get_session",
    "trispoke.receiver.imap_poller.get_session",
    "trispoke.scheduler.followup.get_session",
)


def _patch_all_sessions(db):
    """Return a list of started patchers; caller is responsible for stopping."""
    patchers = []
    for target in _GET_SESSION_TARGETS:
        p = patch(target)
        m = p.start()
        m.return_value.__enter__.return_value = db
        m.return_value.__exit__.return_value = False
        patchers.append(p)
    return patchers


# --- The smoke test -----------------------------------------------------------

@patch("trispoke.receiver.imap_poller.MailBox", new=_MockMailBox)
@patch("smtplib.SMTP")
@patch("smtplib.SMTP_SSL")
@patch("trispoke.llm.ollama_client.OllamaClient.generate")
@patch("trispoke.apollo.client.ApolloClient.enrich_organization")
@patch("trispoke.apollo.client.ApolloClient.match_person")
def test_full_pipeline_smoke(
    # Decorator-arg order is bottom-up (closest-to-def is first).
    mock_match_person,
    mock_enrich_org,
    mock_ollama,
    mock_smtp_ssl,
    mock_smtp,
    monkeypatch,
    xlsx_path,
    db,
):
    # Apollo: enrichment fills the missing company_location so the lead is
    # actually advanced to status='enriched'.
    mock_match_person.return_value = {"email": None}
    mock_enrich_org.return_value = {
        "name": "Apollo-supplied",
        "estimated_num_employees": 25,
        "industry": "Staffing & Recruiting",
        "city": "Toronto",
        "state": "ON",
    }

    # Ollama: emit a parseable cold email.
    mock_ollama.return_value = (
        "Subject: staffing pipeline gaps\n\n"
        "Dear User,\n\n"
        "Noticed your team is hiring; we help staffing firms shorten "
        "time-to-hire by 40%.\n\n"
        "Best,\nAlex",
        150,
        0.5,
    )

    # SMTP: return-value chain so .login/.send_message/.quit all no-op.
    mock_smtp_ssl.return_value = MagicMock()
    mock_smtp.return_value = MagicMock()

    # Sender pacing → 0 so the loop doesn't wait between sends.
    monkeypatch.setattr("trispoke.sender.sender_loop.PACE_SECONDS", 0)

    # Inbox config: one synthetic warmed inbox.
    inbox_mock = MagicMock(
        address="warm@trispokeservices.com",
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_username="warm@trispokeservices.com",
        smtp_password="pw",
        imap_host="imap.test",
        imap_port=993,
        imap_username="warm@trispokeservices.com",
        imap_password="pw",
    )
    monkeypatch.setattr(
        "trispoke.sender.inbox_manager._parse_inboxes_config",
        lambda: [inbox_mock],
    )

    patchers = _patch_all_sessions(db)
    try:
        from trispoke.import_apollo import import_leads
        from trispoke.enrich import enrich_leads
        from trispoke.generate import generate_drafts
        from trispoke.sender.approve import approve_drafts
        from trispoke.sender.sender_loop import SenderLoop
        from trispoke.receiver.imap_poller import poll_once
        from trispoke.scheduler.followup import schedule_followups

        # ---- import ----
        import_leads(xlsx_path, "smoke_campaign")
        assert db.query(Lead).count() == 10
        assert all(lead.status == LeadStatus.new.value for lead in db.query(Lead).all())

        # ---- enrich ----
        enrich_leads("smoke_campaign")
        assert (
            db.query(Lead).filter_by(status=LeadStatus.enriched.value).count() == 10
        )

        # ---- generate ----
        generate_drafts("smoke_campaign", "local_only")
        # Post-V1.5: leads end in 'drafted' (QC passed) or 'qc_flagged' (QC
        # caught something). Either way the email + pain rows exist.
        post_generate_count = (
            db.query(Lead)
            .filter(Lead.status.in_([
                LeadStatus.drafted.value,
                LeadStatus.qc_flagged.value,
            ]))
            .count()
        )
        assert post_generate_count == 10
        assert db.query(Email).count() == 10
        assert db.query(PainAnalysis).count() == 10

        # ---- approve ----
        # V1.5: any leads QC flagged get force-transitioned to drafted here
        # (simulating a reviewer overriding QC) so the smoke test still drives
        # the full end-to-end pipeline.
        from trispoke.db.models import Campaign
        smoke_campaign = db.query(Campaign).filter_by(name="smoke_campaign").first()
        db.query(Lead).filter(
            Lead.campaign_id == smoke_campaign.id,
            Lead.status == LeadStatus.qc_flagged.value,
        ).update({Lead.status: LeadStatus.drafted.value})
        db.commit()
        approve_drafts("smoke_campaign")
        assert (
            db.query(Lead).filter_by(status=LeadStatus.approved.value).count() == 10
        )

        # ---- send ----
        loop = SenderLoop()
        loop._process_pending_emails()
        assert db.query(Lead).filter_by(status=LeadStatus.sent.value).count() == 10
        sends = db.query(Send).all()
        assert len(sends) == 10
        assert all(s.status == SendStatus.sent.value for s in sends)
        assert all(s.smtp_message_id for s in sends)

        # ---- simulate replies ----
        # 6 of 10 reply: 2 positive, 1 negative, 1 unsubscribe, 1 auto_reply,
        # 1 bounce. The remaining 4 stay 'sent'.
        sends_sorted = sorted(sends, key=lambda s: s.id)
        scenarios = [
            ("Re: hi", "Yes, very interested!", False),         # positive
            ("Re: hi", "Sure, happy to chat", False),           # positive
            ("Re: hi", "Not interested, thanks", False),        # negative
            ("Re: hi", "Please unsubscribe me", False),         # unsubscribe
            ("Out of Office", "I am out of the office", False), # auto_reply
            ("Delivery Failure",
             "550 User unknown — undeliverable", True),         # bounce
        ]
        for send, (subject, body, _is_bounce) in zip(sends_sorted, scenarios):
            email = db.query(Email).filter_by(id=send.email_id).first()
            lead = db.query(Lead).filter_by(id=email.lead_id).first()
            _MockMailBox._queue.append(
                _MockMessage(
                    uid=str(send.id),
                    from_=lead.email,
                    subject=subject,
                    text=body,
                    in_reply_to=send.smtp_message_id,
                )
            )

        poll_once()

        # ---- assert reply states ----
        replies = db.query(Reply).all()
        assert len(replies) == 6
        assert {r.classification for r in replies} == {
            "positive",
            "negative",
            "unsubscribe",
            "auto_reply",
            "bounce",
        }
        assert (
            db.query(Lead).filter_by(status=LeadStatus.replied.value).count() == 5
        )
        assert (
            db.query(Lead).filter_by(status=LeadStatus.bounced.value).count() == 1
        )
        assert (
            db.query(Lead).filter_by(status=LeadStatus.sent.value).count() == 4
        )

        # ---- schedule follow-ups ----
        # Backdate the no-reply sends to be 5 days old so the 4-day window fires.
        no_reply_lead_ids = {
            lead.id
            for lead in db.query(Lead)
            .filter_by(status=LeadStatus.sent.value)
            .all()
        }
        five_days_ago = datetime.utcnow() - timedelta(days=5)
        for send in sends:
            email = db.query(Email).filter_by(id=send.email_id).first()
            if email.lead_id in no_reply_lead_ids:
                send.sent_at = five_days_ago
        db.commit()

        schedule_followups()

        followup_emails = (
            db.query(Email).filter(Email.parent_email_id.isnot(None)).all()
        )
        assert len(followup_emails) == 4
        assert (
            db.query(Lead)
            .filter_by(status=LeadStatus.follow_up_pending.value)
            .count()
            == 4
        )

        # ---- final lead-status tally ----
        status_counts = dict(
            (status, db.query(Lead).filter_by(status=status).count())
            for status in [
                LeadStatus.replied.value,
                LeadStatus.bounced.value,
                LeadStatus.follow_up_pending.value,
                LeadStatus.sent.value,
            ]
        )
        assert status_counts == {
            "replied": 5,
            "bounced": 1,
            "follow_up_pending": 4,
            "sent": 0,
        }
        assert sum(status_counts.values()) == 10

        # ---- event sequence coverage ----
        event_types = {e.event_type for e in db.query(Event).all()}
        for required in (
            "lead_imported",
            "apollo_enriched",
            "pain_analyzed",
            "email_generated",
            "email_approved",
            "email_sent",
            "reply_received",
        ):
            assert required in event_types, f"missing event type: {required}"

    finally:
        for p in patchers:
            p.stop()
