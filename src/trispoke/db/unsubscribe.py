"""V1.5.2 — global unsubscribe filter.

A lead is treated as unsubscribed if ANY lead with the same email address
has ever transitioned to status='unsubscribed' (across all campaigns).
Used at every intake path to prevent re-emailing someone who already opted
out.

Lookups are case-insensitive on the email column so 'Foo@Bar.com' matches
'foo@bar.com'.
"""

from __future__ import annotations

from sqlalchemy import func

from trispoke.db.models import Lead, LeadStatus


def is_unsubscribed(session, email: str) -> bool:
    """True if any lead row with this email is in status='unsubscribed'."""
    if not email:
        return False
    target = email.strip().lower()
    if not target:
        return False
    row = (
        session.query(Lead.id)
        .filter(
            func.lower(Lead.email) == target,
            Lead.status == LeadStatus.unsubscribed.value,
        )
        .first()
    )
    return row is not None


def filter_unsubscribed(session, emails: list[str]) -> set[str]:
    """Return the lowercased subset of `emails` that are unsubscribed."""
    targets = {e.strip().lower() for e in emails if e and e.strip()}
    if not targets:
        return set()
    rows = (
        session.query(func.lower(Lead.email))
        .filter(
            Lead.status == LeadStatus.unsubscribed.value,
            func.lower(Lead.email).in_(targets),
        )
        .all()
    )
    return {r[0] for r in rows}
