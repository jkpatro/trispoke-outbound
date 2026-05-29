"""Tests for the V1.5 QC pipeline.

Covers:
  - each deterministic structural check with positive and negative cases
  - JSON-parsing of the LLM response (valid, mixed-with-prose, garbage)
  - severity mapping (errors block, warnings advise)
  - end-to-end: blank-subject draft → flagged

The LLM-based path is mocked via OllamaClient.generate.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _lead(first="Marcus", last="Allen", company="Lumen Analytics"):
    return SimpleNamespace(
        first_name=first,
        last_name=last,
        email="marcus@lumen.example",
        title="Founder & CEO",
        company_name=company,
        company_industry="SaaS",
    )


def _email(subject="lumen analytics scaling pains", body=None):
    if body is None:
        body = (
            "Dear Marcus,\n\n"
            "Engineering teams at growth-stage SaaS companies often hit the same "
            "operational wall during fast expansion: the processes that worked at "
            "half the size start creating bottlenecks faster than new hires can "
            "clear them. For founders managing growth at Lumen Analytics that "
            "usually shows up as the team spending more time coordinating than "
            "shipping.\n\n"
            "Quick question — is that something you're feeling now, or further "
            "out on the radar?\n\n"
            "Best,\nAlex"
        )
    return SimpleNamespace(subject=subject, body=body)


def _pain():
    return SimpleNamespace(
        chronic="scaling without losing speed",
        acute="time spent coordinating",
        trigger="recent expansion",
    )


# ---------------------------------------------------------------------------
# structural checks
# ---------------------------------------------------------------------------


def test_structural_blank_subject_is_error():
    from trispoke.llm.qc_checker import _structural_checks

    flags = _structural_checks(_lead(), _email(subject="   "))
    assert any(f.type == "blank_subject" and f.severity == "error" for f in flags)


def test_structural_blank_body_is_error():
    from trispoke.llm.qc_checker import _structural_checks

    flags = _structural_checks(_lead(), _email(body=""))
    assert any(f.type == "blank_body" and f.severity == "error" for f in flags)


def test_structural_placeholder_leftover_is_error():
    from trispoke.llm.qc_checker import _structural_checks

    e = _email(body="Hi {first_name},\n\nGreat to chat.\n\nBest,\nA")
    flags = _structural_checks(_lead(), e)
    assert any(f.type == "placeholder_leftover" and f.severity == "error" for f in flags)


def test_structural_subject_too_long_is_warning():
    from trispoke.llm.qc_checker import _structural_checks

    long_subj = "x" * 90
    flags = _structural_checks(_lead(), _email(subject=long_subj))
    assert any(f.type == "subject_too_long" and f.severity == "warning" for f in flags)


def test_structural_salutation_mismatch_when_name_missing():
    from trispoke.llm.qc_checker import _structural_checks

    e = _email(
        body="Hi there,\n\nThis email doesn't mention the name at all and is "
             "padded with enough words to clear the 40-word minimum so we only "
             "trip the salutation check we are testing for here right now ok.\n\nBest,\nA"
    )
    flags = _structural_checks(_lead(first="Marcus"), e)
    assert any(f.type == "salutation_mismatch" for f in flags)


def test_structural_email_in_body_flagged_when_unrelated():
    from trispoke.llm.qc_checker import _structural_checks

    e = _email(
        body="Marcus, check out support@otherdomain.example for context. "
             + " ".join(["filler"] * 50)
    )
    flags = _structural_checks(_lead(), e)
    assert any(f.type == "email_in_body" for f in flags)


def test_structural_clean_email_produces_no_flags():
    from trispoke.llm.qc_checker import _structural_checks

    flags = _structural_checks(_lead(), _email())
    assert flags == [], [f.type for f in flags]


# ---------------------------------------------------------------------------
# LLM JSON parsing
# ---------------------------------------------------------------------------


def _mock_ollama(text, monkeypatch):
    """Patch OllamaClient.generate to return `text`."""
    from trispoke.llm import ollama_client as oc

    monkeypatch.setattr(
        oc.OllamaClient, "generate", lambda self, p, **kw: (text, 100, 0.5)
    )


def test_llm_parses_valid_json(monkeypatch):
    _mock_ollama(
        json.dumps(
            {
                "checks": [
                    {"name": "company_name_consistency", "passed": True},
                    {"name": "person_name_consistency", "passed": True},
                    {"name": "tone_appropriate", "passed": True},
                    {"name": "ai_cliche_check", "passed": True},
                    {"name": "factual_hallucination", "passed": True},
                ]
            }
        ),
        monkeypatch,
    )
    from trispoke.llm.qc_checker import _llm_checks

    flags, model = _llm_checks(_lead(), _pain(), _email())
    assert flags == []
    assert "qwen" in model.lower() or model  # whatever the default model is


def test_llm_parses_json_wrapped_in_prose(monkeypatch):
    _mock_ollama(
        "Sure, here's the analysis:\n"
        + json.dumps(
            {
                "checks": [
                    {"name": "ai_cliche_check", "passed": False,
                     "detail": "starts with 'I hope this finds you well'"},
                ]
            }
        )
        + "\nLet me know if you need more.",
        monkeypatch,
    )
    from trispoke.llm.qc_checker import _llm_checks

    flags, _ = _llm_checks(_lead(), _pain(), _email())
    assert any(f.type == "ai_cliche_check" and f.severity == "warning" for f in flags)


def test_llm_garbage_response_yields_parse_failed_warning(monkeypatch):
    _mock_ollama("totally not JSON, just rambling text from the model", monkeypatch)
    from trispoke.llm.qc_checker import _llm_checks

    flags, _ = _llm_checks(_lead(), _pain(), _email())
    assert any(f.type == "qc_llm_parse_failed" and f.severity == "warning" for f in flags)


def test_llm_company_name_failure_maps_to_error(monkeypatch):
    _mock_ollama(
        json.dumps(
            {
                "checks": [
                    {"name": "company_name_consistency", "passed": False,
                     "detail": "email says 'Apex' but lead.company_name is 'Lumen'"},
                ]
            }
        ),
        monkeypatch,
    )
    from trispoke.llm.qc_checker import _llm_checks

    flags, _ = _llm_checks(_lead(), _pain(), _email())
    assert any(
        f.type == "company_name_consistency" and f.severity == "error" for f in flags
    )


# ---------------------------------------------------------------------------
# end-to-end: blank subject → flagged
# ---------------------------------------------------------------------------


def test_check_email_blank_subject_is_flagged(monkeypatch):
    _mock_ollama(json.dumps({"checks": []}), monkeypatch)
    from trispoke.llm.qc_checker import check_email

    result = check_email(_lead(), _pain(), _email(subject=""))
    assert result.status == "flagged"
    assert any(f.type == "blank_subject" for f in result.flags)


def test_check_email_clean_passes_silently(monkeypatch):
    _mock_ollama(
        json.dumps({"checks": [
            {"name": "company_name_consistency", "passed": True},
            {"name": "person_name_consistency", "passed": True},
            {"name": "tone_appropriate", "passed": True},
            {"name": "ai_cliche_check", "passed": True},
            {"name": "factual_hallucination", "passed": True},
        ]}),
        monkeypatch,
    )
    from trispoke.llm.qc_checker import check_email

    result = check_email(_lead(), _pain(), _email())
    assert result.status == "passed"
    assert result.flags == []


def test_has_errors_helper():
    from trispoke.llm.qc_checker import QCFlag, has_errors

    assert has_errors([QCFlag("blank_subject", "error")]) is True
    assert has_errors([QCFlag("ai_cliche_check", "warning")]) is False
    assert has_errors([]) is False


def test_flag_serialization_round_trips():
    from trispoke.llm.qc_checker import QCFlag, flags_from_json, flags_to_json

    flags = [
        QCFlag("blank_subject", "error", None),
        QCFlag("ai_cliche_check", "warning", "contains 'leverage'"),
    ]
    s = flags_to_json(flags)
    parsed = flags_from_json(s)
    assert len(parsed) == 2
    assert parsed[0].type == "blank_subject"
    assert parsed[1].detail == "contains 'leverage'"
