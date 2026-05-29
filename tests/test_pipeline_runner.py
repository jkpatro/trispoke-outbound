"""V1.5.2 — pipeline_runner picks up status='new' leads regardless of intake
source and runs them through enrich → pain → generate → QC."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import (
    Base,
    Campaign,
    Email,
    Lead,
    PainAnalysis,
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
        "trispoke.scheduler.pipeline_runner.get_session", _fake_get_session
    )
    return Session


def _seed_new_lead(Session, intake_source="manual_form"):
    s = Session()
    c = Campaign(name="c", mode="local_only", settings_json="{}")
    s.add(c); s.commit()
    lead = Lead(
        campaign_id=c.id, email="a@x.com", first_name="A", last_name="B",
        title="CTO", company_name="X", company_domain="x.com",
        source_row_json="{}", status="new",
        intake_source=intake_source,
    )
    s.add(lead); s.commit()
    lead_id = lead.id
    s.close()
    return lead_id


def _patch_router_and_apollo(monkeypatch):
    """Mock router.generate_email to return a fixed draft + Apollo to skip enrichment."""
    fake_router = MagicMock()
    draft = SimpleNamespace(
        subject="hello there", body="Dear A,\n\nBody is long enough to clear "
        "the 40-word minimum and contains the lead's first name A throughout "
        "this paragraph so the salutation check passes too.\n\nBest,\nSender",
        model_used="local:qwen3:8b",
        tokens_used=120,
        generation_seconds=2.0,
    )
    fake_router.generate_email.return_value = draft
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.LLMRouter", lambda: fake_router
    )

    fake_apollo = MagicMock()
    fake_apollo.enrich_organization.return_value = {
        "name": "X Co", "estimated_num_employees": 50,
        "industry": "Staffing & Recruiting", "city": "Toronto", "state": "ON",
    }
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.ApolloClient", lambda: fake_apollo
    )
    return fake_router, fake_apollo


def _stub_qc_pass(monkeypatch):
    """Skip the real Ollama-backed QC; return clean."""
    from trispoke.llm.qc_checker import QCResult
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.qc_check",
        lambda lead, pain, email: QCResult(
            status="passed", flags=[], model_used="qwen3:8b", elapsed_seconds=0.1,
        ),
    )


def _stub_qc_flagged(monkeypatch):
    from trispoke.llm.qc_checker import QCFlag, QCResult
    monkeypatch.setattr(
        "trispoke.scheduler.pipeline_runner.qc_check",
        lambda lead, pain, email: QCResult(
            status="flagged",
            flags=[QCFlag("ai_cliche_check", "warning", "leverage")],
            model_used="qwen3:8b", elapsed_seconds=0.1,
        ),
    )


# ---------------------------------------------------------------------------


def test_pipeline_processes_a_new_manual_lead_end_to_end(memdb, monkeypatch):
    Session = memdb
    lead_id = _seed_new_lead(Session, intake_source="manual_form")
    _patch_router_and_apollo(monkeypatch)
    _stub_qc_pass(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()

    assert counts["enriched"] == 1
    assert counts["generated"] == 1
    assert counts["qc_passed"] == 1

    s = Session()
    lead = s.get(Lead, lead_id)
    assert lead.status == "drafted"          # passed QC → drafted
    assert lead.company_size == "50"          # enrichment fired
    assert lead.company_location == "Toronto, ON"
    email = s.query(Email).filter_by(lead_id=lead_id).first()
    assert email is not None
    assert email.qc_status == "passed"
    pain = s.query(PainAnalysis).filter_by(lead_id=lead_id).first()
    assert pain is not None
    s.close()


def test_pipeline_flagged_lead_lands_in_qc_flagged_status(memdb, monkeypatch):
    Session = memdb
    lead_id = _seed_new_lead(Session, intake_source="apollo_search")
    _patch_router_and_apollo(monkeypatch)
    _stub_qc_flagged(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()
    assert counts["qc_flagged"] == 1

    s = Session()
    lead = s.get(Lead, lead_id)
    assert lead.status == "qc_flagged"
    s.close()


def test_pipeline_per_lead_failure_rolls_back_to_new(memdb, monkeypatch):
    Session = memdb
    lead_id = _seed_new_lead(Session)
    fake_router, _ = _patch_router_and_apollo(monkeypatch)
    fake_router.generate_email.side_effect = RuntimeError("LLM down")
    _stub_qc_pass(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()

    # Enrichment counted, but generation failed
    assert counts["enriched"] == 1
    assert counts["generated"] == 0
    assert counts["skipped"] == 1

    s = Session()
    lead = s.get(Lead, lead_id)
    assert lead.status == "new"  # rolled back for retry next cycle
    s.close()


def test_pipeline_no_new_leads_returns_zeros(memdb, monkeypatch):
    Session = memdb
    _patch_router_and_apollo(monkeypatch)
    _stub_qc_pass(monkeypatch)

    from trispoke.scheduler.pipeline_runner import PipelineRunner
    counts = PipelineRunner().run_once()
    assert counts == {"enriched": 0, "generated": 0, "qc_flagged": 0,
                      "qc_passed": 0, "auto_approved": 0,
                      "skipped": 0, "errored": 0}
