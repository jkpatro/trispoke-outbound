"""V1.5 follow-up scheduler for Apollo-mode campaigns.

For campaigns sending via Apollo, follow-ups are implemented by appending a
second step (with `wait_days_after=4`) to the existing Apollo sequence —
Apollo's runtime handles the wait + send. We do NOT generate a separate V1
draft and push it; that would race with Apollo's own scheduling.

The SMTP-direct path keeps its existing `scheduler/followup.py` logic, which
generates a follow-up draft and queues it for the SMTP sender loop.
"""

from __future__ import annotations

import json
from typing import Any

from trispoke.apollo.client import ApolloClient
from trispoke.db.event_log import log_event
from trispoke.db.models import Campaign, Lead
from trispoke.db.session import get_session


_FOLLOWUP_SUBJECT = "re: {{personalization_subject}}"
_FOLLOWUP_BODY_HTML = (
    "<p>Hi {{first_name}},</p>"
    "<p>Floating this up — was the previous note relevant to anything you're "
    "looking at right now? Happy to take it off your plate either way.</p>"
    "<p>Best,<br>{{sender_first_name}}</p>"
)


def schedule_apollo_followups() -> int:
    """For each Apollo-mode campaign with an active sequence, ensure a
    `wait 4 days` follow-up step exists. Returns count of steps appended."""
    appended = 0
    apollo = ApolloClient()

    with get_session() as session:
        campaigns = (
            session.query(Campaign)
            .filter(Campaign.mode == "apollo")
            .all()
        )
        for campaign in campaigns:
            settings_payload: dict[str, Any] = {}
            if campaign.settings_json:
                try:
                    settings_payload = json.loads(campaign.settings_json) or {}
                except (TypeError, ValueError):
                    continue

            seq_id = settings_payload.get("apollo_sequence_id")
            if not seq_id or settings_payload.get("apollo_followup_step_id"):
                continue

            mailbox_id = settings_payload.get("apollo_mailbox_id")
            try:
                # Use the same low-level POST we use for the initial step.
                response = apollo._retry_request(  # type: ignore[attr-defined]
                    "POST",
                    f"{apollo.base_url}/emailer_steps",
                    json={
                        "emailer_campaign_id": seq_id,
                        "position": 2,
                        "type": "auto_email",
                        "wait_time": 4,
                        "wait_mode": "day",
                        "subject": _FOLLOWUP_SUBJECT,
                        "body_html": _FOLLOWUP_BODY_HTML,
                        "mailbox_id": mailbox_id,
                    },
                )
                step = response.json().get("emailer_step") or response.json()
                followup_step_id = step.get("id")
            except Exception as e:
                print(f"[apollo-followup] {campaign.name}: {e}")
                continue

            if not followup_step_id:
                continue

            settings_payload["apollo_followup_step_id"] = followup_step_id
            campaign.settings_json = json.dumps(settings_payload)
            session.commit()

            first_lead = (
                session.query(Lead)
                .filter_by(campaign_id=campaign.id)
                .order_by(Lead.id.asc())
                .first()
            )
            if first_lead:
                log_event(
                    session,
                    first_lead.id,
                    "apollo_sequence_created",
                    {
                        "campaign": campaign.name,
                        "sequence_id": seq_id,
                        "followup_step_id": followup_step_id,
                        "wait_days": 4,
                    },
                )
            appended += 1

    return appended


def main() -> None:
    print("Appending Apollo follow-up steps…")
    n = schedule_apollo_followups()
    print(f"Appended follow-up step to {n} sequence(s).")


if __name__ == "__main__":
    main()
