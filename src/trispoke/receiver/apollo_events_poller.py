"""V1.5.1 events poller — slot-pool aware.

For each slot in `apollo_slots` that currently has a message in flight,
pull the latest message state from Apollo via /emailer_messages/search,
project it into V1's events table, and **free the slot when the message
reaches a terminal state**. Slot turnover is what unblocks the push
worker for new sends.

Endpoint: GET /emailer_messages/search (the plain /emailer_messages
404s on this Apollo plan).

Idempotency: events are keyed on (lead_id, event_type, apollo_message_id);
running the poll twice with the same Apollo data inserts no duplicates.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from trispoke.apollo.client import ApolloClient
from trispoke.app_config import config_int
from trispoke.config import get_settings
from trispoke.db.event_log import log_event
from trispoke.db.models import (
    ApolloSlot,
    Email,
    Event,
    Lead,
    LeadStatus,
    Reply,
    Send,
    SendStatus,
)
from trispoke.db.session import get_session
from trispoke.notifier import notify_positive_reply
from trispoke.receiver.classifier import classify


_TERMINAL_STATUSES = {
    "completed", "failed", "bounced", "hard_bounced",
    "spam_blocked", "finished", "not_sent",
}


class ApolloEventsPoller:
    def __init__(self) -> None:
        self.running = True
        self.settings = get_settings()
        self.apollo = ApolloClient()
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        print("\nApollo events poller shutting down…")
        self.running = False
        sys.exit(0)

    # ---------- main loop ----------

    def run_once(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with get_session() as session:
            # Poll every slot whose current message isn't yet terminal,
            # OR which has no current message (might catch lingering state).
            slots = (
                session.query(ApolloSlot)
                .filter(
                    ApolloSlot.last_message_id.isnot(None),
                    (ApolloSlot.last_message_status.is_(None))
                    | (~ApolloSlot.last_message_status.in_(_TERMINAL_STATUSES)),
                )
                .all()
            )
            for slot in slots:
                try:
                    body = self.apollo._search_messages(slot.sequence_id)
                except Exception as e:
                    print(f"[apollo-poll] {slot.slot_name}: {e}")
                    continue

                msgs = body.get("emailer_messages", [])
                target = next(
                    (m for m in msgs if m.get("id") == slot.last_message_id), None
                )
                if target is None:
                    continue

                applied = self._apply_event(session, slot, target)
                if applied:
                    counts[applied] = counts.get(applied, 0) + 1
                # Mirror Apollo's status onto the slot regardless.
                new_status = target.get("status")
                if new_status and new_status != slot.last_message_status:
                    slot.last_message_status = new_status
                    if new_status in _TERMINAL_STATUSES:
                        slot.last_freed_at = datetime.utcnow()
                    session.commit()

        return counts

    def run(self) -> None:
        print("Apollo events poller (slot-pool) started. Ctrl+C to stop.")
        while self.running:
            interval = config_int(
                "APOLLO_POLL_INTERVAL_SECONDS",
                self.settings.apollo_poll_interval_seconds,
            )
            try:
                counts = self.run_once()
                if counts:
                    print(f"[apollo-poll] processed {counts}")
                time.sleep(interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[apollo-poll] loop error: {e}")
                time.sleep(interval)

    # ---------- per-event dispatch ----------

    def _apply_event(
        self, session, slot: ApolloSlot, ev: dict[str, Any]
    ) -> Optional[str]:
        message_id = ev.get("id")
        if not message_id:
            return None

        # Slot-pool design: email row carries apollo_message_id, so direct lookup.
        email = (
            session.query(Email)
            .filter(Email.apollo_message_id == message_id)
            .first()
        )
        if email is None:
            # Fallback: contact-id chain (handles V1.5 legacy rows whose
            # apollo_message_id wasn't stamped at enrollment time).
            contact_id = ev.get("contact_id")
            if contact_id:
                lead_row = (
                    session.query(Lead)
                    .filter(Lead.apollo_contact_id == contact_id)
                    .order_by(Lead.id.desc())
                    .first()
                )
                if lead_row:
                    email = (
                        session.query(Email)
                        .filter(Email.lead_id == lead_row.id)
                        .order_by(Email.created_at.desc())
                        .first()
                    )
        if email is None:
            return None
        lead = session.get(Lead, email.lead_id)
        if lead is None:
            return None

        events_applied: list[str] = []

        if ev.get("bounced_at"):
            if self._apply_once(session, lead.id, "apollo_message_bounced", message_id):
                lead.status = LeadStatus.bounced.value
                session.commit()
                log_event(session, lead.id, "apollo_message_bounced", {
                    "apollo_message_id": message_id, "slot_name": slot.slot_name,
                })
                events_applied.append("bounced")

        if ev.get("unsubscribed_at"):
            if self._apply_once(session, lead.id, "apollo_message_unsubscribed", message_id):
                lead.status = LeadStatus.unsubscribed.value
                session.commit()
                log_event(session, lead.id, "apollo_message_unsubscribed", {
                    "apollo_message_id": message_id, "slot_name": slot.slot_name,
                })
                events_applied.append("unsubscribed")

        if ev.get("replied_at"):
            if self._apply_once(session, lead.id, "apollo_message_replied", message_id):
                body_text = ""
                subject = ev.get("subject") or email.subject or ""
                try:
                    detail = self.apollo.get_message_body(message_id)
                    body_text = detail.get("body_text") or detail.get("body_html") or ""
                    subject = detail.get("subject") or subject
                except Exception:
                    pass
                classification = classify(subject, body_text)
                reply = Reply(
                    lead_id=lead.id,
                    email_id=email.id,
                    received_at=_parse_iso(ev["replied_at"]) or datetime.utcnow(),
                    from_address=lead.email,
                    subject=subject,
                    body=body_text,
                    classification=classification,
                )
                session.add(reply)
                lead.status = LeadStatus.replied.value
                session.commit()
                log_event(session, lead.id, "apollo_message_replied", {
                    "apollo_message_id": message_id,
                    "slot_name": slot.slot_name,
                    "classification": classification,
                })
                # Fire the webhook for positive replies (Slack / Zapier / etc).
                if classification == "positive":
                    from trispoke.db.models import Campaign as _C
                    from trispoke.secrets_store import secret as _secret
                    campaign = session.get(_C, email.campaign_id)
                    notify_positive_reply(
                        _secret("REPLY_WEBHOOK_URL", self.settings.reply_webhook_url),
                        campaign=campaign.name if campaign else "(unknown)",
                        lead_email=lead.email,
                        lead_name=(
                            f"{lead.first_name or ''} {lead.last_name or ''}"
                        ).strip(),
                        reply_subject=subject,
                        reply_excerpt=body_text,
                        apollo_message_id=message_id,
                    )
                events_applied.append("replied")

        if ev.get("opened_at"):
            if self._apply_once(session, lead.id, "apollo_message_opened", message_id):
                log_event(session, lead.id, "apollo_message_opened", {
                    "apollo_message_id": message_id,
                    "slot_name": slot.slot_name,
                    "opened_at": ev["opened_at"],
                })
                events_applied.append("opened")

        # 'completed' (Apollo's status when the message actually went out via SMTP)
        is_sent = ev.get("status") == "completed" or ev.get("completed_at") or ev.get("sent_at")
        if is_sent:
            if self._apply_once(session, lead.id, "apollo_message_sent", message_id):
                mailbox_id = (
                    ev.get("email_account_id")
                    or ev.get("send_email_from_email_account_id")
                    or slot.mailbox_id
                )
                snd = Send(
                    email_id=email.id,
                    sent_from_inbox=ev.get("from_email") or "",
                    sent_at=_parse_iso(
                        ev.get("completed_at") or ev.get("sent_at") or ev.get("due_at")
                    ) or datetime.utcnow(),
                    smtp_message_id=ev.get("provider_message_id"),
                    status=SendStatus.sent.value,
                    sent_via="apollo",
                    apollo_mailbox_id=mailbox_id,
                )
                session.add(snd)
                if lead.status in (
                    LeadStatus.queued_in_apollo.value,
                    LeadStatus.approved.value,
                ):
                    lead.status = LeadStatus.sent.value
                session.commit()
                log_event(session, lead.id, "apollo_message_sent", {
                    "apollo_message_id": message_id,
                    "slot_name": slot.slot_name,
                    "mailbox_id": mailbox_id,
                    "provider_message_id": ev.get("provider_message_id"),
                })
                events_applied.append("sent")

        return events_applied[-1] if events_applied else None

    def _apply_once(
        self, session, lead_id: int, event_type: str, apollo_message_id: str
    ) -> bool:
        existing = (
            session.query(Event)
            .filter(
                Event.lead_id == lead_id,
                Event.event_type == event_type,
                Event.payload_json.like(f'%"apollo_message_id": "{apollo_message_id}"%'),
            )
            .first()
        )
        return existing is None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        s = value.replace("Z", "+00:00") if isinstance(value, str) else value
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


def main() -> None:
    ApolloEventsPoller().run()


if __name__ == "__main__":
    main()
