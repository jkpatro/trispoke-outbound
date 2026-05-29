import argparse
import json
import sys
from datetime import datetime

from sqlalchemy.orm import Session

from trispoke.config import get_settings
from trispoke.db.event_log import log_event
from trispoke.db.models import Campaign, Email, Lead, LeadStatus
from trispoke.db.session import get_session
from trispoke.llm.qc_checker import check_email as qc_check
from trispoke.llm.qc_checker import flags_to_json
from trispoke.llm.router import LLMRouter
from trispoke.pain_analyzer import analyze

settings = get_settings()

def generate_drafts(campaign_name: str, mode: str):
    """Generate email drafts for a campaign"""
    router = LLMRouter()

    with get_session() as session:
        # Get campaign
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            print(f"Error: Campaign '{campaign_name}' not found")
            sys.exit(1)

        # Get enriched leads
        leads = session.query(Lead).filter_by(
            campaign_id=campaign.id,
            status=LeadStatus.enriched.value
        ).all()

        if not leads:
            print(f"No enriched leads found in campaign '{campaign_name}'")
            return

        generated_count = 0

        for lead in leads:
            # Analyze pain
            pain = analyze(lead)

            # Persist pain analysis
            from trispoke.db.models import PainAnalysis
            pain_record = PainAnalysis(
                lead_id=lead.id,
                chronic=pain.chronic,
                acute=pain.acute,
                trigger=pain.trigger,
                confidence=pain.confidence
            )
            session.add(pain_record)

            # Generate email
            draft = router.generate_email(lead, pain, mode)

            # Create email record
            email = Email(
                lead_id=lead.id,
                campaign_id=campaign.id,
                subject=draft.subject,
                body=draft.body,
                model_used=draft.model_used,
                tokens_used=draft.tokens_used,
                generation_seconds=draft.generation_seconds
            )
            session.add(email)
            session.commit()  # Commit to get email.id

            # Log generation first (decoupled from QC outcome)
            log_event(session, lead.id, "pain_analyzed", {
                "pain": {
                    "chronic": pain.chronic,
                    "acute": pain.acute,
                    "trigger": pain.trigger,
                    "confidence": pain.confidence
                },
                "campaign": campaign_name
            })
            log_event(session, lead.id, "email_generated", {
                "email_id": email.id,
                "model_used": draft.model_used,
                "tokens_used": draft.tokens_used,
                "generation_seconds": draft.generation_seconds,
                "campaign": campaign_name
            })

            # V1.5: QC pass. Flagged emails block approval downstream until
            # resolved or overridden. Errors during QC itself fall back to
            # 'drafted' so generation isn't blocked by a flaky local model.
            lead.status = LeadStatus.qc_pending.value
            session.commit()
            try:
                qc_result = qc_check(lead, pain, email)
                email.qc_status = qc_result.status
                email.qc_checked_at = datetime.utcnow()
                email.qc_flags_json = flags_to_json(qc_result.flags)
                email.qc_model_used = qc_result.model_used
                if qc_result.status == "flagged":
                    lead.status = LeadStatus.qc_flagged.value
                    event_type = "qc_flagged"
                else:
                    lead.status = LeadStatus.drafted.value
                    event_type = "qc_passed"
                session.commit()
                log_event(session, lead.id, event_type, {
                    "email_id": email.id,
                    "model_used": qc_result.model_used,
                    "elapsed_seconds": qc_result.elapsed_seconds,
                    "flags": json.loads(email.qc_flags_json or "[]"),
                    "campaign": campaign_name,
                })
            except Exception as e:
                print(f"[qc] failed for lead {lead.id}: {e}")
                lead.status = LeadStatus.drafted.value
                session.commit()

            generated_count += 1

        session.commit()
        print(f"Generated {generated_count} drafts for campaign '{campaign_name}'")
        print(f"Review with: uv run python -m trispoke.export --campaign '{campaign_name}'")

def main():
    parser = argparse.ArgumentParser(description="Generate email drafts")
    parser.add_argument("--campaign", required=True, help="Campaign name")
    parser.add_argument("--mode", choices=["local_only", "claude_only", "hybrid"],
                       default="hybrid", help="Generation mode")

    args = parser.parse_args()
    generate_drafts(args.campaign, args.mode)

if __name__ == "__main__":
    main()