import argparse
import sys
from trispoke.db.session import get_session
from trispoke.db.models import Email, Lead, Campaign, LeadStatus
from trispoke.db.event_log import log_event

def approve_drafts(campaign_name: str, limit: int = None):
    """Approve drafted emails for a campaign"""
    with get_session() as session:
        # Get campaign
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            print(f"Error: Campaign '{campaign_name}' not found")
            sys.exit(1)
        
        # Get drafted emails
        query = session.query(Email).join(
            Lead, Email.lead_id == Lead.id
        ).filter(
            Lead.campaign_id == campaign.id,
            Lead.status == LeadStatus.drafted.value
        )
        
        if limit:
            query = query.limit(limit)
        
        emails = query.all()
        
        if not emails:
            print(f"No drafted emails found in campaign '{campaign_name}'")
            return
        
        # Approve emails
        for email in emails:
            lead = session.query(Lead).get(email.lead_id)
            lead.status = LeadStatus.approved.value
            
            log_event(session, lead.id, "email_approved", {
                "email_id": email.id,
                "campaign": campaign_name
            })
        
        session.commit()
        print(f"Approved {len(emails)} emails in campaign '{campaign_name}'")

def main():
    parser = argparse.ArgumentParser(description="Approve drafted emails")
    parser.add_argument("--campaign", required=True, help="Campaign name")
    parser.add_argument("--limit", type=int, default=None, help="Max emails to approve")
    
    args = parser.parse_args()
    approve_drafts(args.campaign, args.limit)

if __name__ == "__main__":
    main()