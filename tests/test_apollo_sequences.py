"""Unit tests for the V1.5 Apollo sequence / contact / events methods.

Mocks the underlying httpx.Client at the ApolloClient instance boundary so
no network calls happen. Covers the seven methods added in Prompt 1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_client(monkeypatch):
    """ApolloClient with its internal httpx.Client swapped for a MagicMock."""
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    from trispoke.config import get_settings

    get_settings.cache_clear()

    from trispoke.apollo.client import ApolloClient

    ac = ApolloClient()
    ac.client = MagicMock()
    return ac


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# create_or_update_contact
# ---------------------------------------------------------------------------


def test_create_or_update_contact_posts_mapped_fields(fake_client):
    fake_client.client.request.return_value = _resp(
        {"contact": {"id": "abc123", "email": "a@b.com"}}
    )
    lead = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        email="a@b.com",
        title="CTO",
        company_name="Analytical Engines",
        company_domain="ae.example",
        linkedin_url="https://linkedin.com/in/ada",
    )

    contact = fake_client.create_or_update_contact(lead)

    assert contact["id"] == "abc123"
    method, url = fake_client.client.request.call_args[0][:2]
    sent_body = fake_client.client.request.call_args[1]["json"]
    assert method == "POST"
    assert url.endswith("/contacts")
    assert sent_body["first_name"] == "Ada"
    assert sent_body["organization_name"] == "Analytical Engines"
    assert sent_body["website_url"] == "ae.example"
    assert "company_name" not in sent_body  # we mapped to organization_name


def test_create_or_update_contact_strips_empty_fields(fake_client):
    fake_client.client.request.return_value = _resp(
        {"contact": {"id": "x", "email": "a@b.com"}}
    )
    lead = SimpleNamespace(
        first_name="Ada", last_name=None, email="a@b.com",
        title=None, company_name=None, company_domain=None, linkedin_url=None,
    )
    fake_client.create_or_update_contact(lead)
    sent = fake_client.client.request.call_args[1]["json"]
    assert sent == {"first_name": "Ada", "email": "a@b.com"}


def test_create_or_update_contact_raises_on_empty_response(fake_client):
    fake_client.client.request.return_value = _resp({})
    lead = SimpleNamespace(first_name="A", email="a@b.com",
                            last_name=None, title=None, company_name=None,
                            company_domain=None, linkedin_url=None)
    with pytest.raises(RuntimeError, match="did not return a contact"):
        fake_client.create_or_update_contact(lead)


# ---------------------------------------------------------------------------
# search_contacts
# ---------------------------------------------------------------------------


def test_search_contacts_posts_query_with_pagination(fake_client):
    fake_client.client.request.return_value = _resp(
        {"people": [{"id": "1"}], "pagination": {"page": 2, "per_page": 10}}
    )
    body = fake_client.search_contacts(
        {"person_titles": ["VP Sales"], "q_keywords": "logistics"},
        page=2,
        page_size=10,
    )
    assert body["pagination"]["page"] == 2
    method, url = fake_client.client.request.call_args[0][:2]
    sent = fake_client.client.request.call_args[1]["json"]
    # Endpoint changed to api_search (old /mixed_people/search is deprecated).
    assert url.endswith("/mixed_people/api_search")
    assert sent["person_titles"] == ["VP Sales"]
    assert sent["q_keywords"] == "logistics"
    assert sent["page"] == 2
    assert sent["per_page"] == 10


# ---------------------------------------------------------------------------
# list_mailboxes
# ---------------------------------------------------------------------------


def test_list_mailboxes_returns_email_accounts(fake_client):
    fake_client.client.request.return_value = _resp(
        {"email_accounts": [{"id": "mb1", "email": "out@trispoke.com"}]}
    )
    boxes = fake_client.list_mailboxes()
    assert boxes == [{"id": "mb1", "email": "out@trispoke.com"}]
    method, url = fake_client.client.request.call_args[0][:2]
    assert method == "GET"
    assert url.endswith("/email_accounts")


def test_list_mailboxes_handles_alt_response_key(fake_client):
    fake_client.client.request.return_value = _resp(
        {"mailboxes": [{"id": "mb2"}]}
    )
    assert fake_client.list_mailboxes() == [{"id": "mb2"}]


# ---------------------------------------------------------------------------
# create_sequence (two API calls — sequence shell + first step)
# ---------------------------------------------------------------------------


def test_create_sequence_does_full_lifecycle(fake_client):
    """V1.5.1: create_sequence now does 6 API calls:
       1. POST /emailer_campaigns        (sequence shell)
       2. POST /emailer_steps             (step + auto-creates touch+template)
       3. GET  /emailer_campaigns/{id}   (discover touch+template ids)
       4. PUT  /emailer_templates/{id}   (populate subject+body)
       5. POST /emailer_touches/{id}/approve  (mark touch reviewed)
       6. PUT  /emailer_campaigns/{id}   (attach schedule)
    """
    fake_client.client.request.side_effect = [
        _resp({"emailer_campaign": {"id": "seq-1"}}),                # 1
        _resp({"emailer_step": {"id": "step-1"}}),                   # 2
        _resp({"emailer_campaign": {"id": "seq-1"},
               "emailer_touches": [
                   {"id": "touch-1", "emailer_step_id": "step-1",
                    "emailer_template_id": "tmpl-1"},
               ]}),                                                  # 3
        _resp({"emailer_template": {"id": "tmpl-1"}}),               # 4
        _resp({"ok": True}),                                          # 5
        _resp({"emailer_campaign": {"id": "seq-1"}}),                # 6
    ]
    out = fake_client.create_sequence(
        "trispoke-slot-001",
        step_template={
            "subject": "hi", "body_html": "<p>hi</p>", "body_text": "hi",
            "wait_days_after": 0, "mailbox_id": "mb-1", "schedule_id": "sch-1",
        },
    )

    assert out["sequence_id"] == "seq-1"
    assert out["step_id"] == "step-1"
    assert out["touch_id"] == "touch-1"
    assert out["template_id"] == "tmpl-1"
    assert out["schedule_id"] == "sch-1"
    assert out["mailbox_id"] == "mb-1"

    calls = fake_client.client.request.call_args_list
    assert calls[3].args[1].endswith("/emailer_templates/tmpl-1")
    assert calls[3].kwargs["json"]["subject"] == "hi"
    assert calls[4].args[1].endswith("/emailer_touches/touch-1/approve")
    assert calls[5].kwargs["json"]["emailer_schedule_id"] == "sch-1"


def test_create_sequence_raises_if_step_missing_id(fake_client):
    fake_client.client.request.side_effect = [
        _resp({"emailer_campaign": {"id": "seq-001"}}),
        _resp({"emailer_step": {}}),  # no id
    ]
    with pytest.raises(RuntimeError, match="did not return a step"):
        fake_client.create_sequence("c", {"subject": "x", "body_html": "y"})


# ---------------------------------------------------------------------------
# enroll_contact_in_sequence
# ---------------------------------------------------------------------------


def test_enroll_contact_in_sequence_wraps_id_as_list(fake_client):
    fake_client.client.request.return_value = _resp({"enrolled": 1})
    fake_client.enroll_contact_in_sequence("seq-1", "ct-9", mailbox_id="mb-7")
    method, url = fake_client.client.request.call_args[0][:2]
    sent = fake_client.client.request.call_args[1]["json"]
    assert method == "POST"
    assert url.endswith("/emailer_campaigns/seq-1/add_contact_ids")
    assert sent["contact_ids"] == ["ct-9"]
    # Apollo requires this — without it the endpoint 422s. Don't drop it.
    assert sent["send_email_from_email_account_id"] == "mb-7"


def test_enroll_contact_send_at_formatted_iso(fake_client):
    fake_client.client.request.return_value = _resp({"enrolled": 1})
    dt = datetime(2026, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
    fake_client.enroll_contact_in_sequence(
        "seq-1", "ct-9", send_at=dt, mailbox_id="mb-7"
    )
    sent = fake_client.client.request.call_args[1]["json"]
    assert sent["send_at"].startswith("2026-06-01T09:30:00")


def test_enroll_contact_falls_back_to_first_active_mailbox(fake_client):
    """If no mailbox_id passed, the client should pick one via list_mailboxes."""
    fake_client.client.request.side_effect = [
        _resp({"email_accounts": [
            {"id": "mb-inactive", "active": False},
            {"id": "mb-good", "active": True},
        ]}),
        _resp({"enrolled": 1}),
    ]
    fake_client.enroll_contact_in_sequence("seq-1", "ct-9")
    enroll_call = fake_client.client.request.call_args_list[1]
    assert enroll_call.kwargs["json"]["send_email_from_email_account_id"] == "mb-good"


# ---------------------------------------------------------------------------
# get_sequence_events
# ---------------------------------------------------------------------------


def test_get_sequence_events_uses_search_endpoint(fake_client):
    """V1.5.1: /emailer_messages 404s on this plan; we use /search instead
    and filter by since-timestamp in code."""
    fake_client.client.request.return_value = _resp(
        {
            "emailer_messages": [
                {"id": "m1", "status": "completed", "sent_at": "2026-05-20T10:00:00Z"},
                {"id": "m2", "status": "opened", "opened_at": "2026-05-20T11:00:00Z"},
                {"id": "m3", "status": "completed", "sent_at": "2026-05-18T10:00:00Z"},
            ]
        }
    )
    since = datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc)
    events = fake_client.get_sequence_events("seq-7", since)
    # m1 + m2 are at/after 2026-05-20; m3 is before → filtered out
    assert {e["id"] for e in events} == {"m1", "m2"}

    method, url = fake_client.client.request.call_args[0][:2]
    params = fake_client.client.request.call_args[1]["params"]
    assert method == "GET"
    assert url.endswith("/emailer_messages/search")
    assert params["emailer_campaign_id"] == "seq-7"


# ---------------------------------------------------------------------------
# get_message_body
# ---------------------------------------------------------------------------


def test_get_message_body_fetches_single_message(fake_client):
    fake_client.client.request.return_value = _resp(
        {"emailer_message": {"id": "m1", "body_text": "Thanks for reaching out!"}}
    )
    msg = fake_client.get_message_body("m1")
    assert msg["body_text"] == "Thanks for reaching out!"
    method, url = fake_client.client.request.call_args[0][:2]
    assert method == "GET"
    assert url.endswith("/emailer_messages/m1")


# ---------------------------------------------------------------------------
# 429 backoff is inherited from existing _retry_request — sanity-check that
# the new methods do go through it.
# ---------------------------------------------------------------------------


def test_new_methods_inherit_429_retry(fake_client, monkeypatch):
    monkeypatch.setattr("trispoke.apollo.client.time.sleep", lambda *_: None)
    rate_limited = _resp({}, status=429)
    success = _resp({"email_accounts": [{"id": "mb1"}]})
    fake_client.client.request.side_effect = [rate_limited, success]
    boxes = fake_client.list_mailboxes()
    assert boxes == [{"id": "mb1"}]
    assert fake_client.client.request.call_count == 2
