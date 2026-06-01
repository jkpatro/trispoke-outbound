from dataclasses import dataclass
from typing import Optional, List
import json
from trispoke.config import get_settings
from trispoke.db.session import get_session
from trispoke.db.models import Send
from sqlalchemy import func
from datetime import datetime, timedelta

@dataclass
class InboxConfig:
    address: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    imap_host: str
    imap_port: int
    imap_username: str
    imap_password: str

def _parse_inboxes_config() -> List[InboxConfig]:
    """Parse inboxes from settings.

    Per-inbox env vars (preferred):
      INBOX_1_ADDRESS=connect@company.com
      INBOX_1_SMTP_HOST=...   INBOX_1_SMTP_PORT=...
      INBOX_1_SMTP_USERNAME=...   INBOX_1_SMTP_PASSWORD=...
      INBOX_1_IMAP_HOST=...   INBOX_1_IMAP_PORT=...
      INBOX_1_IMAP_USERNAME=...   INBOX_1_IMAP_PASSWORD=...

    Per-inbox IMAP fields fall back to the global IMAP_* settings when omitted.
    If no INBOX_N_ADDRESS is set, a single inbox is built from global SMTP/IMAP.
    """
    settings = get_settings()

    inboxes = []
    i = 1
    while True:
        address = getattr(settings, f'inbox_{i}_address', None)
        if not address:
            break

        inbox = InboxConfig(
            address=address,
            smtp_host=getattr(settings, f'inbox_{i}_smtp_host'),
            smtp_port=getattr(settings, f'inbox_{i}_smtp_port'),
            smtp_username=getattr(settings, f'inbox_{i}_smtp_username'),
            smtp_password=getattr(settings, f'inbox_{i}_smtp_password'),
            imap_host=getattr(settings, f'inbox_{i}_imap_host', settings.imap_host),
            imap_port=getattr(settings, f'inbox_{i}_imap_port', settings.imap_port),
            imap_username=getattr(settings, f'inbox_{i}_imap_username', settings.imap_username),
            imap_password=getattr(settings, f'inbox_{i}_imap_password', settings.imap_password),
        )
        inboxes.append(inbox)
        i += 1

    if not inboxes:
        from trispoke.secrets_store import secret as _secret
        inboxes.append(InboxConfig(
            address=settings.smtp_username,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=_secret("SMTP_PASSWORD", settings.smtp_password),
            imap_host=settings.imap_host,
            imap_port=settings.imap_port,
            imap_username=settings.imap_username,
            imap_password=_secret("IMAP_PASSWORD", settings.imap_password),
        ))

    return inboxes

def today_sends_for_inbox(inbox_address: str) -> int:
    """Get number of sends from an inbox today"""
    with get_session() as session:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        count = session.query(Send).filter(
            Send.sent_from_inbox == inbox_address,
            Send.sent_at >= today_start
        ).count()
        
        return count

def pick_next_inbox(daily_cap: int) -> Optional[InboxConfig]:
    """Pick the inbox with lowest send count today, if under cap"""
    inboxes = _parse_inboxes_config()
    
    best_inbox = None
    best_count = float('inf')
    
    for inbox in inboxes:
        count = today_sends_for_inbox(inbox.address)
        
        if count < daily_cap and count < best_count:
            best_inbox = inbox
            best_count = count
    
    return best_inbox