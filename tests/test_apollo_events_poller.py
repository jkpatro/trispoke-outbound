"""V1.5.1 — slot-pool aware events poller.

Verifies:
  - The poller iterates `apollo_slots` (not campaigns) for in-flight messages.
  - 'completed' event creates a sends row, transitions lead, frees the slot.
  - 'replied' event creates a reply with classification.
  - Idempotency: running poll twice doesn't double-insert.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import (
    ApolloSlot,
    Base,
    Campaign,
    Email,
    Event,
    Lead,
    Reply,
    Send,
)


@pytest.fixture
def memdb(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def _fake_get_session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.get_session", _fake_get_session
    )
    return Session


def _seed_one_in_flight_message(Session, msg_id="msg-1"):
    """Slot in_use → message scheduled, awaiting events."""
    s = Session()
    c = Campaign(name="A", mode="apollo", settings_json="{}")
    s.add(c); s.commit()
    lead = Lead(
        campaign_id=c.id, email="x@x.com", first_name="X",
        source_row_json="{}", status="queued_in_apollo",
        apollo_contact_id="ct-1",
    )
    s.add(lead); s.commit()
    email = Email(
        lead_id=lead.id, campaign_id=c.id,
        subject="s", body="b",
        apollo_message_id=msg_id,
        apollo_sequence_id="seq-1",
        apollo_pushed_at=datetime(2026, 5, 29, 8, 0),
    )
    s.add(email); s.commit()
    slot = ApolloSlot(
        slot_name="slot-001",
        sequence_id="seq-1",
        step_id="step-1", touch_id="touch-1", template_id="tmpl-1",
        mailbox_id="mb-1",
        status="in_use",
        last_message_id=msg_id,
        last_message_status="scheduled",
        last_used_at=datetime(2026, 5, 29, 8, 0),
    )
    s.add(slot); s.commit()
    lead_id, email_id, slot_id = lead.id, email.id, slot.id
    s.close()
    return lead_id, email_id, slot_id


def _apollo_search_returning(messages):
    """Mock ApolloClient with _search_messages returning the given message list."""
    apollo = MagicMock()
    apollo._search_messages.return_value = {"emailer_messages": messages}
    apollo.get_message_body.return_value = {
        "subject": "Re: hello",
        "body_text": "Yes, interested - let's chat.",
    }
    return apollo


def test_completed_event_creates_send_and_frees_slot(memdb, monkeypatch):
    Session = memdb
    lead_id, email_id, slot_id = _seed_one_in_flight_message(Session, "msg-1")

    apollo = _apollo_search_returning([{
        "id": "msg-1",
        "contact_id": "ct-1",
        "status": "completed",
        "completed_at": "2026-05-29T08:05:00Z",
        "due_at": "2026-05-29T08:05:00Z",
        "sent_at": "2026-05-29T08:05:00Z",
        "email_account_id": "mb-1",
        "from_email": "out@trispoke.com",
        "provider_message_id": "<mid@trispoke.com>",
    }])
    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.ApolloClient", lambda: apollo
    )

    from trispoke.receiver.apollo_events_poller import ApolloEventsPoller
    counts = ApolloEventsPoller().run_once()
    assert counts.get("sent") == 1

    s = Session()
    assert s.get(Lead, lead_id).status == "sent"
    sends = s.query(Send).filter_by(email_id=email_id).all()
    assert len(sends) == 1
    assert sends[0].sent_via == "apollo"
    assert sends[0].smtp_message_id == "<mid@trispoke.com>"
    # Slot was freed (last_message_status updated to completed).
    slot = s.get(ApolloSlot, slot_id)
    assert slot.last_message_status == "completed"
    s.close()


def test_replied_event_creates_reply_with_classification(memdb, monkeypatch):
    Session = memdb
    lead_id, _, _ = _seed_one_in_flight_message(Session, "msg-1")

    apollo = _apollo_search_returning([{
        "id": "msg-1",
        "contact_id": "ct-1",
        "status": "completed",
        "completed_at": "2026-05-29T08:05:00Z",
        "sent_at": "2026-05-29T08:05:00Z",
        "replied_at": "2026-05-29T09:00:00Z",
        "email_account_id": "mb-1",
        "from_email": "out@trispoke.com",
    }])
    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.ApolloClient", lambda: apollo
    )

    from trispoke.receiver.apollo_events_poller import ApolloEventsPoller
    ApolloEventsPoller().run_once()

    s = Session()
    assert s.get(Lead, lead_id).status == "replied"
    replies = s.query(Reply).filter_by(lead_id=lead_id).all()
    assert len(replies) == 1
    assert replies[0].classification == "positive"
    s.close()


def test_idempotent_double_poll(memdb, monkeypatch):
    Session = memdb
    _seed_one_in_flight_message(Session, "msg-1")

    sent_event = {
        "id": "msg-1",
        "contact_id": "ct-1",
        "status": "completed",
        "completed_at": "2026-05-29T08:05:00Z",
        "sent_at": "2026-05-29T08:05:00Z",
        "email_account_id": "mb-1",
        "from_email": "out@trispoke.com",
    }
    apollo = _apollo_search_returning([sent_event])
    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.ApolloClient", lambda: apollo
    )

    from trispoke.receiver.apollo_events_poller import ApolloEventsPoller
    poller = ApolloEventsPoller()
    poller.run_once()
    # Reseed the slot status so the poller selects it again
    s = Session()
    slot = s.query(ApolloSlot).first()
    slot.last_message_status = "scheduled"
    s.commit()
    s.close()
    poller.run_once()

    s = Session()
    assert s.query(Send).count() == 1
    sent_events = s.query(Event).filter_by(event_type="apollo_message_sent").all()
    assert len(sent_events) == 1
    s.close()


def test_bounce_transitions_lead(memdb, monkeypatch):
    Session = memdb
    lead_id, _, _ = _seed_one_in_flight_message(Session, "msg-1")

    apollo = _apollo_search_returning([{
        "id": "msg-1",
        "contact_id": "ct-1",
        "status": "bounced",
        "bounced_at": "2026-05-29T08:30:00Z",
    }])
    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.ApolloClient", lambda: apollo
    )

    from trispoke.receiver.apollo_events_poller import ApolloEventsPoller
    ApolloEventsPoller().run_once()

    s = Session()
    assert s.get(Lead, lead_id).status == "bounced"
    s.close()


def test_terminal_status_slots_are_not_repolled(memdb, monkeypatch):
    """Once a slot's last_message_status is terminal, the poller skips it."""
    Session = memdb
    _seed_one_in_flight_message(Session, "msg-1")
    s = Session()
    slot = s.query(ApolloSlot).first()
    slot.last_message_status = "completed"  # already terminal
    s.commit()
    s.close()

    apollo = _apollo_search_returning([{
        "id": "msg-1", "contact_id": "ct-1", "status": "completed",
        "completed_at": "2026-05-29T08:05:00Z", "sent_at": "2026-05-29T08:05:00Z",
    }])
    monkeypatch.setattr(
        "trispoke.receiver.apollo_events_poller.ApolloClient", lambda: apollo
    )

    from trispoke.receiver.apollo_events_poller import ApolloEventsPoller
    ApolloEventsPoller().run_once()

    # Apollo should NOT have been called (slot was already terminal).
    apollo._search_messages.assert_not_called()
