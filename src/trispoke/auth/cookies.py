"""Browser-cookie persistence for the Supabase refresh token.

Email + password sign-in happens entirely inside the Streamlit app (form submit
→ rerun), so session_state survives the login itself. The only thing that wipes
session_state is a hard browser refresh — which is exactly what this module
guards against: we stash the GoTrue refresh token in a cookie and, on a cold
start, exchange it for a fresh session (see session.hydrate_from_cookie).

Everything here degrades gracefully: if streamlit-cookies-controller isn't
installed (or the component fails), the app still works — you just have to sign
in again after a hard refresh.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

COOKIE_NAME = "ts_auth_rt"  # ts = trispoke, rt = refresh token
_COOKIE_STATE_KEY = "ts_auth_cookies"  # CookieController's own session_state key
_DEFAULT_MAX_AGE_DAYS = 30


def _controller():
    """Construct a CookieController for THIS run, or None if unavailable.

    Deliberately not cached across runs: CookieController persists its cookie
    snapshot under `_COOKIE_STATE_KEY` in session_state and only renders the
    underlying component the first time that key is absent (subsequent
    constructions in the same run read from session_state — no duplicate
    widget). Caching the object instead would freeze the empty first-paint
    snapshot and break refresh-time hydration.
    """
    try:
        from streamlit_cookies_controller import CookieController
    except Exception as exc:  # library not installed
        logger.warning(
            "streamlit-cookies-controller unavailable; sessions won't survive a "
            "browser refresh: %s",
            exc,
        )
        return None
    try:
        return CookieController(key=_COOKIE_STATE_KEY)
    except Exception as exc:
        logger.warning("CookieController init failed: %s", exc)
        return None


def read_refresh_token() -> str | None:
    """Read the refresh-token cookie.

    Prefers `st.context.cookies` (the cookies the browser sent with the current
    request) because that is populated immediately on a cold start / hard
    refresh — no component round-trip, no first-paint flash. Falls back to the
    JS component only if the request cookies aren't available.
    """
    try:
        ck = getattr(st.context, "cookies", None)
        if ck:
            val = ck.get(COOKIE_NAME)
            if val:
                return str(val).strip() or None
    except Exception as exc:
        logger.debug("st.context.cookies read failed: %s", exc)

    ctrl = _controller()
    if ctrl is None:
        return None
    try:
        val = ctrl.get(COOKIE_NAME)
    except Exception as exc:
        logger.warning("refresh-token cookie read failed: %s", exc)
        return None
    if not val:
        return None
    return str(val).strip() or None


def write_refresh_token(token: str, max_age_days: int = _DEFAULT_MAX_AGE_DAYS) -> None:
    ctrl = _controller()
    if ctrl is None or not token:
        return
    max_age = max_age_days * 24 * 3600
    try:
        ctrl.set(COOKIE_NAME, token, max_age=max_age, same_site="lax")
    except TypeError:
        # Older/newer signature — fall back to the minimal call.
        try:
            ctrl.set(COOKIE_NAME, token)
        except Exception as exc:
            logger.warning("refresh-token cookie write failed: %s", exc)
    except Exception as exc:
        logger.warning("refresh-token cookie write failed: %s", exc)


def clear_refresh_token() -> None:
    ctrl = _controller()
    if ctrl is None:
        return
    try:
        ctrl.remove(COOKIE_NAME)
    except Exception as exc:
        logger.warning("refresh-token cookie clear failed: %s", exc)
