"""V1.5: QC columns on the Email model round-trip and accept the flag schema
the qc_checker module will emit."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import Base, Campaign, Email, Lead


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _email_for_new_lead(session):
    c = Campaign(name="c", mode="apollo", settings_json="{}")
    session.add(c)
    session.commit()
    lead = Lead(
        campaign_id=c.id, email="a@b.com", source_row_json="{}", status="drafted"
    )
    session.add(lead)
    session.commit()
    e = Email(lead_id=lead.id, campaign_id=c.id, subject="s", body="b")
    session.add(e)
    session.commit()
    return e


def test_qc_columns_persist_flag_payload(session):
    e = _email_for_new_lead(session)
    flags = [
        {"type": "blank_subject", "severity": "error", "detail": None},
        {
            "type": "company_name_mismatch",
            "severity": "warning",
            "detail": "email says 'NexusStaff' but lead.company_name is 'Nexus Staffing'",
        },
    ]
    e.qc_status = "flagged"
    e.qc_checked_at = datetime(2026, 5, 23, 12, 0)
    e.qc_flags_json = json.dumps(flags)
    e.qc_model_used = "qwen3:8b"
    session.commit()

    fetched = session.query(Email).filter_by(id=e.id).first()
    assert fetched.qc_status == "flagged"
    assert fetched.qc_checked_at == datetime(2026, 5, 23, 12, 0)
    assert fetched.qc_model_used == "qwen3:8b"
    decoded = json.loads(fetched.qc_flags_json)
    assert len(decoded) == 2
    assert decoded[0]["severity"] == "error"
    assert decoded[1]["type"] == "company_name_mismatch"


def test_qc_status_enum_values_present():
    from trispoke.db.models import QCStatus

    assert {s.value for s in QCStatus} == {"pending", "passed", "flagged"}
