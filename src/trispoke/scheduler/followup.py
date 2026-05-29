from datetime import datetime, timedelta
from sqlalchemy import select
from trispoke.db.session import get_session
from trispoke.db.models import Lead, Email, Send, Reply, Campaign, LeadStatus, SendStatus
from trispoke.db.event_log import log_event
from trispoke.llm.router import LLMRouter
from trispoke.pain_analyzer import analyze

def schedule_followups():
    """Generate follow-ups for leads with no reply after 4 days"""
    with get_session() as session:
        four_days_ago = datetime.utcnow() - timedelta(days=4)

        # Lead is eligible when its original email (parent_email_id IS NULL)
        # has a successful Send whose sent_at is at least 4 days old.
        original_send_subq = (
            select(Email.lead_id, Send.sent_at)
            .join(Send, Send.email_id == Email.id)
            .where(
                Email.parent_email_id.is_(None),
                Send.status == SendStatus.sent.value,
                Send.sent_at <= four_days_ago,
            )
            .subquery()
        )

        leads = (
            session.query(Lead)
            .join(original_send_subq, original_send_subq.c.lead_id == Lead.id)
            .filter(Lead.status == LeadStatus.sent.value)
            .all()
        )

        router = LLMRouter()
        followup_count = 0

        for lead in leads:
            reply = session.query(Reply).filter_by(lead_id=lead.id).first()
            if reply:
                continue

            original_email = session.query(Email).filter(
                Email.lead_id == lead.id,
                Email.parent_email_id.is_(None),
            ).order_by(Email.created_at.asc()).first()

            if not original_email:
                continue
            
            # Check if follow-up already exists
            existing_followup = session.query(Email).filter(
                Email.lead_id == lead.id,
                Email.parent_email_id == original_email.id
            ).first()
            
            if existing_followup:
                continue  # Follow-up already generated
            
            # Get campaign
            campaign = session.query(Campaign).get(lead.campaign_id)
            
            # Analyze pain (use cached analysis if available)
            from trispoke.db.models import PainAnalysis
            pain_record = session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
            
            if not pain_record:
                pain = analyze(lead)
            else:
                from trispoke.pain_analyzer import PainTriple
                pain = PainTriple(
                    chronic=pain_record.chronic,
                    acute=pain_record.acute,
                    trigger=pain_record.trigger,
                    confidence=pain_record.confidence
                )
            
            # Generate follow-up (renders follow_up.j2 directly, no LLM call)
            try:
                followup_draft = router.generate_email(
                    lead, pain, campaign.mode, is_follow_up=True
                )
                
                # Create follow-up email record
                followup_email = Email(
                    lead_id=lead.id,
                    campaign_id=lead.campaign_id,
                    subject=followup_draft.subject,
                    body=followup_draft.body,
                    model_used=followup_draft.model_used,
                    tokens_used=followup_draft.tokens_used,
                    generation_seconds=followup_draft.generation_seconds,
                    parent_email_id=original_email.id
                )
                session.add(followup_email)
                session.commit()
                
                # Update lead status
                lead.status = LeadStatus.follow_up_pending.value
                session.commit()
                
                # Log event
                log_event(session, lead.id, "email_generated", {
                    "type": "followup",
                    "parent_email_id": original_email.id,
                    "model_used": followup_draft.model_used,
                    "campaign": campaign.name
                })
                
                followup_count += 1
                
            except Exception as e:
                print(f"Error generating follow-up for lead {lead.id}: {e}")
                continue
        
        return followup_count

def check_no_replies():
    """Transition leads to 'closed_no_reply' 8 days after the follow-up was sent."""
    with get_session() as session:
        eight_days_ago = datetime.utcnow() - timedelta(days=8)

        # A lead is closeable when its follow-up email (parent_email_id IS NOT NULL)
        # has a successful Send whose sent_at is at least 8 days old.
        followup_send_subq = (
            select(Email.lead_id)
            .join(Send, Send.email_id == Email.id)
            .where(
                Email.parent_email_id.is_not(None),
                Send.status == SendStatus.sent.value,
                Send.sent_at <= eight_days_ago,
            )
            .subquery()
        )

        leads = (
            session.query(Lead)
            .join(followup_send_subq, followup_send_subq.c.lead_id == Lead.id)
            .filter(Lead.status.in_([
                LeadStatus.sent.value,
                LeadStatus.follow_up_pending.value,
            ]))
            .all()
        )

        closed_count = 0
        for lead in leads:
            reply = session.query(Reply).filter_by(lead_id=lead.id).first()
            if reply:
                lead.status = LeadStatus.replied.value
            else:
                lead.status = LeadStatus.closed_no_reply.value

            session.commit()
            closed_count += 1

        return closed_count

def main():
    print("Scheduling follow-ups...")
    followup_count = schedule_followups()
    print(f"Generated {followup_count} follow-ups")
    
    print("Closing leads with no replies...")
    closed_count = check_no_replies()
    print(f"Closed {closed_count} leads")

if __name__ == "__main__":
    main()