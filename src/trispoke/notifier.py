"""Outbound webhook for positive replies.

Configured via REPLY_WEBHOOK_URL in .env (works with Slack incoming webhooks,
Zapier catch hooks, generic JSON receivers). Silently no-ops when unset.

The hook fires only on `classification='positive'` to avoid noisy alerts on
auto-replies / unsubscribes / OOO.
"""

from __future__ import annotations

from typing import Any

import httpx


def notify_positive_reply(
    webhook_url: str | None,
    *,
    campaign: str,
    lead_email: str,
    lead_name: str,
    reply_subject: str,
    reply_excerpt: str,
    apollo_message_id: str | None = None,
) -> bool:
    """POST a JSON payload + Slack-compatible text. Returns True on 2xx."""
    if not webhook_url:
        return False
    text = (
        f":envelope_with_arrow: *Positive reply* from *{lead_name or lead_email}* "
        f"({lead_email}) — campaign `{campaign}`\n"
        f"> *{reply_subject}*\n"
        f"> {reply_excerpt[:400]}"
    )
    payload: dict[str, Any] = {
        "text": text,
        "event": "positive_reply",
        "campaign": campaign,
        "lead_email": lead_email,
        "lead_name": lead_name,
        "reply_subject": reply_subject,
        "reply_excerpt": reply_excerpt[:1000],
        "apollo_message_id": apollo_message_id,
    }
    try:
        r = httpx.post(webhook_url, json=payload, timeout=5.0)
        return 200 <= r.status_code < 300
    except Exception as e:
        print(f"[notifier] webhook post failed: {e}")
        return False
