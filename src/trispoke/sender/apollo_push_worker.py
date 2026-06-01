"""V1.5.1 push worker — slot-pool driven Apollo sender.

The flow per approved email:

  1. Allocate a free slot from `apollo_slots` (round-robin by last_used_at).
  2. Upsert the lead into Apollo as a Contact (apollo_contact_id cached on
     the lead row).
  3. PUT the AI-generated subject + body into the slot's template. Apollo
     reads templates LIVE at send time, so the slot is marked `in_use`
     immediately to prevent concurrent overwrites.
  4. POST add_contact_ids — enrolls the lead into the slot's sequence,
     which materialises an emailer_message.
  5. Find the message_id via /emailer_messages/search.
  6. If email.send_mode == 'now', POST /send_now to bypass the schedule.
  7. Stamp the email + slot rows with the IDs and Apollo state.

If no free slots are available, the email stays in 'approved' status —
the next poll cycle (after the events poller frees some) will pick it up.

Per-lead Apollo failures are isolated; one bad lead never blocks a batch.
Rate-limited by APOLLO_MAX_ENROLLMENTS_PER_MINUTE (default 25).
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import or_

from trispoke.apollo.client import ApolloClient
from trispoke.app_config import config_float, config_int
from trispoke.config import get_settings
from trispoke.db.event_log import log_event
from trispoke.db.models import (
    ApolloSlot,
    Email,
    Lead,
    LeadStatus,
    Send,
    SendStatus,
)
from trispoke.db.session import get_session


BATCH_SIZE = 25
POLL_INTERVAL_SECONDS = 60


def _plain_to_html(plain: str) -> str:
    if not plain:
        return ""
    paras = [p.strip() for p in plain.split("\n\n") if p.strip()]
    return "".join(
        "<p>" + para.replace("\n", "<br>") + "</p>" for para in paras
    )


class NoFreeSlotError(RuntimeError):
    pass


class ApolloPushWorker:
    def __init__(self) -> None:
        self.running = True
        self.settings = get_settings()
        self.apollo = ApolloClient()
        signal.signal(signal.SIGINT, self._signal_handler)
        self._enroll_log: list[float] = []

    def _signal_handler(self, sig, frame):
        print("\nApollo push worker shutting down…")
        self.running = False
        sys.exit(0)

    # ---------- rate limiting ----------

    def _pace(self) -> None:
        cap = max(1, config_int(
            "APOLLO_MAX_ENROLLMENTS_PER_MINUTE",
            self.settings.apollo_max_enrollments_per_minute,
        ))
        now = time.monotonic()
        self._enroll_log = [t for t in self._enroll_log if now - t < 60.0]
        if len(self._enroll_log) >= cap:
            wait_for = 60.0 - (now - self._enroll_log[0])
            if wait_for > 0:
                time.sleep(wait_for)
        self._enroll_log.append(time.monotonic())

    # ---------- slot allocation ----------

    def _allocate_slot(self, session) -> Optional[ApolloSlot]:
        """Pick the longest-idle free slot. A slot is allocatable when:
          - status='free' (never used or recycled), OR
          - status='in_use' but last_message_status is terminal.
        Round-robin via ORDER BY last_used_at ASC NULLS FIRST.
        """
        slot = (
            session.query(ApolloSlot)
            .filter(
                or_(
                    ApolloSlot.status == "free",
                    (ApolloSlot.status == "in_use")
                    & (
                        ApolloSlot.last_message_status.in_(
                            ["completed", "failed", "bounced", "spam_blocked",
                             "hard_bounced", "finished"]
                        )
                    ),
                )
            )
            .order_by(ApolloSlot.last_used_at.asc().nulls_first(), ApolloSlot.id.asc())
            .first()
        )
        if slot is None:
            return None
        # Mark in_use immediately so a concurrent cycle (or our own next
        # iteration) doesn't double-allocate the same slot.
        slot.status = "in_use"
        slot.last_used_at = datetime.utcnow()
        slot.last_message_id = None
        slot.last_message_status = "allocating"
        session.commit()
        return slot

    # ---------- bounce-rate guard ----------

    def _check_bounce_rate(self, session) -> bool:
        """Return False (push paused) if recent bounce rate exceeds threshold."""
        threshold = config_float(
            "BOUNCE_PAUSE_THRESHOLD", self.settings.bounce_pause_threshold
        )
        min_sends = config_int(
            "BOUNCE_PAUSE_MIN_SENDS", self.settings.bounce_pause_min_sends
        )
        if threshold <= 0:
            return True
        recent = (
            session.query(Send.status)
            .order_by(Send.created_at.desc())
            .limit(100)
            .all()
        )
        if len(recent) < min_sends:
            return True
        bounces = sum(1 for (s,) in recent if s == SendStatus.bounced.value)
        rate = bounces / len(recent) if recent else 0.0
        if rate > threshold:
            print(
                f"[push] PAUSED — bounce rate {rate:.1%} (>{threshold:.1%}) "
                f"over last {len(recent)} sends. Will retry next cycle."
            )
            return False
        return True

    # ---------- main loop ----------

    def run_once(self) -> int:
        pushed = 0
        with get_session() as session:
            if not self._check_bounce_rate(session):
                return 0

            approved = (
                session.query(Email)
                .join(Lead, Email.lead_id == Lead.id)
                .filter(
                    Lead.status == LeadStatus.approved.value,
                    Email.apollo_pushed_at.is_(None),
                )
                .limit(BATCH_SIZE)
                .all()
            )
            if not approved:
                return 0

            for email in approved:
                if not self.running:
                    return pushed
                lead = session.get(Lead, email.lead_id)
                if lead is None:
                    continue

                slot = self._allocate_slot(session)
                if slot is None:
                    # Backpressure — leave email in approved status, retry next cycle.
                    print(
                        f"[push] no free slot for lead {lead.id}; will retry "
                        f"after events poller frees a slot"
                    )
                    return pushed

                try:
                    self._pace()
                    self._push_one(session, slot, lead, email)
                    pushed += 1
                except Exception as e:
                    # Roll back the slot allocation so it can be retried.
                    print(f"[push] failed lead {lead.id} ({lead.email}): {e}")
                    slot.status = "free"
                    slot.last_message_status = None
                    slot.last_message_id = None
                    session.commit()

        return pushed

    def run(self) -> None:
        print("Apollo push worker (slot-pool) started. Ctrl+C to stop.")
        while self.running:
            try:
                pushed = self.run_once()
                if pushed:
                    print(f"[push] pushed {pushed} email(s) into Apollo")
                time.sleep(POLL_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[push] loop error: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)

    # ---------- per-email push ----------

    def _push_one(
        self, session, slot: ApolloSlot, lead: Lead, email: Email
    ) -> None:
        # 1. Upsert contact
        if not lead.apollo_contact_id:
            contact = self.apollo.create_or_update_contact(lead)
            lead.apollo_contact_id = contact["id"]
            session.commit()
            log_event(session, lead.id, "apollo_contact_created", {
                "contact_id": lead.apollo_contact_id, "email": lead.email,
            })

        # 2. PUT the AI subject + body into the slot's initial template
        self.apollo.update_template(
            slot.template_id,
            subject=email.subject or "",
            body_html=_plain_to_html(email.body or ""),
            body_text=email.body or "",
        )

        # 2b. If the slot has a follow-up step, ensure it's personalised too.
        # Generic "floating this up" content using the lead's first name —
        # we replace Apollo merge variables manually since this is a one-shot
        # per-lead PUT rather than a bulk template fill.
        if slot.followup_template_id:
            first = (lead.first_name or "there").strip().split()[0]
            fu_subject = f"re: {email.subject or 'quick follow-up'}"
            fu_text = (
                f"Hi {first},\n\nFloating this up — was the previous note "
                "relevant to anything you're looking at right now? Happy to "
                "take it off your plate either way.\n\nBest,"
            )
            fu_html = _plain_to_html(fu_text)
            self.apollo.update_template(
                slot.followup_template_id,
                subject=fu_subject,
                body_html=fu_html,
                body_text=fu_text,
            )

        # 3. Enroll into the slot's sequence
        self.apollo.enroll_contact_in_sequence(
            sequence_id=slot.sequence_id,
            contact_id=lead.apollo_contact_id,
            mailbox_id=slot.mailbox_id,
        )

        # 4. Find the just-materialized emailer_message
        message_id = self.apollo.find_message_for_enrollment(
            slot.sequence_id, lead.apollo_contact_id
        )

        # 5. Stamp slot + email rows
        slot.last_message_id = message_id
        slot.last_message_status = "scheduled"
        email.apollo_sequence_id = slot.sequence_id
        email.apollo_step_id = slot.step_id
        email.apollo_enrollment_id = message_id
        email.apollo_message_id = message_id
        email.apollo_pushed_at = datetime.utcnow()
        lead.status = LeadStatus.queued_in_apollo.value
        session.commit()

        log_event(session, lead.id, "apollo_contact_enrolled", {
            "slot_name": slot.slot_name,
            "sequence_id": slot.sequence_id,
            "message_id": message_id,
            "email_id": email.id,
            "send_mode": email.send_mode,
        })

        # 6. send_now if requested — bypasses Apollo's slot schedule
        if email.send_mode == "now" and message_id:
            try:
                self.apollo.send_message_now(message_id)
                # Mark optimistically; events poller will confirm with the
                # real completed_at + provider_message_id.
                slot.last_message_status = "completed"
                slot.last_freed_at = datetime.utcnow()
                session.commit()
                log_event(session, lead.id, "apollo_message_sent", {
                    "apollo_message_id": message_id,
                    "slot_name": slot.slot_name,
                    "via": "send_now",
                })
            except Exception as e:
                print(f"[push] send_now failed for message {message_id}: {e}")
                # Leave slot status='in_use' / last_message_status='scheduled'
                # so the events poller can sort it out.


def main() -> None:
    ApolloPushWorker().run()


if __name__ == "__main__":
    main()
