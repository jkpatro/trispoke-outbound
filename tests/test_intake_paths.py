"""V1.5 tests for the three intake paths.

`apollo_csv` is exercised by the existing test_smoke. Here we cover the
two new paths: `manual_form` and `apollo_search`.
"""

from __future__ import annotations

import json
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

    # The intake helpers do `from trispoke.db.session import get_session`
    # at call time, so patch the real binding.
    monkeypatch.setattr("trispoke.db.session.get_session", _fake_get_session)
    monkeypatch.setattr(
        "trispoke.ui.pages.campaign_settings.get_session", _fake_get_session
    )
    return Session


def _seed_campaign(Session, name="c1") -> int:
    s = Session()
    c = Campaign(name=name, mode="apollo", settings_json="{}")
    s.add(c)
    s.commit()
    cid = c.id
    s.close()
    return cid


def test_manual_form_inserts_with_correct_intake_source(memdb):
    Session = memdb
    cid = _seed_campaign(Session, "c-manual")

    from trispoke.ui.pages.campaign_settings import _insert_manual_lead

    lead_id = _insert_manual_lead(
        campaign_name="c-manual",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        title="CTO",
        company_name="Analytical Engines",
        company_domain="ae.example",
        linkedin_url=None,
    )

    s2 = Session()
    lead = s2.get(Lead, lead_id)
    assert lead.intake_source == "manual_form"
    assert lead.status == "new"  # NOT auto-generated
    assert lead.email == "ada@example.com"
    s2.close()


def test_manual_form_does_not_auto_generate(memdb):
    Session = memdb
    _seed_campaign(Session, "c-no-auto")

    from trispoke.ui.pages.campaign_settings import _insert_manual_lead

    _insert_manual_lead(
        campaign_name="c-no-auto",
        first_name="X", last_name="Y", email="xy@example.com",
        title="t", company_name="co", company_domain="co.com",
        linkedin_url=None,
    )

    from trispoke.db.models import Email

    s2 = Session()
    assert s2.query(Email).count() == 0  # no draft generated
    assert s2.query(Lead).filter_by(status="new").count() == 1
    s2.close()


def test_search_intake_inserts_with_correct_intake_source(memdb):
    Session = memdb
    _seed_campaign(Session, "c-search")

    apollo = MagicMock()
    apollo.search_contacts.return_value = {
        "contacts": [
            {
                "id": "ct-1",
                "first_name": "Marcus",
                "last_name": "Allen",
                "email": "marcus@example.com",
                "title": "CEO",
                "organization": {"name": "Lumen", "website_url": "lumen.example"},
                "linkedin_url": "https://linkedin.com/in/marcus",
            },
            {
                "id": "ct-2",
                "first_name": "Priya",
                "last_name": "Sharma",
                "email": "priya@example.com",
                "title": "VP",
                "organization": {"name": "Northwind", "website_url": "nw.example"},
                "linkedin_url": None,
            },
            # one without email — must be skipped
            {
                "id": "ct-3",
                "first_name": "No",
                "last_name": "Email",
                "title": "Mgr",
                "organization": {"name": "X"},
            },
        ]
    }

    from trispoke.ui.pages.campaign_settings import _bulk_add_search_results

    inserted = _bulk_add_search_results(
        apollo, {"person_titles": ["CEO"]}, "c-search"
    )

    s2 = Session()
    rows = s2.query(Lead).filter_by(intake_source="apollo_search").all()
    s2.close()
    assert inserted == 2
    assert len(rows) == 2
    assert {r.apollo_contact_id for r in rows} == {"ct-1", "ct-2"}
    # Status stays 'new' — search-imported leads run through the normal pipeline.
    assert all(r.status == "new" for r in rows)


def test_search_intake_skips_duplicates(memdb):
    Session = memdb
    cid = _seed_campaign(Session, "c-dups")

    # Pre-existing lead with the email we'll try to import.
    s = Session()
    s.add(Lead(
        campaign_id=cid, email="dup@example.com", first_name="Already",
        source_row_json="{}", status="new", intake_source="apollo_csv",
    ))
    s.commit()
    s.close()

    apollo = MagicMock()
    apollo.search_contacts.return_value = {
        "contacts": [
            {"id": "ct-1", "email": "dup@example.com", "first_name": "Dup",
             "last_name": "Lead", "organization": {}},
            {"id": "ct-2", "email": "new@example.com", "first_name": "New",
             "last_name": "Lead", "organization": {}},
        ]
    }

    from trispoke.ui.pages.campaign_settings import _bulk_add_search_results

    inserted = _bulk_add_search_results(apollo, {}, "c-dups")
    assert inserted == 1
    s2 = Session()
    assert s2.query(Lead).count() == 2  # 1 seeded + 1 new
    s2.close()


def test_search_intake_paginates_until_empty(memdb):
    Session = memdb
    _seed_campaign(Session, "c-page")

    apollo = MagicMock()
    apollo.search_contacts.side_effect = [
        # full page of 25
        {"contacts": [
            {"id": f"ct-{i}", "email": f"a{i}@x.com", "first_name": f"L{i}",
             "last_name": "n", "organization": {}}
            for i in range(25)
        ]},
        # partial page → loop stops
        {"contacts": [
            {"id": "ct-final", "email": "fin@x.com", "first_name": "F",
             "last_name": "n", "organization": {}}
        ]},
    ]

    from trispoke.ui.pages.campaign_settings import _bulk_add_search_results

    inserted = _bulk_add_search_results(apollo, {}, "c-page")
    assert inserted == 26
    assert apollo.search_contacts.call_count == 2
