"""Auth session helpers — Supabase Auth (GoTrue) email + password.

Sign-in/sign-up happen against GoTrue through the anon client; the resulting
session is cached on st.session_state and its refresh token is persisted in a
browser cookie (see auth.cookies) so a hard refresh re-hydrates instead of
bouncing the user back to the login screen.

Each GoTrue user is mirrored by a row in public.profiles, keyed by email. The
profile carries the app-level role / is_active / display name and links back to
the GoTrue user via profiles.auth_uid. Everything the rest of the app calls —
current_user_*, is_admin, is_account_active — reads from that cached profile.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from trispoke.auth import client as sb
from trispoke.auth import cookies
from trispoke.db.session import get_session

logger = logging.getLogger(__name__)

USER_KEY = "_auth_user"
ROLE_KEY = "_auth_role"
NAME_KEY = "_auth_full_name"
ACTIVE_KEY = "_auth_is_active"
AUTH_UID_KEY = "_auth_uid"
PROFILE_FETCHED_AT_KEY = "_auth_profile_fetched_at"
# Current GoTrue refresh token for this tab; persisted to the cookie on a normal
# (non-rerunning) authenticated render so the set actually commits to the browser.
SESSION_RT_KEY = "_auth_refresh_token"
# Last token value we wrote to the cookie — write only on change to avoid
# re-rendering the cookie component every interaction.
COOKIE_WRITTEN_KEY = "_auth_cookie_written"
# Set by sign_out() so a leftover cookie can't silently re-hydrate the same tab
# before a hard refresh clears session_state.
SIGNED_OUT_KEY = "_auth_signed_out"

BOOTSTRAP_ADMIN_EMAIL = "jkpatro@gmail.com"
MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Configuration / public read API (same shape the rest of the app already uses)
# ---------------------------------------------------------------------------


def is_auth_enabled() -> bool:
    """True when Supabase Auth is configured. When False the login gate is a
    no-op and the UI runs with open access (dev only)."""
    return sb.is_configured()


def is_authenticated() -> bool:
    if not is_auth_enabled():
        return True  # dev fallback — open access
    return st.session_state.get(USER_KEY) is not None


def current_user() -> Optional[dict]:
    return st.session_state.get(USER_KEY)


def current_user_email() -> Optional[str]:
    u = current_user()
    return u.get("email") if u else None


def current_user_name() -> Optional[str]:
    return st.session_state.get(NAME_KEY)


def current_user_role() -> str:
    """'admin' or 'user'. When auth is disabled, return 'admin' so dev isn't gated."""
    if not is_auth_enabled():
        return "admin"
    return st.session_state.get(ROLE_KEY) or "user"


def is_admin() -> bool:
    return current_user_role() == "admin"


def is_account_active() -> bool:
    """Fail-closed: False unless the cached profile flagged the user active."""
    if not is_auth_enabled():
        return True
    return bool(st.session_state.get(ACTIVE_KEY, False))


def profile_stale(ttl_seconds: int = 60) -> bool:
    return time.time() - st.session_state.get(PROFILE_FETCHED_AT_KEY, 0) > ttl_seconds


# ---------------------------------------------------------------------------
# Profile lookup / creation / reconciliation
# ---------------------------------------------------------------------------


def _row_to_profile(row) -> dict:
    return {
        "id": row[0],
        "email": row[1],
        "full_name": row[2],
        "role": row[3] or "user",
        "is_active": bool(row[4]),
        "auth_uid": row[5],
    }


def _select_profile_by_email(email: str) -> Optional[dict]:
    with get_session() as session:
        row = session.execute(
            text(
                """
                SELECT id::text, email, full_name, role::text, is_active,
                       auth_uid::text
                FROM public.profiles
                WHERE LOWER(email) = LOWER(:e)
                """
            ),
            {"e": email},
        ).fetchone()
    return _row_to_profile(row) if row else None


