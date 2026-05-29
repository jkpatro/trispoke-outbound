"""Receiver layer — routes to Apollo events poller or legacy IMAP poller.

V1.5: Apollo-mode campaigns pull events via
:mod:`trispoke.receiver.apollo_events_poller`. SMTP-direct campaigns still
use the legacy :mod:`trispoke.receiver.imap_poller` (deprecated).
"""

from __future__ import annotations


def start_poller(mode: str = "apollo") -> None:
    """Start the appropriate poller for the configured sending mode.

    mode='apollo'      → Apollo events poller (default)
    mode='smtp_direct' → legacy IMAP poller (deprecated)
    """
    if mode == "smtp_direct":
        from trispoke.receiver.imap_poller import main as _imap_main
        _imap_main()
        return
    from trispoke.receiver.apollo_events_poller import main as _apollo_main
    _apollo_main()
