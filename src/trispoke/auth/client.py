"""Supabase client factories for email + password auth (GoTrue).

Two clients:
  - anon client    — built with the publishable/anon key. Handles the
                     end-user flows: sign_in_with_password, update_user,
                     refresh_session. Cached on st.session_state so the session
                     a sign-in establishes survives across reruns within the
                     same browser tab.
  - service client — built with the service_role secret. Handles privileged
                     admin operations (create user, reset another user's
                     password). NEVER constructed from anything the browser can
                     read; the key lives only in the server's .env. Cached at
                     module scope (no per-user session state on it).

Both are created lazily — importing this module never touches the network and
never requires Supabase to be configured, so dev/test runs with auth disabled
keep working.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from trispoke.config import get_settings

_ANON_CLIENT_KEY = "_supabase_anon_client"
_service_client = None  # module-level cache for the service-role client


def supabase_url() -> Optional[str]:
    url = (get_settings().supabase_url or "").strip().rstrip("/")
    return url or None


def supabase_anon_key() -> Optional[str]:
    key = (get_settings().supabase_anon_key or "").strip()
    return key or None


def supabase_service_role_key() -> Optional[str]:
    key = (get_settings().supabase_service_role_key or "").strip()
    return key or None


def is_configured() -> bool:
    """True when the basic (anon) auth flow can run."""
    return bool(supabase_url()) and bool(supabase_anon_key())


def has_service_role() -> bool:
    """True when privileged admin operations are available."""
    return bool(supabase_url()) and bool(supabase_service_role_key())


def get_anon_client():
    """Return a tab-scoped supabase.Client built with the anon key.

    Cached on st.session_state so the session set by sign_in_with_password is
    reused by later update_user / refresh_session calls in the same tab.
    Raises RuntimeError if Supabase isn't configured.
    """
    if not is_configured():
        raise RuntimeError(
            "Supabase URL or anon key not configured. Set SUPABASE_URL and "
            "SUPABASE_ANON_KEY in .env."
        )
    if _ANON_CLIENT_KEY not in st.session_state:
        from supabase import create_client

        st.session_state[_ANON_CLIENT_KEY] = create_client(
            supabase_url(), supabase_anon_key()
        )
    return st.session_state[_ANON_CLIENT_KEY]


def get_service_client():
    """Return the service-role supabase.Client, or None if no service key.

    Module-cached (not session-scoped) — it carries no per-user session and is
    only used for admin.* calls.
    """
    global _service_client
    if not has_service_role():
        return None
    if _service_client is None:
        from supabase import create_client

        _service_client = create_client(
            supabase_url(), supabase_service_role_key()
        )
    return _service_client
