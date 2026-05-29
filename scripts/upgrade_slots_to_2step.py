#!/usr/bin/env python
"""Retro-upgrade existing 1-step Apollo slots to 2-step (add the 4-day
follow-up).

Idempotent: skips slots where followup_step_id is already set. Apollo
accepts new steps on active sequences, so no need to deactivate.

Usage:
    uv run python scripts/upgrade_slots_to_2step.py [--followup-wait-days 4]
                                                    [--pace-seconds 0.5]
"""

from __future__ import annotations

import argparse
import sys
import time

from trispoke.apollo.client import ApolloClient
from trispoke.db.models import ApolloSlot
from trispoke.db.session import get_session


FOLLOW_UP_SUBJECT = "re: floating this up"
FOLLOW_UP_BODY_HTML = (
    "<p>Hi {{first_name}},</p>"
    "<p>Floating this up — was the previous note relevant to anything you're "
    "looking at right now? Happy to take it off your plate either way.</p>"
    "<p>Best,</p>"
)
FOLLOW_UP_BODY_TEXT = (
    "Hi {{first_name}},\n\n"
    "Floating this up — was the previous note relevant to anything you're "
    "looking at right now? Happy to take it off your plate either way.\n\nBest,"
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--followup-wait-days", type=int, default=4)
    p.add_argument("--pace-seconds", type=float, default=0.5)
    args = p.parse_args()

    apollo = ApolloClient()

    with get_session() as session:
        slots_to_upgrade = (
            session.query(ApolloSlot)
            .filter(ApolloSlot.followup_step_id.is_(None))
            .order_by(ApolloSlot.id.asc())
            .all()
        )
        targets = [(s.id, s.slot_name, s.sequence_id) for s in slots_to_upgrade]

    if not targets:
        print("No 1-step slots to upgrade (all already have follow-up).")
        return 0

    print(f"Upgrading {len(targets)} slot(s)…\n")
    upgraded, failed = 0, 0
    for slot_id, slot_name, sequence_id in targets:
        try:
            out = apollo.add_followup_step(
                sequence_id,
                template={
                    "subject": FOLLOW_UP_SUBJECT,
                    "body_html": FOLLOW_UP_BODY_HTML,
                    "body_text": FOLLOW_UP_BODY_TEXT,
                    "wait_days_after": args.followup_wait_days,
                },
            )
        except Exception as e:
            failed += 1
            print(f"  [{slot_name}] FAILED: {e}")
            time.sleep(args.pace_seconds)
            continue

        with get_session() as session:
            slot = session.get(ApolloSlot, slot_id)
            slot.followup_step_id = out["step_id"]
            slot.followup_touch_id = out["touch_id"]
            slot.followup_template_id = out["template_id"]
            session.commit()

        upgraded += 1
        print(
            f"  [{slot_name}] OK fu_step={out['step_id']} "
            f"fu_template={out['template_id']}"
        )
        time.sleep(args.pace_seconds)

    print()
    print(f"Done. upgraded={upgraded}  failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
