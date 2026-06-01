"""Per-inbox daily sending cap, with a warm-up ramp.

Both the steady-state cap and the ramp schedule are configured from the
Settings page (stored in app_config); the values here are just fallbacks used
before anything has been saved. The effective cap for a campaign on a given day
is min(ramp cap for the current week, hard cap) so the ramp climbs toward — but
never exceeds — the configured ceiling.
"""

from datetime import datetime

from trispoke.app_config import config_int, config_json
from trispoke.config import get_settings
from trispoke.db.models import Campaign
from trispoke.db.session import get_session

# Fallback ramp: week 1 → 10/day, week 2 → 15/day, week 3+ → 20/day.
DEFAULT_RAMP_SCHEDULE = [
    {"week": 1, "cap": 10},
    {"week": 2, "cap": 15},
    {"week": 3, "cap": 20},
]


def get_ramp_schedule() -> list[dict]:
    """Return the configured ramp schedule (sorted by week), or the default."""
    sched = config_json("RAMP_SCHEDULE", None)
    rows = []
    if isinstance(sched, list):
        for r in sched:
            try:
                rows.append({"week": int(r["week"]), "cap": int(r["cap"])})
            except (KeyError, TypeError, ValueError):
                continue
    if not rows:
        rows = [dict(r) for r in DEFAULT_RAMP_SCHEDULE]
    return sorted(rows, key=lambda r: r["week"])


def get_daily_cap() -> int:
    """Configured steady-state (post-ramp) cap per inbox per day."""
    fallback = get_settings().daily_cap_per_inbox or 20
    return config_int("DAILY_CAP_PER_INBOX", fallback)


def _ramp_cap_for_week(week: int) -> int:
    """Cap for the given 1-based week: the last schedule row whose week is <=
    the current week (so the final row acts as 'week N+')."""
    schedule = get_ramp_schedule()
    cap = schedule[0]["cap"]
    for row in schedule:
        if week >= row["week"]:
            cap = row["cap"]
    return cap


def current_daily_cap(campaign_name: str) -> int:
    """Effective per-inbox daily cap for a campaign right now."""
    hard_cap = get_daily_cap()

    with get_session() as session:
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()

    if not campaign or not campaign.started_at:
        week = 1
    else:
        days_elapsed = (datetime.utcnow() - campaign.started_at).days
        week = days_elapsed // 7 + 1

    return min(_ramp_cap_for_week(week), hard_cap)