def _ensure_profile(email: str, auth_uid: Optional[str]) -> dict:
    """Return the profile for `email`, creating/reconciling it against GoTrue.

      - Existing profile: backfill auth_uid if missing; force bootstrap admin
        to admin/active. Returns the (possibly updated) row.
      - No profile yet: consume a matching invitation (role + active), else
        bootstrap admin (admin/active), else user/inactive (pending approval).
        New rows use the GoTrue uid as both profiles.id and profiles.auth_uid.
    """
    is_bootstrap = email.lower() == BOOTSTRAP_ADMIN_EMAIL

    with get_session() as session:
        existing = session.execute(
            text(
                """
                SELECT id::text, email, full_name, role::text, is_active,
                       auth_uid::text
                FROM public.profiles
                WHERE LOWER(email) = LOWER(:e)
                """
            ),
            {"e": email},
        ).fetchone()

        if existing:
            profile = _row_to_profile(existing)
            updates, params = [], {"id": profile["id"]}
            if auth_uid and profile.get("auth_uid") != auth_uid:
                updates.append("auth_uid = CAST(:uid AS UUID)")
                params["uid"] = auth_uid
                profile["auth_uid"] = auth_uid
            if is_bootstrap and (profile["role"] != "admin" or not profile["is_active"]):
                updates.append("role = 'admin'::public.user_role")
                updates.append("is_active = TRUE")
                profile["role"] = "admin"
                profile["is_active"] = True
            if updates:
                session.execute(
                    text(
                        f"UPDATE public.profiles SET {', '.join(updates)}, "
                        f"updated_at = NOW() WHERE id = CAST(:id AS UUID)"
                    ),
                    params,
                )
                session.commit()
            return profile

        # No profile — create one, consuming an invitation if present.
        inv = session.execute(
            text(
                "SELECT role::text FROM public.user_invitations "
                "WHERE LOWER(email) = LOWER(:e)"
            ),
            {"e": email},
        ).fetchone()
        if inv:
            role, is_active = inv[0] or "user", True
            session.execute(
                text("DELETE FROM public.user_invitations WHERE LOWER(email) = LOWER(:e)"),
                {"e": email},
            )
        elif is_bootstrap:
            role, is_active = "admin", True
        else:
            role, is_active = "user", False

        new_id = auth_uid or str(uuid.uuid4())
        try:
            session.execute(
                text(
                    """
                    INSERT INTO public.profiles (id, email, role, is_active, auth_uid)
                    VALUES (CAST(:id AS UUID), :e, CAST(:r AS public.user_role), :a,
                            CAST(:uid AS UUID))
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": new_id, "e": email, "r": role, "a": is_active, "uid": auth_uid},
            )
            session.commit()
        except IntegrityError:
            # A concurrent insert (or an email-cased duplicate) beat us to it —
            # re-read the authoritative row instead of crashing the sign-in.
            session.rollback()
            row = session.execute(
                text(
                    """
                    SELECT id::text, email, full_name, role::text, is_active,
                           auth_uid::text
                    FROM public.profiles
                    WHERE LOWER(email) = LOWER(:e)
                    """
                ),
                {"e": email},
            ).fetchone()
            if row:
                return _row_to_profile(row)

    return {
        "id": new_id,
        "email": email,
        "full_name": None,
        "role": role,
        "is_active": is_active,
        "auth_uid": auth_uid,
    }


def _persist_profile_into_session(profile: dict) -> None:
    st.session_state[USER_KEY] = {"id": profile["id"], "email": profile["email"]}
    st.session_state[ROLE_KEY] = profile.get("role") or "user"
    st.session_state[NAME_KEY] = profile.get("full_name")
    st.session_state[ACTIVE_KEY] = bool(profile.get("is_active"))
    st.session_state[AUTH_UID_KEY] = profile.get("auth_uid")
    st.session_state[PROFILE_FETCHED_AT_KEY] = time.time()


# ---------------------------------------------------------------------------
# GoTrue session establishment
# ---------------------------------------------------------------------------


def _establish_session(session_obj, user_obj) -> None:
    """Translate a fresh GoTrue session into our cached profile + stash the
    refresh token. The cookie is written later by persist_session_cookie() on a
    normal render so the set actually commits to the browser (writing here would
    race the st.rerun() that follows sign-in)."""
    email = getattr(user_obj, "email", None) or ""
    auth_uid = getattr(user_obj, "id", None)
    profile = _ensure_profile(email, auth_uid)
    _persist_profile_into_session(profile)
    rt = getattr(session_obj, "refresh_token", None)
    if rt:
        st.session_state[SESSION_RT_KEY] = rt
    st.session_state.pop(SIGNED_OUT_KEY, None)


def persist_session_cookie() -> None:
    """Write the current refresh token to the cookie if it changed.

    Called from the login gate on authenticated renders (which don't immediately
    rerun), so the cookie component has a chance to commit. No-op when the token
    is unchanged, so we don't re-render the component every interaction."""
    rt = st.session_state.get(SESSION_RT_KEY)
    if not rt:
        return
    if st.session_state.get(COOKIE_WRITTEN_KEY) == rt:
        return
    cookies.write_refresh_token(rt)
    st.session_state[COOKIE_WRITTEN_KEY] = rt


def clear_cookie_if_signed_out() -> None:
    """On the login screen after an explicit sign-out, re-assert cookie removal
    on a normal (non-rerunning) render so the JS remove actually commits."""
    if st.session_state.get(SIGNED_OUT_KEY):
        cookies.clear_refresh_token()


def _friendly_auth_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "invalid login" in msg or "invalid credentials" in msg:
        return "Incorrect email or password."
    if "email not confirmed" in msg:
        return "Email not confirmed. Ask an admin to confirm your account."
    return f"Sign-in failed: {exc}"


# ---------------------------------------------------------------------------
# Sign in / sign up / change password / sign out
# ---------------------------------------------------------------------------


def sign_in(email: str, password: str) -> tuple[bool, str]:
    if not is_auth_enabled():
        return False, "Auth is not configured."
    email = (email or "").strip().lower()
    if not email or not password:
        return False, "Enter your email and password."
    try:
        client = sb.get_anon_client()
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        return False, _friendly_auth_error(exc)
    if not res or not res.session or not res.user:
        return False, "Incorrect email or password."
    _establish_session(res.session, res.user)
    return True, "Signed in."


def _validate_new_password(pw: str) -> Optional[str]:
    if not pw or len(pw) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def sign_up(email: str, password: str) -> tuple[bool, str]:
    """Self-service registration. Provisions the GoTrue user via the service
    role (so no email-confirmation round-trip is needed), then signs them in.
    The new account lands inactive — gated behind admin approval — unless it was
    pre-invited."""
    if not is_auth_enabled():
        return False, "Auth is not configured."
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    pw_err = _validate_new_password(password)
    if pw_err:
        return False, pw_err

    service = sb.get_service_client()
    if service is None:
        return False, (
            "Self-signup is unavailable on this server (missing "
            "SUPABASE_SERVICE_ROLE_KEY). Ask an admin to create your account."
        )
    try:
        service.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True}
        )
    except Exception as exc:
        m = str(exc).lower()
        if "already" in m or "exists" in m or "registered" in m:
            return False, (
                "An account with this email already exists. Sign in instead, "
                "or ask an admin to reset your password."
            )
        return False, f"Could not create account: {exc}"

    ok, msg = sign_in(email, password)
    if not ok:
        return False, f"Account created but sign-in failed: {msg}"
    return True, "Account created."


