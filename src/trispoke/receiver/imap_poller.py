"""DEPRECATED in V1.5 — only used by legacy SMTP-direct campaigns.

Apollo-mode campaigns receive events via
:mod:`trispoke.receiver.apollo_events_poller`.
"""
import time
import argparse
import sys
from datetime import datetime
from imap_tools import MailBox, A
from trispoke.db.session import get_session
from trispoke.db.models import Send, Reply, Lead, Email, LeadStatus, ReplyClassification
from trispoke.db.event_log import log_event
from trispoke.receiver.classifier import classify, is_bounce
from trispoke.config import get_settings

settings = get_settings()
POLL_INTERVAL = 300  # 5 minutes

def extract_message_id(email_msg) -> str:
    """Extract message ID from In-Reply-To or References header"""
    # Try In-Reply-To first
    in_reply_to = email_msg.headers.get('In-Reply-To', '')
    if in_reply_to:
        # Strip angle brackets if present
        return in_reply_to.strip('<> ')
    
    # Try References header (last message ID in the chain)
    references = email_msg.headers.get('References', '')
    if references:
        # Get the last message ID from the space-separated list
        msg_ids = references.split()
        if msg_ids:
            return msg_ids[-1].strip('<> ')
    
    return None

def match_reply_to_send(from_address: str, smtp_message_id: str = None):
    """Find the send record that this reply matches"""
    with get_session() as session:
        # Try to match by SMTP message ID first
        if smtp_message_id:
            send = session.query(Send).filter_by(
                smtp_message_id=smtp_message_id
            ).first()
            if send:
                return send

        # Fallback: most recent Send to a Lead whose email matches from_address.
        send = (
            session.query(Send)
            .join(Email, Send.email_id == Email.id)
            .join(Lead, Email.lead_id == Lead.id)
            .filter(Lead.email == from_address)
            .order_by(Send.sent_at.desc())
            .first()
        )
        return send

def process_inbox(mailbox: MailBox, inbox_name: str = "INBOX"):
    """Process new messages in an inbox"""
    with get_session() as session:
        # Get unread messages. imap-tools uses `seen=False`, not `unseen=True`.
        messages = mailbox.fetch(A(seen=False), headers_only=False)
        
        processed = 0
        for msg in messages:
            try:
                # Extract message ID
                message_id = extract_message_id(msg)
                
                # Match to a send
                send = match_reply_to_send(msg.from_, message_id)
                if not send:
                    # Mark as read and skip
                    mailbox.flag([msg.uid], '+\\Seen', silent=True)
                    continue
                
                # Get the lead
                email = session.query(Email).get(send.email_id)
                lead = session.query(Lead).get(email.lead_id)
                
                # Classify the reply
                classification = classify(msg.subject or '', msg.text or '')
                
                # Check for bounce
                is_bounce_email = is_bounce(msg.subject or '', msg.text or '')
                
                if is_bounce_email:
                    classification = 'bounce'
                    lead.status = LeadStatus.bounced.value
                else:
                    lead.status = LeadStatus.replied.value
                
                # Insert reply record
                reply = Reply(
                    lead_id=lead.id,
                    email_id=email.id,
                    received_at=msg.date or datetime.utcnow(),
                    from_address=msg.from_,
                    subject=msg.subject or '',
                    body=msg.text or '',
                    classification=classification
                )
                session.add(reply)
                session.commit()
                
                # Log event
                log_event(session, lead.id, "reply_received", {
                    "from": msg.from_,
                    "classification": classification,
                    "is_bounce": is_bounce_email
                })
                
                # Mark as read
                mailbox.flag([msg.uid], '+\\Seen', silent=True)
                processed += 1
                
            except Exception as e:
                print(f"Error processing message: {e}")
                continue
        
        return processed

def poll_once():
    """Poll every configured inbox once."""
    from trispoke.sender.inbox_manager import _parse_inboxes_config

    total = 0
    for inbox in _parse_inboxes_config():
        try:
            mailbox = MailBox(inbox.imap_host)
            mailbox.login(inbox.imap_username, inbox.imap_password)
            try:
                processed = process_inbox(mailbox, inbox.address)
            finally:
                mailbox.logout()
            print(f"[{inbox.address}] Processed {processed} replies")
            total += processed
        except Exception as e:
            print(f"[{inbox.address}] Error connecting to IMAP: {e}")

    return total

def main():
    parser = argparse.ArgumentParser(description="Poll IMAP for replies")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="Poll interval in seconds")
    
    args = parser.parse_args()
    
    if args.once:
        poll_once()
    else:
        print(f"Starting IMAP poller (interval: {args.interval}s)")
        while True:
            try:
                poll_once()
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nShutting down...")
                break

if __name__ == "__main__":
    main()