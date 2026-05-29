"""DEPRECATED in V1.5 — only used by the legacy SMTP-direct sender_loop.

Apollo-mode campaigns send via :mod:`trispoke.sender.apollo_push_worker`.
"""
import smtplib
import uuid
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime

@dataclass
class SendResult:
    smtp_message_id: Optional[str]
    sent_at: datetime
    error: Optional[str]

class SmtpError(Exception):
    """Domain-specific SMTP error"""
    pass

class SmtpClient:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        from_inbox: str,
        reply_to: Optional[str] = None
    ) -> SendResult:
        """Send an email via SMTP"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = from_inbox
            msg['To'] = to
            
            # Generate Message-ID. Store the bare form (uuid@domain) in the
            # DB so it round-trips with the IMAP extractor, which strips the
            # angle brackets when reading In-Reply-To / References headers.
            # The on-the-wire header keeps the angle brackets per RFC 2822.
            domain = from_inbox.split('@')[1]
            message_id = f"{uuid.uuid4()}@{domain}"
            msg['Message-ID'] = f"<{message_id}>"
            
            # Add List-Unsubscribe header
            msg['List-Unsubscribe'] = '<mailto:unsubscribe@trispokeservices.com>'
            
            if reply_to:
                msg['Reply-To'] = reply_to
            
            # Add body
            part = MIMEText(body, 'plain')
            msg.attach(part)
            
            # Connect and send
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port)
            else:
                server = smtplib.SMTP(self.host, self.port)
                server.starttls()
            
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            
            return SendResult(
                smtp_message_id=message_id,
                sent_at=datetime.utcnow(),
                error=None
            )
        
        except smtplib.SMTPException as e:
            raise SmtpError(f"SMTP error: {str(e)}")
        except Exception as e:
            raise SmtpError(f"Failed to send email: {str(e)}")