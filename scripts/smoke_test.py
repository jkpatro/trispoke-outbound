#!/usr/bin/env python
"""User-facing post-install smoke check.

Runs a battery of connectivity / config / IO checks and prints a green
'All checks passed' or a red list of failures. Exit code 0 on success,
non-zero on any failure.

Usage:
    uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


# --- ANSI colour helpers (Windows: enable VT mode if possible) ---------------

def _enable_vt_on_windows() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


_enable_vt_on_windows()

# Force UTF-8 on Windows consoles so the check/cross glyphs render.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"


# --- Check registry ----------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


@contextmanager
def _check(name: str):
    print(f"  {_DIM}…{_RESET} {name}", end="\r")
    try:
        yield
        print(f"  {_GREEN}✓{_RESET} {name}                ")
        _results.append((name, True, ""))
    except _Skip as s:
        print(f"  {_YELLOW}~{_RESET} {name} {_DIM}({s}){_RESET}")
        _results.append((name, True, f"skipped: {s}"))
    except Exception as e:
        print(f"  {_RED}✗{_RESET} {name}")
        print(f"      {_RED}{type(e).__name__}: {e}{_RESET}")
        _results.append((name, False, f"{type(e).__name__}: {e}"))


class _Skip(Exception):
    """Raised inside a check to mark it skipped (still a success)."""


# --- Individual checks -------------------------------------------------------

def check_imports() -> None:
    import importlib

    for mod in (
        "trispoke.config",
        "trispoke.db.session",
        "trispoke.db.models",
        "trispoke.import_apollo",
        "trispoke.enrich",
        "trispoke.generate",
        "trispoke.export",
        "trispoke.sender.smtp_client",
        "trispoke.sender.sender_loop",
        "trispoke.receiver.imap_poller",
        "trispoke.scheduler.followup",
        # NB: skipping trispoke.ui.app — its module-level st.set_page_config
        # produces noisy "missing ScriptRunContext" warnings outside `streamlit run`.
        # The UI is exercised by `streamlit run src/trispoke/ui/app.py` instead.
    ):
        importlib.import_module(mod)


def check_settings() -> None:
    from trispoke.config import get_settings

    s = get_settings()
    # V1.5: only APOLLO_API_KEY is required for the Apollo-out flow.
    # SMTP/IMAP moved to the --legacy check.
    if not s.apollo_api_key:
        raise RuntimeError("Missing required .env value: APOLLO_API_KEY")


def check_sqlite_rw() -> None:
    from sqlalchemy import create_engine, text

    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'smoke.db'}"
        eng = create_engine(url)
        try:
            with eng.connect() as c:
                c.execute(text("CREATE TABLE t (x INTEGER)"))
                c.execute(text("INSERT INTO t VALUES (1)"))
                c.commit()
                row = c.execute(text("SELECT x FROM t")).fetchone()
                assert row == (1,)
        finally:
            # Release the file handle so Windows can clean up the temp dir.
            eng.dispose()


def check_ollama() -> None:
    import httpx
    from trispoke.config import get_settings

    s = get_settings()
    try:
        r = httpx.get(f"{s.ollama_host}/api/tags", timeout=2.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise _Skip(f"Ollama not running at {s.ollama_host}")
    r.raise_for_status()
    models = [m.get("name") for m in r.json().get("models", [])]
    if not models:
        raise _Skip("Ollama running but no models pulled")


def check_anthropic() -> None:
    from trispoke.config import get_settings

    s = get_settings()
    if not s.anthropic_api_key:
        raise _Skip("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import Anthropic
    except ImportError:
        raise _Skip("anthropic SDK not installed")

    client = Anthropic(api_key=s.anthropic_api_key)
    # Cheapest auth check: list models (metadata, no token spend).
    list(client.models.list(limit=1))


def check_apollo_auth() -> None:
    """Required: Apollo auth probe via /auth/health."""
    import httpx

    from trispoke.config import get_settings

    s = get_settings()
    if not s.apollo_api_key:
        raise RuntimeError("APOLLO_API_KEY not set")
    r = httpx.get(
        "https://api.apollo.io/api/v1/auth/health",
        headers={"x-api-key": s.apollo_api_key},
        timeout=5.0,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(f"Apollo rejected key ({r.status_code})")
    r.raise_for_status()


def check_apollo_mailboxes() -> None:
    """Required for Apollo-out sending: at least one connected mailbox."""
    from trispoke.apollo.client import ApolloClient

    boxes = ApolloClient().list_mailboxes()
    if not boxes:
        raise RuntimeError(
            "No mailboxes connected in Apollo — sending will fail. "
            "Connect at least one in the Apollo dashboard."
        )


def check_apollo_sequences() -> None:
    """Verify API key has sequence-write scope.

    Apollo's GET /emailer_campaigns isn't a public list endpoint on every
    plan — it 404s on some workspaces even when sequence creation works.
    We only treat 401/403 as a hard fail; 404 we skip and rely on the push
    worker to surface any real permissions issue on first use.
    """
    import httpx

    from trispoke.config import get_settings

    s = get_settings()
    r = httpx.get(
        "https://api.apollo.io/api/v1/emailer_campaigns",
        headers={"x-api-key": s.apollo_api_key},
        params={"per_page": 1},
        timeout=5.0,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Apollo API key lacks sequence-write scope (401/403 on /emailer_campaigns)"
        )
    if r.status_code == 404:
        raise _Skip("GET /emailer_campaigns not exposed on this Apollo plan")
    r.raise_for_status()


def check_abacus() -> None:
    """Cheap auth probe against Abacus.AI RouteLLM via GET /v1/models."""
    import httpx

    from trispoke.config import get_settings

    s = get_settings()
    if not s.abacus_api_key:
        raise _Skip("ABACUS_API_KEY not set")
    url = f"{s.abacus_base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {s.abacus_api_key}"}
    try:
        r = httpx.get(url, headers=headers, timeout=5.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        raise _Skip(f"cannot reach {url} ({e})")
    if r.status_code in (401, 403):
        raise RuntimeError("ABACUS_API_KEY missing or invalid")
    r.raise_for_status()


def check_smtp_login() -> None:
    """LEGACY (--legacy only). Skipped when SMTP_HOST is empty."""
    import smtplib

    from trispoke.config import get_settings

    s = get_settings()
    if not s.smtp_host or not s.smtp_username:
        raise _Skip("SMTP not configured (legacy SMTP-direct mode only)")
    try:
        if s.smtp_port == 465:
            srv = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=5)
        else:
            srv = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=5)
            srv.starttls()
    except (socket.gaierror, ConnectionRefusedError, TimeoutError, OSError) as e:
        raise _Skip(f"cannot reach {s.smtp_host}:{s.smtp_port} ({e})")

    try:
        srv.login(s.smtp_username, s.smtp_password)
    finally:
        try:
            srv.quit()
        except Exception:
            pass


def check_imap_login() -> None:
    """LEGACY (--legacy only). Skipped when IMAP_HOST is empty."""
    from imap_tools import MailBox, MailboxLoginError

    from trispoke.config import get_settings

    s = get_settings()
    if not s.imap_host or not s.imap_username:
        raise _Skip("IMAP not configured (legacy SMTP-direct mode only)")
    try:
        mb = MailBox(s.imap_host)
    except (socket.gaierror, ConnectionRefusedError, TimeoutError, OSError) as e:
        raise _Skip(f"cannot reach {s.imap_host} ({e})")

    try:
        mb.login(s.imap_username, s.imap_password)
        mb.logout()
    except MailboxLoginError as e:
        raise RuntimeError(f"IMAP login failed: {e}")


# --- Driver ------------------------------------------------------------------

CHECKS: list[tuple[str, Callable[[], None]]] = [
    ("Module imports", check_imports),
    ("Required .env settings", check_settings),
    ("SQLite read/write", check_sqlite_rw),
    ("Ollama /api/tags", check_ollama),
    ("Anthropic auth (BYOK)", check_anthropic),
    ("Abacus auth (RouteLLM)", check_abacus),
    ("Apollo /auth/health", check_apollo_auth),
    ("Apollo mailboxes connected", check_apollo_mailboxes),
    ("Apollo sequence-write scope", check_apollo_sequences),
]

LEGACY_CHECKS: list[tuple[str, Callable[[], None]]] = [
    ("SMTP login (legacy)", check_smtp_login),
    ("IMAP login (legacy)", check_imap_login),
]


def main() -> int:
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument(
        "--legacy", action="store_true",
        help="Also run SMTP/IMAP checks (only relevant for sending_mode='smtp_direct')",
    )
    args = parser.parse_args()

    print(f"{_DIM}trispoke-outbound smoke checks{_RESET}\n")
    for name, fn in CHECKS:
        with _check(name):
            fn()
    if args.legacy:
        print(f"\n{_DIM}-- legacy SMTP-direct --{_RESET}")
        for name, fn in LEGACY_CHECKS:
            with _check(name):
                fn()

    failures = [r for r in _results if not r[1]]
    print()
    if failures:
        print(f"{_RED}✗ {len(failures)} check(s) failed:{_RESET}")
        for name, _ok, msg in failures:
            print(f"  - {name}: {msg}")
        return 1

    print(f"{_GREEN}✓ All checks passed{_RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
