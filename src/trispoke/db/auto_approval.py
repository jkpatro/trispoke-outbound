"""Auto-approval policy: decides whether a QC-passed draft should be
auto-approved (skipping human review).

Policy lives on the Campaign row:
  - never:        always require human review (legacy / safe default)
  - after_warmup: auto-approve once `warmup_threshold` sends have completed
                  successfully (Send rows with status='sent' for this campaign)
  - always:       auto-approve every QC-passed lead immediately

When auto-approval applies, the draft transitions:
    qc_pending -> approved (skipping 'drafted' / human queue)

The send_mode for auto-approved drafts defaults to 'scheduled' so Apollo's
own pacing protects deliverability. (Reviewers can still pick 'now' for
manually-approved drafts via the UI.)
"""

from __future__ import annotations

from trispoke.db.models import Campaign, Email, Lead, Send, SendStatus


def should_auto_approve(session, campaign: Campaign) -> bool:
    """Return True if QC-passed leads in this campaign should bypass human
    review."""
    mode = (campaign.auto_approve_mode or "after_warmup").strip().lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    # after_warmup
    threshold = int(campaign.warmup_threshold or 20)
    if threshold <= 0:
        return True
    completed_sends = (
        session.query(Send)
        .join(Email, Email.id == Send.email_id)
        .filter(
            Email.campaign_id == campaign.id,
            Send.status == SendStatus.sent.value,
        )
        .count()
    )
    return completed_sends >= threshold
