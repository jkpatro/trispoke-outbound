"""End-to-end automation guardrails.

Tests:
  - auto-approval policy (never / after_warmup / always)
  - pipeline runner integration with auto-approval
  - push worker bounce-rate auto-pause
  - reply webhook fires only on positive classification
  - 2-step slot create_sequence
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import (
    ApolloSlot,
    Base,
    Campaign,
    Email,
    Lead,
    Send,
)


# ---------------------------------------------------------------------------
# auto-approval policy unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def memdb():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session


def _campaign(s, name="c", mode="after_warmup", threshold=20):
    c = Campaign(
        name=name, mode="local_only", settings_json="{}",
        auto_approve_mode=mode, warmup_threshold=threshold,
    )
    s.add(c); s.commit()
    return c


def _seed_completed_sends(s, campaign, n):
    """Insert n successful Send rows for this campaign."""
    lead = Lead(
        campaign_id=campaign.id, email="x@y.com", source_row_json="{}",
        status="sent",
    )
    s.add(lead); s.commit()
    email = Email(
        lead_id=lead.id, campaign_id=campaign.id, subject="s", body="b",
    )
    s.add(email); s.commit()
    for _ in range(n):
        s.add(Send(
            email_id=email.id, sent_from_inbox="x@y.com",
            sent_at=datetime.utcnow(), status="sent", sent_via="apollo",
        ))
    s.commit()


def test_auto_approve_mode_never_always_false(memdb):
    Session = memdb
    s = Session()
    c = _campaign(s, mode="never")
    from trispoke.db.auto_approval import should_auto_approve
    assert should_auto_approve(s, c) is False
    s.close()


def test_auto_approve_mode_always_returns_true_immediately(memdb):
    Session = memdb
    s = Session()
    c = _campaign(s, mode="always")
    from trispoke.db.auto_approval import should_auto_approve
    assert should_auto_approve(s, c) is True
    s.close()


def test_auto_approve_after_warmup_false_before_threshold(memdb):
    Session = memdb
    s = Session()
    c = _campaign(s, mode="after_warmup", threshold=20)
    _seed_completed_sends(s, c, 5)
    from trispoke.db.auto_approval import should_auto_approve
    assert should_auto_approve(s, c) is False
    s.close()


def test_auto_approve_after_warmup_true_at_threshold(memdb):
    Session = memdb
    s = Session()
    c = _campaign(s, mode="after_warmup", threshold=20)
    _seed_completed_sends(s, c, 20)
    from trispoke.db.auto_approval import should_auto_approve
    assert should_auto_approve(s, c) is True
    s.close()


# ---------------------------------------------------------------------------
# pipeline runner + auto-approval integration
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_memdb(monkeypatch):
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
        "trispoke.scheduler.pipeline_runner.get_session", _fake_get_session
    )
    return Session


def _patch_pipeline_deps(monkeypatch):
    fake_router = MagicMock()
    fake_router.generate_email.return_value = SimpleNamespace(
        subject="hello", body=("Dear A,\n\n" + ("body " * 20) + "\n\nBest,\nT"),
        model_used="local:qwen3:8b", tokens_used=120, generation_seconds=2.0,
    )
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.LLMRouter", lambda: fake_router
    )
    fake_apollo = MagicMock()
    fake_apollo.enrich_organization.return_value = {}
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.ApolloClient", lambda: fake_apollo
    )
    from trispoke.llm.qc_checker import QCResult
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.qc_check",
        lambda lead, pain, email: QCResult(
            status="passed", flags=[], model_used="qwen3:8b", elapsed_seconds=0.1,
        ),
    )


def test_pipeline_auto_approves_when_policy_is_always(pipeline_memdb, monkeypatch):
    Session = pipeline_memdb
    s = Session()
    c = Campaign(
        name="c", mode="local_only", settings_json="{}",
        auto_approve_mode="always", warmup_threshold=0,
    )
    s.add(c); s.commit()
    lead = Lead(
        campaign_id=c.id, email="a@x.com", first_name="A", last_name="B",
        title="t", company_name="X", company_domain="x.com",
        source_row_json="{}", status="new",
    )
    s.add(lead); s.commit()
    lead_id = lead.id
    s.close()

    _patch_pipeline_deps(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()
    assert counts["auto_approved"] == 1

    s2 = Session()
    assert s2.get(Lead, lead_id).status == "approved"
    e = s2.query(Email).filter_by(lead_id=lead_id).first()
    assert e.send_mode == "scheduled"
    s2.close()


def test_pipeline_does_not_auto_approve_when_policy_is_never(
    pipeline_memdb, monkeypatch
):
    Session = pipeline_memdb
    s = Session()
    c = Campaign(
        name="c", mode="local_only", settings_json="{}",
        auto_approve_mode="never", warmup_threshold=0,
    )
    s.add(c); s.commit()
    lead = Lead(
        campaign_id=c.id, email="a@x.com", first_name="A", last_name="B",
        title="t", company_name="X", company_domain="x.com",
        source_row_json="{}", status="new",
    )
    s.add(lead); s.commit()
    lead_id = lead.id
    s.close()

    _patch_pipeline_deps(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()
    assert counts["auto_approved"] == 0
    assert counts["qc_passed"] == 1

    s2 = Session()
    assert s2.get(Lead, lead_id).status == "drafted"  # waits for human
    s2.close()


# ---------------------------------------------------------------------------
# push worker bounce-rate guard
# ---------------------------------------------------------------------------


@pytest.fixture
def push_memdb(monkeypatch):
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


def test_push_worker_pauses_when_bounce_rate_exceeds_threshold(
    push_memdb, monkeypatch
):
    Session = push_memdb
    s = Session()
    c = Campaign(name="c", mode="hybrid_smart", settings_json="{}")
    s.add(c); s.commit()
    s.add(ApolloSlot(
        slot_name="s1", sequence_id="seq-1", step_id="st-1",
        touch_id="t-1", template_id="tm-1", status="free",
    ))
    lead = Lead(
        campaign_id=c.id, email="a@x.com", first_name="A",
        source_row_json="{}", status="approved",
    )
    s.add(lead); s.commit()
    s.add(Email(
        lead_id=lead.id, campaign_id=c.id, subject="hi", body="body",
    ))
    # 50 sends, 10 bounces (20% > 3% threshold)
    fake_email = s.query(Email).first()
    for i in range(40):
        s.add(Send(
            email_id=fake_email.id, sent_from_inbox="x", sent_at=datetime.utcnow(),
            status="sent", sent_via="apollo",
        ))
    for _ in range(10):
        s.add(Send(
            email_id=fake_email.id, sent_from_inbox="x", sent_at=datetime.utcnow(),
            status="bounced", sent_via="apollo",
        ))
    s.commit(); s.close()

    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: MagicMock()
    )
    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    pushed = ApolloPushWorker().run_once()
    assert pushed == 0  # paused — bounce rate too high


def test_push_worker_runs_normally_when_bounce_rate_is_low(
    push_memdb, monkeypatch
):
    Session = push_memdb
    s = Session()
    c = Campaign(name="c", mode="hybrid_smart", settings_json="{}")
    s.add(c); s.commit()
    s.add(ApolloSlot(
        slot_name="s1", sequence_id="seq-1", step_id="st-1",
        touch_id="t-1", template_id="tm-1", status="free",
    ))
    lead = Lead(
        campaign_id=c.id, email="a@x.com", first_name="A",
        source_row_json="{}", status="approved",
    )
    s.add(lead); s.commit()
    s.add(Email(
        lead_id=lead.id, campaign_id=c.id, subject="hi", body="body",
    ))
    fake_email = s.query(Email).first()
    # 50 sends, 0 bounces
    for _ in range(50):
        s.add(Send(
            email_id=fake_email.id, sent_from_inbox="x", sent_at=datetime.utcnow(),
            status="sent", sent_via="apollo",
        ))
    s.commit(); s.close()

    apollo = MagicMock()
    apollo.create_or_update_contact.return_value = {"id": "ct-1"}
    apollo.update_template.return_value = {}
    apollo.enroll_contact_in_sequence.return_value = {"id": "enr-1"}
    apollo.find_message_for_enrollment.return_value = "msg-1"
    monkeypatch.setattr(
        "trispoke.sender.apollo_push_worker.ApolloClient", lambda: apollo
    )

    from trispoke.sender.apollo_push_worker import ApolloPushWorker
    w = ApolloPushWorker()
    w._pace = lambda: None
    pushed = w.run_once()
    assert pushed == 1


# ---------------------------------------------------------------------------
# reply webhook notifier
# ---------------------------------------------------------------------------


def test_notify_positive_reply_no_op_when_url_blank(monkeypatch):
    from trispoke.notifier import notify_positive_reply
    monkeypatch.setattr("httpx.post", lambda *_a, **_kw: pytest.fail(
        "should not POST when url is None"
    ))
    ok = notify_positive_reply(
        None, campaign="c", lead_email="x@x.com", lead_name="X",
        reply_subject="Re", reply_excerpt="yes",
    )
    assert ok is False


def test_notify_positive_reply_posts_when_url_set(monkeypatch):
    calls = []
    class _R:
        status_code = 200
    def _post(url, json=None, timeout=None):
        calls.append((url, json))
        return _R()
    monkeypatch.setattr("httpx.post", _post)

    from trispoke.notifier import notify_positive_reply
    ok = notify_positive_reply(
        "https://hooks.example/x", campaign="c", lead_email="x@x.com",
        lead_name="X Y", reply_subject="Re: hello",
        reply_excerpt="yes interested",
    )
    assert ok is True
    assert calls and calls[0][0] == "https://hooks.example/x"
    payload = calls[0][1]
    assert payload["event"] == "positive_reply"
    assert payload["lead_email"] == "x@x.com"
    assert "Positive reply" in payload["text"]


# ---------------------------------------------------------------------------
# 2-step slot create_sequence
# ---------------------------------------------------------------------------


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.raise_for_status.return_value = None
    return r


def test_create_sequence_with_followup_appends_step_two(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    from trispoke.config import get_settings
    get_settings.cache_clear()

    from trispoke.apollo.client import ApolloClient
    ac = ApolloClient()
    ac.client = MagicMock()

    ac.client.request.side_effect = [
        # 1. create sequence
        _resp({"emailer_campaign": {"id": "seq-1"}}),
        # 2. create step 1
        _resp({"emailer_step": {"id": "step-1"}}),
        # 3. GET sequence to discover touch+template
        _resp({"emailer_campaign": {"id": "seq-1"},
               "emailer_touches": [
                   {"id": "touch-1", "emailer_step_id": "step-1",
                    "emailer_template_id": "tmpl-1"},
               ]}),
        # 4. PUT template 1
        _resp({"emailer_template": {"id": "tmpl-1"}}),
        # 5. approve touch 1
        _resp({"ok": True}),
        # 6. PUT sequence (attach schedule)
        _resp({"emailer_campaign": {"id": "seq-1"}}),
        # 7. create step 2 (follow-up)
        _resp({"emailer_step": {"id": "step-2"}}),
        # 8. GET sequence to discover step-2's touch+template
        _resp({"emailer_campaign": {"id": "seq-1"},
               "emailer_touches": [
                   {"id": "touch-1", "emailer_step_id": "step-1",
                    "emailer_template_id": "tmpl-1"},
                   {"id": "touch-2", "emailer_step_id": "step-2",
                    "emailer_template_id": "tmpl-2"},
               ]}),
        # 9. PUT template 2
        _resp({"emailer_template": {"id": "tmpl-2"}}),
        # 10. approve touch 2
        _resp({"ok": True}),
    ]

    out = ac.create_sequence(
        "trispoke-slot-001",
        step_template={"subject": "hi", "body_html": "h", "body_text": "t",
                       "mailbox_id": "mb-1", "schedule_id": "sch-1"},
        follow_up_template={"subject": "re: hi", "body_html": "fh",
                             "body_text": "ft", "wait_days_after": 4},
    )
    assert out["sequence_id"] == "seq-1"
    assert out["step_id"] == "step-1"
    assert out["template_id"] == "tmpl-1"
    assert out["followup_step_id"] == "step-2"
    assert out["followup_touch_id"] == "touch-2"
    assert out["followup_template_id"] == "tmpl-2"