def change_own_password(current_password: str, new_password: str) -> tuple[bool, str]:
    """Verify the current password (by re-authenticating), then set a new one."""
    if not is_auth_enabled():
        return False, "Auth is not configured."
    email = current_user_email()
    if not email:
        return False, "Not signed in."
    pw_err = _validate_new_password(new_password)
    if pw_err:
        return False, pw_err
    if current_password == new_password:
        return False, "New password must differ from the current one."

    try:
        client = sb.get_anon_client()
        reauth = client.auth.sign_in_with_password(
            {"email": email, "password": current_password}
        )
    except Exception:
        return False, "Current password is incorrect."
    if not reauth or not reauth.session:
        return False, "Current password is incorrect."

    try:
        client.auth.update_user({"password": new_password})
    except Exception as exc:
        return False, f"Could not update password: {exc}"

    # Persist whatever session the client now holds — update_user may have
    # rotated the tokens — falling back to the re-auth session if we can't read
    # it. Persisting the stale re-auth token here would strand the next refresh.
    fresh = None
    try:
        fresh = client.auth.get_session()
    except Exception:
        fresh = None
    if fresh is not None:
        _establish_session(fresh, getattr(fresh, "user", None) or reauth.user)
    else:
        _establish_session(reauth.session, reauth.user)
    return True, "Password updated."


