"""LLM-based quality-control pass.

Two layers:

  1. **Deterministic structural checks** (no LLM). Fast, never crashes.
     Catch blank/oversized/template-leftover problems.
  2. **Semantic checks via local Ollama**. Catch tone, AI clichés,
     company-name drift, and potential hallucinations.

Performance: 1-3 seconds per email on qwen3:8b. For 300 leads that's
~5-15 minutes added to a generation run. Intentional — every flag the
QC catches saves human reviewer time at a much higher rate.

The qc module is designed to be ERR-ON-THE-SIDE-OF-FLAGGING. False
positives are cheap (one extra second of human attention); false
negatives cost a bad email to a real prospect.

CLI:
    uv run python -m trispoke.llm.qc_checker --campaign "Staffing Ontario Q2"
      [--only-failed] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape


_PROMPT_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
    keep_trailing_newline=True,
)


# ---------------------------------------------------------------------------
# data types
# ---------------------------------------------------------------------------


Severity = Literal["error", "warning"]


@dataclass
class QCFlag:
    type: str
    severity: Severity
    detail: Optional[str] = None


@dataclass
class QCResult:
    status: Literal["passed", "flagged"]
    flags: list[QCFlag] = field(default_factory=list)
    model_used: str = ""
    elapsed_seconds: float = 0.0


# Severity mapping per the V1.5 spec.
_ERROR_FLAGS = {
    "blank_subject",
    "blank_body",
    "placeholder_leftover",
    "company_name_consistency",  # when failed
    "person_name_consistency",   # when failed
}
_WARNING_FLAGS = {
    "subject_too_long",
    "body_too_long",
    "body_too_short",
    "salutation_mismatch",
    "email_in_body",
    "tone_appropriate",
    "ai_cliche_check",
    "factual_hallucination",
    "qc_llm_parse_failed",  # fallback when the LLM doesn't return JSON
}


# ---------------------------------------------------------------------------
# deterministic structural checks
# ---------------------------------------------------------------------------


_PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{?\s*[a-zA-Z_]\w*\s*\}?\}"),  # {first_name} / {{company}}
    re.compile(r"\[(INSERT|TODO|FIXME|FILL|XXX|TBD)\b[^\]]*\]", re.IGNORECASE),
    re.compile(r"\bTODO\b|\bFIXME\b|###"),
]

_AT_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _structural_checks(lead: Any, email: Any) -> list[QCFlag]:
    flags: list[QCFlag] = []
    subject = (email.subject or "").strip()
    body = (email.body or "").strip()

    if not subject:
        flags.append(QCFlag("blank_subject", "error"))
    if not body or len(body) < 30:
        flags.append(QCFlag("blank_body", "error"))

    if len(subject) > 80:
        flags.append(
            QCFlag(
                "subject_too_long",
                "warning",
                f"{len(subject)} chars (cap 80)",
            )
        )

    word_count = len(body.split())
    if word_count > 250:
        flags.append(
            QCFlag("body_too_long", "warning", f"{word_count} words (cap 250)")
        )
    elif word_count < 40 and body:
        flags.append(
            QCFlag("body_too_short", "warning", f"{word_count} words (min 40)")
        )

    haystack = f"{subject}\n{body}"
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(haystack):
            flags.append(
                QCFlag(
                    "placeholder_leftover",
                    "error",
                    f"matched pattern '{pat.pattern}'",
                )
            )
            break

    first_name = (getattr(lead, "first_name", "") or "").strip().lower()
    if first_name and first_name not in body.lower():
        flags.append(
            QCFlag(
                "salutation_mismatch",
                "warning",
                f"greeting doesn't mention '{getattr(lead, 'first_name', '')}'",
            )
        )

    lead_email = (getattr(lead, "email", "") or "").lower()
    for match in _AT_EMAIL.findall(body):
        if match.lower() != lead_email:
            flags.append(
                QCFlag(
                    "email_in_body",
                    "warning",
                    f"unexpected address '{match}' in body",
                )
            )
            break

    return flags


# ---------------------------------------------------------------------------
# LLM-based semantic checks
# ---------------------------------------------------------------------------


def _llm_checks(lead: Any, pain: Any, email: Any) -> tuple[list[QCFlag], str]:
    """Run the LLM check. Returns (flags, model_used).

    On any parsing failure we add a `qc_llm_parse_failed` warning rather
    than throwing — the structural checks have already produced useful
    output and the reviewer should still see the email.
    """
    from trispoke.llm.ollama_client import OllamaClient

    client = OllamaClient()
    template = _jinja.get_template("qc_check.j2")
    prompt = template.render(lead=lead, pain=pain, email=email)

    try:
        text, _tokens, _elapsed = client.generate(
            prompt, temperature=0.2, max_tokens=512
        )
    except Exception as e:
        return (
            [QCFlag("qc_llm_parse_failed", "warning", f"Ollama error: {e}")],
            client.default_model,
        )

    parsed = _extract_json_object(text)
    if parsed is None:
        return (
            [QCFlag("qc_llm_parse_failed", "warning",
                    f"non-JSON response: {text[:120]!r}")],
            client.default_model,
        )

    flags: list[QCFlag] = []
    for raw in parsed.get("checks", []) or []:
        name = raw.get("name")
        passed = raw.get("passed", True)
        if name and not passed:
            severity: Severity = "error" if name in _ERROR_FLAGS else "warning"
            flags.append(QCFlag(name, severity, raw.get("detail")))
    return flags, client.default_model


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> Optional[dict]:
    """Try direct json.loads; fall back to extracting the first {...} blob."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def check_email(lead: Any, pain: Any, email: Any) -> QCResult:
    """Run both layers. Returns a QCResult with merged flags."""
    start = time.time()
    flags = _structural_checks(lead, email)
    llm_flags, model_used = _llm_checks(lead, pain, email)
    flags.extend(llm_flags)

    status = "flagged" if flags else "passed"
    return QCResult(
        status=status,
        flags=flags,
        model_used=model_used,
        elapsed_seconds=time.time() - start,
    )


