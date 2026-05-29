"""V1.5: verify the Apollo-tracking columns are present on the SQLAlchemy
models and accept the values the push worker and events poller will write."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import (
    ApolloSyncState,
    Base,
    Campaign,
    Email,
    Lead,
    Send,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_lead(session, name="Ada"):
    c = Campaign(name="c1", mode="apollo", settings_json="{}")
    session.add(c)
    session.commit()
    lead = Lead(
        campaign_id=c.id,
        email=f"{name.lower()}@example.com",
        first_name=name,
        source_row_json="{}",
        status="new",
    )
    session.add(lead)
    session.commit()
    return c, lead


def test_lead_apollo_contact_id_and_intake_source_round_trip(session):
    _, lead = _make_lead(session)
    lead.apollo_contact_id = "ct-abc"
    lead.intake_source = "apollo_search"
    session.commit()

    fetched = session.query(Lead).filter_by(id=lead.id).first()
    assert fetched.apollo_contact_id == "ct-abc"
    assert fetched.intake_source == "apollo_search"


def test_email_apollo_sequence_columns_round_trip(session):
    c, lead = _make_lead(session)
    e = Email(
        lead_id=lead.id,
        campaign_id=c.id,
        subject="s",
        body="b",
        apollo_sequence_id="seq-1",
        apollo_step_id="step-1",
        apollo_enrollment_id="enr-9",
        apollo_pushed_at=datetime(2026, 5, 23, 12, 0),
        apollo_message_id="msg-77",
    )
    session.add(e)
    session.commit()

    fetched = session.query(Email).filter_by(id=e.id).first()
    assert fetched.apollo_sequence_id == "seq-1"
    assert fetched.apollo_step_id == "step-1"
    assert fetched.apollo_enrollment_id == "enr-9"
    assert fetched.apollo_pushed_at == datetime(2026, 5, 23, 12, 0)
    assert fetched.apollo_message_id == "msg-77"


def test_send_sent_via_default_is_apollo(session):
    c, lead = _make_lead(session)
    e = Email(lead_id=lead.id, campaign_id=c.id, subject="s", body="b")
    session.add(e)
    session.commit()

    snd = Send(
        email_id=e.id,
        sent_from_inbox="connect@trispokeservices.com",
        sent_at=datetime(2026, 5, 23, 12, 0),
        status="sent",
        apollo_mailbox_id="mb1",
    )
    session.add(snd)
    session.commit()

    fetched = session.query(Send).filter_by(id=snd.id).first()
    assert fetched.sent_via == "apollo"
    assert fetched.apollo_mailbox_id == "mb1"


def test_apollo_sync_state_one_row_per_campaign(session):
    c, _ = _make_lead(session)
    state = ApolloSyncState(
        campaign_id=c.id,
        last_synced_at=datetime(2026, 5, 23, 0, 0),
        last_event_cursor="cursor-abc",
    )
    session.add(state)
    session.commit()

    # FK unique constraint: a second row for the same campaign should fail.
    state2 = ApolloSyncState(campaign_id=c.id)
    session.add(state2)
    with pytest.raises(Exception):
        session.commit()


def test_lifecycle_statuses_present():
    from trispoke.db.models import LeadStatus

    expected = {
        "new", "enriched", "drafted", "qc_pending", "qc_flagged",
        "approved", "queued_in_apollo", "sent", "replied", "bounced",
        "follow_up_pending", "closed_no_reply", "unsubscribed",
    }
    actual = {s.value for s in LeadStatus}
    assert expected.issubset(actual), expected - actual


def test_event_vocabulary_extended():
    from trispoke.db.models import EventType

    expected_new = {
        "qc_passed", "qc_flagged",
        "apollo_contact_created", "apollo_sequence_created",
        "apollo_contact_enrolled", "apollo_message_sent",
        "apollo_message_opened", "apollo_message_replied",
        "apollo_message_bounced", "apollo_message_unsubscribed",
    }
    expected_legacy = {"email_sent", "reply_received", "bounce_received"}
    actual = {e.value for e in EventType}
    assert expected_new.issubset(actual)
    assert expected_legacy.issubset(actual)