def refresh_profile() -> None:
    """Force a re-read of the cached profile (e.g. after a name edit)."""
    email = current_user_email()
    if not email:
        return
    try:
        profile = _select_profile_by_email(email)
    except Exception as exc:
        logger.warning("refresh_profile select failed: %s", exc)
        return
    if profile:
        _persist_profile_into_session(profile)


def update_full_name(new_name: str) -> tuple[bool, str]:
    """Persist the user's display name via direct Postgres."""
    u = current_user()
    if not u or not u.get("id"):
        return False, "Not signed in."
    cleaned = (new_name or "").strip() or None
    try:
        with get_session() as session:
            result = session.execute(
                text(
                    "UPDATE public.profiles SET full_name = :n, updated_at = NOW() "
                    "WHERE id = CAST(:id AS UUID)"
                ),
                {"n": cleaned, "id": u["id"]},
            )
            session.commit()
        if result.rowcount == 0:
            return False, "Profile row not found — try signing out and back in."
    except Exception as e:
        return False, f"Update failed: {e}"
    refresh_profile()
    return True, "Display name updated."


def hydrate_from_cookie() -> bool:
    """Cold-start path: if no cached session but a refresh-token cookie exists,
    exchange it for a fresh GoTrue session and hydrate. Returns True if a user
    is now signed in."""
    if not is_auth_enabled():
        return False
    if st.session_state.get(USER_KEY) is not None:
        return True
    # Don't let a leftover cookie undo an explicit sign-out within the same tab.
    if st.session_state.get(SIGNED_OUT_KEY):
        return False
    rt = cookies.read_refresh_token()
    if not rt:
        return False
    try:
        client = sb.get_anon_client()
        res = client.auth.refresh_session(rt)
    except Exception as exc:
        logger.info("refresh_session from cookie failed: %s", exc)
        cookies.clear_refresh_token()
        return False
    if not res or not res.session or not res.user:
        cookies.clear_refresh_token()
        return False
    _establish_session(res.session, res.user)
    return True


def sign_out() -> None:
    """Clear cached profile, drop the GoTrue session, and remove the cookie."""
    if is_auth_enabled():
        try:
            sb.get_anon_client().auth.sign_out()
        except Exception as exc:
            logger.info("GoTrue sign_out failed (ignored): %s", exc)
    try:
        cookies.clear_refresh_token()
    except Exception as exc:
        logger.info("cookie clear on sign_out failed (ignored): %s", exc)

    for k in (
        USER_KEY,
        ROLE_KEY,
        NAME_KEY,
        ACTIVE_KEY,
        AUTH_UID_KEY,
        PROFILE_FETCHED_AT_KEY,
        SESSION_RT_KEY,
        COOKIE_WRITTEN_KEY,
    ):
        st.session_state.pop(k, None)
    for k in list(st.session_state.keys()):
        if (
            k.startswith("settings_input_")
            or k.startswith("_header_edit_name_input_")
            or k.startswith("_header_save_name_")
            or k.startswith("_header_cancel_edit_")
            or k.startswith("_header_pw_")
        ):
            st.session_state.pop(k, None)
    # Remember the explicit sign-out so a still-present cookie can't re-hydrate
    # this tab before a hard refresh (which drops session_state) clears it.
    st.session_state[SIGNED_OUT_KEY] = True
