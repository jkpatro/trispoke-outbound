"""V1.5.2 — globally-unsubscribed addresses are filtered out at all 4 intake paths."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trispoke.db.models import Base, Campaign, Lead


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

    monkeypatch.setattr("trispoke.db.session.get_session", _fake_get_session)
    monkeypatch.setattr(
        "trispoke.ui.pages.campaign_settings.get_session", _fake_get_session
    )
    monkeypatch.setattr(
        "trispoke.import_apollo.get_session", _fake_get_session
    )
    return Session


def _seed_campaign(Session, name="c1") -> int:
    s = Session()
    c = Campaign(name=name, mode="hybrid_smart", settings_json="{}")
    s.add(c); s.commit()
    cid = c.id
    s.close()
    return cid


def _seed_unsubscribed(Session, email: str, in_campaign_name="other-camp"):
    """Add a lead in status='unsubscribed' to simulate someone who opted out
    of a previous campaign."""
    s = Session()
    c = s.query(Campaign).filter_by(name=in_campaign_name).first()
    if c is None:
        c = Campaign(name=in_campaign_name, mode="hybrid", settings_json="{}")
        s.add(c); s.commit()
    s.add(Lead(
        campaign_id=c.id, email=email, first_name="Prior",
        source_row_json="{}", status="unsubscribed",
        intake_source="apollo_csv",
    ))
    s.commit(); s.close()


# ---------- helper function unit tests ----------


def test_is_unsubscribed_true_when_lead_exists_with_status(memdb):
    Session = memdb
    _seed_unsubscribed(Session, "opt-out@example.com")

    from trispoke.db.unsubscribe import is_unsubscribed
    s = Session()
    assert is_unsubscribed(s, "opt-out@example.com") is True
    assert is_unsubscribed(s, "fresh@example.com") is False
    s.close()


def test_is_unsubscribed_is_case_insensitive(memdb):
    Session = memdb
    _seed_unsubscribed(Session, "Opt-Out@example.com")

    from trispoke.db.unsubscribe import is_unsubscribed
    s = Session()
    assert is_unsubscribed(s, "OPT-OUT@EXAMPLE.COM") is True
    s.close()


def test_is_unsubscribed_handles_empty_and_whitespace(memdb):
    Session = memdb
    from trispoke.db.unsubscribe import is_unsubscribed
    s = Session()
    assert is_unsubscribed(s, "") is False
    assert is_unsubscribed(s, "   ") is False
    s.close()


# ---------- manual_form intake ----------


def test_manual_form_rejects_unsubscribed(memdb):
    Session = memdb
    _seed_campaign(Session, "c1")
    _seed_unsubscribed(Session, "opt-out@example.com")

    from trispoke.ui.pages.campaign_settings import _insert_manual_lead

    with pytest.raises(ValueError, match="globally unsubscribed"):
        _insert_manual_lead(
            campaign_name="c1",
            first_name="X", last_name="Y", email="opt-out@example.com",
            title="t", company_name="co", company_domain="co.com",
            linkedin_url=None,
        )

    s = Session()
    assert s.query(Lead).filter_by(email="opt-out@example.com",
                                    intake_source="manual_form").count() == 0
    s.close()


def test_manual_form_accepts_non_unsubscribed(memdb):
    Session = memdb
    _seed_campaign(Session, "c1")

    from trispoke.ui.pages.campaign_settings import _insert_manual_lead

    lead_id = _insert_manual_lead(
        campaign_name="c1",
        first_name="X", last_name="Y", email="fresh@example.com",
        title="t", company_name="co", company_domain="co.com",
        linkedin_url=None,
    )
    assert lead_id > 0


# ---------- apollo_search intake ----------


def test_apollo_search_filters_unsubscribed(memdb):
    Session = memdb
    _seed_campaign(Session, "c-search")
    _seed_unsubscribed(Session, "out@example.com")

    apollo = MagicMock()
    apollo.search_contacts.return_value = {
        "contacts": [
            {"id": "c1", "email": "out@example.com", "first_name": "Out",
             "last_name": "L", "organization": {}},
            {"id": "c2", "email": "in@example.com", "first_name": "In",
             "last_name": "L", "organization": {}},
        ]
    }

    from trispoke.ui.pages.campaign_settings import _bulk_add_search_results

    inserted = _bulk_add_search_results(apollo, {}, "c-search")
    assert inserted == 1
    s = Session()
    rows = s.query(Lead).filter_by(intake_source="apollo_search").all()
    emails = {r.email for r in rows}
    assert emails == {"in@example.com"}
    s.close()


# ---------- apollo_csv intake (via import_apollo) ----------


def test_import_apollo_skips_unsubscribed(memdb, tmp_path, monkeypatch):
    Session = memdb
    _seed_unsubscribed(Session, "out@example.com")

    # Build a tiny .xlsx using openpyxl (already a project dep)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Email", "First Name", "Last Name", "Title", "Company"])
    ws.append(["out@example.com", "Out", "Lead", "CEO", "X"])
    ws.append(["in@example.com",  "In",  "Lead", "CEO", "Y"])
    xlsx_path = tmp_path / "test.xlsx"
    wb.save(xlsx_path)

    from trispoke.import_apollo import import_leads
    import_leads(str(xlsx_path), "c-csv")

    s = Session()
    c_csv = s.query(Campaign).filter_by(name="c-csv").first()
    new_rows = s.query(Lead).filter_by(campaign_id=c_csv.id).all()
    emails = {r.email for r in new_rows}
    assert "in@example.com" in emails
    assert "out@example.com" not in emails
    s.close()