def flags_to_json(flags: list[QCFlag]) -> str:
    return json.dumps([asdict(f) for f in flags])


def flags_from_json(s: Optional[str]) -> list[QCFlag]:
    if not s:
        return []
    try:
        items = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []
    return [
        QCFlag(
            type=i.get("type", ""),
            severity=i.get("severity", "warning"),
            detail=i.get("detail"),
        )
        for i in items
        if isinstance(i, dict) and i.get("type")
    ]


def has_errors(flags: list[QCFlag]) -> bool:
    return any(f.severity == "error" for f in flags)


# ---------------------------------------------------------------------------
# CLI — re-run QC on existing drafts for prompt iteration
# ---------------------------------------------------------------------------


def _recheck_campaign(
    campaign_name: str, only_failed: bool, limit: Optional[int]
) -> None:
    from datetime import datetime
    from trispoke.db.event_log import log_event
    from trispoke.db.models import Campaign, Email, Lead, LeadStatus, PainAnalysis
    from trispoke.db.session import get_session
    from trispoke.pain_analyzer import PainTriple

    with get_session() as session:
        campaign = (
            session.query(Campaign).filter_by(name=campaign_name).first()
        )
        if not campaign:
            print(f"No campaign named '{campaign_name}'")
            sys.exit(1)

        q = (
            session.query(Email)
            .filter(Email.campaign_id == campaign.id)
        )
        if only_failed:
            q = q.filter(Email.qc_status == "flagged")
        if limit:
            q = q.limit(limit)
        emails = q.all()

        if not emails:
            print("No emails to recheck.")
            return

        for email in emails:
            lead = session.get(Lead, email.lead_id)
            pain_row = (
                session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
            )
            pain = (
                PainTriple(
                    chronic=pain_row.chronic,
                    acute=pain_row.acute,
                    trigger=pain_row.trigger,
                    confidence=pain_row.confidence or 0.0,
                )
                if pain_row
                else PainTriple("", "", "", 0.0)
            )
            result = check_email(lead, pain, email)
            email.qc_status = result.status
            email.qc_checked_at = datetime.utcnow()
            email.qc_flags_json = flags_to_json(result.flags)
            email.qc_model_used = result.model_used
            if result.status == "flagged":
                lead.status = LeadStatus.qc_flagged.value
            else:
                lead.status = LeadStatus.drafted.value
            session.commit()
            log_event(
                session,
                lead.id,
                "qc_flagged" if result.status == "flagged" else "qc_passed",
                {
                    "email_id": email.id,
                    "model_used": result.model_used,
                    "elapsed_seconds": result.elapsed_seconds,
                    "flags": json.loads(email.qc_flags_json or "[]"),
                },
            )
            print(
                f"{email.id:>5}  status={result.status:>7}  "
                f"flags={len(result.flags)}  "
                f"elapsed={result.elapsed_seconds:.2f}s"
            )


def main() -> None:
    p = argparse.ArgumentParser(description="Re-run QC on existing drafts")
    p.add_argument("--campaign", required=True, help="Campaign name")
    p.add_argument("--only-failed", action="store_true",
                   help="Only re-check emails whose qc_status is 'flagged'")
    p.add_argument("--limit", type=int, default=None, help="Cap rows checked")
    args = p.parse_args()
    _recheck_campaign(args.campaign, args.only_failed, args.limit)


if __name__ == "__main__":
    main()
