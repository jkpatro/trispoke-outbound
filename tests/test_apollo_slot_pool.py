"""V1.5.1 — slot-pool push worker semantics.

Verifies:
  - Allocation picks the longest-idle free slot (round-robin)
  - Reuse rule: in_use + terminal status counts as free
  - Reuse blocked: in_use + scheduled status is NOT reallocated
  - Push flow per email: template PUT + enroll + (send_now if requested)
  - Per-email failure rolls back the slot allocation
  - Backpressure: when no slots are free, surplus emails are left in approved
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import ApolloSlot, Base, Campaign, Email, Lead


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
        "trispoke.sender.apollo_push_worker.get_session", _fake_get_session
    )
    return Session


def _seed(Session, n_slots=3, n_emails=1, send_mode="scheduled"):
    s = Session()
    c = Campaign(name="c", mode="apollo", settings_json="{}")
    s.add(c)
    s.commit()
    for i in range(n_slots):
        s.add(ApolloSlot(
            slot_name=f"slot-{i:03d}",
            sequence_id=f"seq-{i}",
            step_id=f"step-{i}",
            touch_id=f"touch-{i}",
            template_id=f"tmpl-{i}",
            mailbox_id="mb-1",
            schedule_id="sched-1",
            status="free",
        ))
    s.commit()
    email_ids: list[int] = []
    for i in range(n_emails):
        lead = Lead(
            campaign_id=c.id, email=f"x{i}@x.com", first_name=f"L{i}",
            source_row_json="{}", status="approved",
        )
        s.add(lead); s.commit()
        e = Email(
            lead_id=lead.id, campaign_id=c.id,
            subject=f"hi {i}", body=f"body for lead {i}",
            send_mode=send_mode,
        )
        s.add(e); s.commit()
        email_ids.append(e.id)
    s.close()
    return email_ids


def _make_apollo_mock():
    apollo = MagicMock()
    counter = {"n": 0, "msg": 100}
    def _contact(lead):
        counter["n"] += 1
        return {"id": f"ct-{counter['n']}", "email": getattr(lead, "email", "")}
    apollo.create_or_update_contact.side_effect = _contact
    apollo.update_template.return_value = {}
    apollo.enroll_contact_in_sequence.return_value = {"id": "enr-anon"}
    def _find(seq_id, ct_id):
        counter["msg"] += 1
        return f"msg-{counter['msg']}"
    apollo.find_message_for_enrollment.side_effect = _find
    apollo.send_message_now.return_value = {"status": "completed"}
    return apollo


# ---------------------------------------------------------------------------


def test_basic_push_assigns_slot_and_stamps_email(memdb, monkeypatch):
    Session = memdb
    [eid] = _seed(Session, n_slots=2, n_emails=1, send_mode="scheduled")
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    w = ApolloPushWorker()
    w._pace = lambda: None
    pushed = w.run_once()
    assert pushed == 1

    s = Session()
    e = s.get(Email, eid)
    assert e.apollo_sequence_id is not None
    assert e.apollo_message_id is not None
    assert e.apollo_pushed_at is not None
    assert s.get(Lead, e.lead_id).status == "queued_in_apollo"

    used = s.query(ApolloSlot).filter_by(sequence_id=e.apollo_sequence_id).first()
    assert used.status == "in_use"
    assert used.last_message_id == e.apollo_message_id
    assert used.last_message_status == "scheduled"
    s.close()

    # Template was overwritten with this email's content
    args = apollo.update_template.call_args
    assert args.kwargs["subject"] == "hi 0"
    assert args.kwargs["body_text"] == "body for lead 0"


def test_send_now_flow_marks_slot_completed_optimistically(memdb, monkeypatch):
    Session = memdb
    [eid] = _seed(Session, n_slots=1, n_emails=1, send_mode="now")
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    w = ApolloPushWorker()
    w._pace = lambda: None
    w.run_once()

    apollo.send_message_now.assert_called_once()
    s = Session()
    slot = s.query(ApolloSlot).first()
    assert slot.last_message_status == "completed"
    assert slot.last_freed_at is not None
    s.close()


def test_allocator_picks_longest_idle_slot_round_robin(memdb, monkeypatch):
    Session = memdb
    _seed(Session, n_slots=3, n_emails=0)
    # Make slot-001 most-recently used; slot-002 oldest; slot-000 never used.
    s = Session()
    now = datetime.utcnow()
    s001 = s.query(ApolloSlot).filter_by(slot_name="slot-001").first()
    s002 = s.query(ApolloSlot).filter_by(slot_name="slot-002").first()
    s001.last_used_at = now
    s002.last_used_at = now - timedelta(hours=2)
    s.commit()
    s.close()

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )
    w = ApolloPushWorker()

    s2 = Session()
    picked = w._allocate_slot(s2)
    picked_name = picked.slot_name if picked else None
    s2.close()
    # Never-used slot wins (NULLS FIRST).
    assert picked_name == "slot-000"


def test_in_use_slot_with_terminal_status_is_reallocatable(memdb, monkeypatch):
    Session = memdb
    _seed(Session, n_slots=1, n_emails=0)
    s = Session()
    slot = s.query(ApolloSlot).first()
    slot.status = "in_use"
    slot.last_message_id = "msg-old"
    slot.last_message_status = "completed"
    slot.last_used_at = datetime.utcnow() - timedelta(minutes=10)
    s.commit()
    s.close()

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )
    w = ApolloPushWorker()
    s2 = Session()
    picked = w._allocate_slot(s2)
    picked_name = picked.slot_name if picked else None
    s2.close()
    assert picked_name is not None


def test_in_use_slot_with_scheduled_status_is_NOT_reallocatable(memdb, monkeypatch):
    Session = memdb
    _seed(Session, n_slots=1, n_emails=0)
    s = Session()
    slot = s.query(ApolloSlot).first()
    slot.status = "in_use"
    slot.last_message_id = "msg-pending"
    slot.last_message_status = "scheduled"
    s.commit()
    s.close()

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )
    w = ApolloPushWorker()
    s2 = Session()
    picked = w._allocate_slot(s2)
    s2.close()
    assert picked is None


def test_backpressure_when_no_free_slots(memdb, monkeypatch):
    """3 approved emails, only 1 slot — should push 1 and leave 2 in approved."""
    Session = memdb
    _seed(Session, n_slots=1, n_emails=3, send_mode="scheduled")
    apollo = _make_apollo_mock()
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    w = ApolloPushWorker()
    w._pace = lambda: None
    pushed = w.run_once()
    assert pushed == 1

    s = Session()
    queued = s.query(Lead).filter_by(status="queued_in_apollo").count()
    still_approved = s.query(Lead).filter_by(status="approved").count()
    assert queued == 1
    assert still_approved == 2
    s.close()


def test_per_email_apollo_failure_rolls_back_slot(memdb, monkeypatch):
    Session = memdb
    _seed(Session, n_slots=1, n_emails=1, send_mode="scheduled")
    apollo = _make_apollo_mock()
    apollo.enroll_contact_in_sequence.side_effect = RuntimeError("Apollo down")
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    w = ApolloPushWorker()
    w._pace = lambda: None
    pushed = w.run_once()
    assert pushed == 0

    s = Session()
    slot = s.query(ApolloSlot).first()
    # Slot was allocated then rolled back to free.
    assert slot.status == "free"
    assert slot.last_message_id is None
    # Email stays approved, will retry next cycle.
    assert s.query(Lead).filter_by(status="approved").count() == 1
    s.close()
