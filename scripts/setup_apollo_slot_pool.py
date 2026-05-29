#!/usr/bin/env python
"""One-time setup: create N pre-wired Apollo sequences as a slot pool.

Each slot is a fully-configured sequence (template populated, touch
approved, schedule attached, mailbox attached) that the push worker can
overwrite per-send.

After this script finishes, you MUST manually activate each slot in
Apollo's UI ONCE (Sequences → bulk select → Activate). After that the
pool is permanent — trispoke never touches activation again.

Idempotent: re-running picks up where it left off if you killed it
mid-run. Existing slots in the DB are skipped.

Usage:
    uv run python scripts/setup_apollo_slot_pool.py \\
        --count 100 \\
        [--mailbox-id MB_ID] \\
        [--schedule-id SCHED_ID] \\
        [--start-from 1] \\
        [--pace-seconds 0.5]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Optional

from trispoke.apollo.client import ApolloClient
from trispoke.db.models import ApolloSlot
from trispoke.db.session import get_session


PLACEHOLDER_SUBJECT = "trispoke slot (will be overwritten per send)"
PLACEHOLDER_BODY_HTML = (
    "<p>This template is overwritten by trispoke before each send. "
    "If you're reading this, something went wrong.</p>"
)
PLACEHOLDER_BODY_TEXT = (
    "This template is overwritten by trispoke before each send. "
    "If you're reading this, something went wrong."
)

# The follow-up step uses the SAME template content across all sends because
# it's a short, generic "floating this up" bump — no per-lead personalization
# needed. Each slot still gets its own copy for race-free template overwrites.
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
    p.add_argument("--count", type=int, default=5,
                   help="how many slots to ensure exist (default 5 for safety; "
                        "production = 100)")
    p.add_argument("--mailbox-id", type=str, default=None,
                   help="Apollo mailbox id (default: first active mailbox)")
    p.add_argument("--schedule-id", type=str, default=None,
                   help="Apollo emailer_schedule id (default: workspace default)")
    p.add_argument("--start-from", type=int, default=1,
                   help="slot number to start from (1-indexed)")
    p.add_argument("--pace-seconds", type=float, default=0.5,
                   help="sleep between Apollo calls to respect rate limits")
    p.add_argument("--prefix", type=str, default="trispoke-slot",
                   help="slot name prefix (default 'trispoke-slot')")
    p.add_argument("--no-followup", action="store_true",
                   help="skip the 2nd (follow-up) step in each slot")
    p.add_argument("--followup-wait-days", type=int, default=4,
                   help="days to wait before the follow-up step fires (default 4)")
    args = p.parse_args()

    apollo = ApolloClient()

    # Resolve mailbox
    mailbox_id = args.mailbox_id
    if mailbox_id is None:
        boxes = apollo.list_mailboxes()
        active = [b for b in boxes if b.get("active") is not False]
        chosen = active[0] if active else (boxes[0] if boxes else None)
        if chosen is None:
            print("ERROR: no mailboxes connected in Apollo.", file=sys.stderr)
            return 1
        mailbox_id = chosen["id"]
        print(f"using mailbox: {chosen.get('email')} (id={mailbox_id})")

    # Resolve schedule
    schedule_id = args.schedule_id
    if schedule_id is None:
        scheds = apollo.list_emailer_schedules()
        default = next((s for s in scheds if s.get("default")), None) or (
            scheds[0] if scheds else None
        )
        if default is None:
            print("ERROR: no emailer_schedules in workspace.", file=sys.stderr)
            return 1
        schedule_id = default["id"]
        print(f"using schedule: {default.get('name')} (id={schedule_id})")

    end = args.start_from + args.count - 1
    print(f"Creating slots {args.start_from:03d}..{end:03d} (count={args.count})")
    print(f"Pacing: {args.pace_seconds}s between Apollo calls\n")

    created = 0
    skipped = 0
    failed = 0

    for n in range(args.start_from, args.start_from + args.count):
        slot_name = f"{args.prefix}-{n:03d}"

        with get_session() as session:
            existing = (
                session.query(ApolloSlot).filter_by(slot_name=slot_name).first()
            )
            if existing:
                skipped += 1
                print(f"  [{slot_name}] already in DB (seq={existing.sequence_id}) — skipped")
                continue

        try:
            follow_up = None
            if not args.no_followup:
                follow_up = {
                    "subject": FOLLOW_UP_SUBJECT,
                    "body_html": FOLLOW_UP_BODY_HTML,
                    "body_text": FOLLOW_UP_BODY_TEXT,
                    "wait_days_after": args.followup_wait_days,
                }
            out = apollo.create_sequence(
                name=slot_name,
                step_template={
                    "subject": PLACEHOLDER_SUBJECT,
                    "body_html": PLACEHOLDER_BODY_HTML,
                    "body_text": PLACEHOLDER_BODY_TEXT,
                    "wait_days_after": 0,
                    "mailbox_id": mailbox_id,
                    "schedule_id": schedule_id,
                },
                follow_up_template=follow_up,
            )
        except Exception as e:
            failed += 1
            print(f"  [{slot_name}] FAILED to create in Apollo: {e}")
            time.sleep(args.pace_seconds)
            continue

        with get_session() as session:
            slot = ApolloSlot(
                slot_name=slot_name,
                sequence_id=out["sequence_id"],
                step_id=out["step_id"],
                touch_id=out["touch_id"],
                template_id=out["template_id"],
                followup_step_id=out.get("followup_step_id"),
                followup_touch_id=out.get("followup_touch_id"),
                followup_template_id=out.get("followup_template_id"),
                mailbox_id=mailbox_id,
                schedule_id=schedule_id,
                status="free",
            )
            session.add(slot)
            session.commit()

        created += 1
        fu_note = " +followup" if out.get("followup_step_id") else ""
        print(
            f"  [{slot_name}] OK seq={out['sequence_id']} "
            f"step={out['step_id']} template={out['template_id']}{fu_note}"
        )
        time.sleep(args.pace_seconds)

    print()
    print("=" * 60)
    print(f"Done. created={created}  skipped={skipped}  failed={failed}")
    print()
    print("NEXT STEP (manual, one-time):")
    print("  Open Apollo dashboard -> Sequences -> filter by prefix")
    print(f"  '{args.prefix}-' -> select all -> Activate.")
    print()
    print("After activation the pool is permanent. trispoke's push worker")
    print("will rotate templates through it automatically from now on.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
