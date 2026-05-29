"""DEPRECATED in V1.5 — legacy SMTP-direct sending loop.

New campaigns use :mod:`trispoke.sender.apollo_push_worker` instead. This
module is kept so campaigns saved with `mode='smtp_direct'` (the legacy
fallback) continue to send. Do not extend this for new functionality.
"""
import time
import signal
import sys
from datetime import datetime, timedelta
from trispoke.db.session import get_session
from trispoke.db.models import Email, Lead, Send, SendStatus, LeadStatus, Campaign
from trispoke.db.event_log import log_event
from trispoke.sender.smtp_client import SmtpClient, SmtpError
from trispoke.sender.inbox_manager import pick_next_inbox
from trispoke.sender.ramp import current_daily_cap
from trispoke.config import get_settings
from sqlalchemy import func

settings = get_settings()
POLL_INTERVAL = 60  # seconds
PACE_SECONDS = 90   # seconds between sends

class SenderLoop:
    def __init__(self):
        self.running = True
        self.current_send = None
        
        # Register signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully"""
        print("\nShutting down gracefully...")
        self.running = False
        sys.exit(0)
    
    def _get_bounce_rate(self) -> float:
        """Get bounce rate over last 100 sends"""
        with get_session() as session:
            recent_sends = session.query(Send).order_by(
                Send.created_at.desc()
            ).limit(100).all()
            
            if len(recent_sends) < 10:
                return 0.0
            
            bounces = sum(1 for s in recent_sends if s.status == SendStatus.bounced.value)
            return bounces / len(recent_sends)
    
    def _check_bounce_protection(self) -> bool:
        """Check if bounce rate exceeds 3%"""
        bounce_rate = self._get_bounce_rate()
        if bounce_rate > 0.03:
            print(f"WARNING: Bounce rate exceeds 3% ({bounce_rate:.2%}). Pausing sends.")
            return False
        return True
    
    def _process_pending_emails(self):
        """Process emails with lead status='approved'"""
        with get_session() as session:
            # Get leads with status='approved' and their emails
            leads = session.query(Lead).filter(
                Lead.status == LeadStatus.approved.value
            ).all()
            
            if not leads:
                return
            
            # Group by campaign
            campaigns = {}
            for lead in leads:
                email = session.query(Email).filter_by(lead_id=lead.id).order_by(
                    Email.created_at.desc()
                ).first()
                
                if not email:
                    continue
                
                campaign = session.query(Campaign).get(lead.campaign_id)
                if campaign.name not in campaigns:
                    campaigns[campaign.name] = []
                campaigns[campaign.name].append((lead, email))
            
            # Process each campaign
            for campaign_name, items in campaigns.items():
                self._process_campaign_emails(campaign_name, items, session)
    
    def _process_campaign_emails(self, campaign_name: str, items: list, session):
        """Process emails for a specific campaign"""
        if not self._check_bounce_protection():
            return
        
        daily_cap = current_daily_cap(campaign_name)
        
        for lead, email in items:
            if not self.running:
                break
            
            # Pick inbox respecting daily cap
            inbox = pick_next_inbox(daily_cap)
            if not inbox:
                print(f"All inboxes at daily cap for {campaign_name}")
                continue
            
            try:
                # Send email
                smtp_client = SmtpClient(
                    host=inbox.smtp_host,
                    port=inbox.smtp_port,
                    username=inbox.smtp_username,
                    password=inbox.smtp_password
                )
                
                result = smtp_client.send_email(
                    to=lead.email,
                    subject=email.subject,
                    body=email.body,
                    from_inbox=inbox.address
                )
                
                # Record send
                send = Send(
                    email_id=email.id,
                    sent_from_inbox=inbox.address,
                    sent_at=result.sent_at,
                    smtp_message_id=result.smtp_message_id,
                    status=SendStatus.sent.value
                )
                session.add(send)
                session.commit()
                
                # Update lead status
                lead.status = LeadStatus.sent.value
                session.commit()
                
                # Log event
                log_event(session, lead.id, "email_sent", {
                    "email_id": email.id,
                    "from_inbox": inbox.address,
                    "smtp_message_id": result.smtp_message_id,
                    "campaign": campaign_name
                })
                
                print(f"Sent email to {lead.email}")
                
                # Pace
                time.sleep(PACE_SECONDS)
                
            except SmtpError as e:
                print(f"Failed to send to {lead.email}: {e}")
                send = Send(
                    email_id=email.id,
                    sent_from_inbox=inbox.address,
                    sent_at=datetime.utcnow(),
                    status=SendStatus.failed.value
                )
                session.add(send)
                session.commit()
    
    def run(self):
        """Main loop"""
        print("Sender loop started. Press Ctrl+C to stop.")
        
        while self.running:
            try:
                self._process_pending_emails()
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                print("\nShutting down...")
                break
            except Exception as e:
                print(f"Error in sender loop: {e}")
                time.sleep(POLL_INTERVAL)

def main():
    loop = SenderLoop()
    loop.run()

if __name__ == "__main__":
    main()