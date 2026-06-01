"""Login gate — Supabase Auth (GoTrue) email + password.

Sign-in:  session.sign_in(email, password)
Sign-up:  session.sign_up(email, password)   ← open self-registration
Identity: cached profile in st.session_state (see session.py)

A signed-in user whose profile.is_active is false sees a "pending admin
approval" screen until an admin activates them from the Users page. Forgotten
passwords are handled by an admin reset (Users page) — there is no email flow.
"""

from __future__ import annotations

import streamlit as st

from trispoke.auth.session import (
    clear_cookie_if_signed_out,
    hydrate_from_cookie,
    is_account_active,
    is_auth_enabled,
    is_authenticated,
    persist_session_cookie,
    profile_stale,
    refresh_profile,
    sign_in,
    sign_out,
    sign_up,
)
from trispoke.ui.components.footer import logo_img_html, render_footer


def render_login_gate() -> None:
    """Block page render until the user is signed in. No-op when auth is disabled."""
    if not is_auth_enabled():
        return

    # Cold start (hard refresh): try to restore the session from the cookie.
    if not is_authenticated():
        hydrate_from_cookie()

    if is_authenticated():
        # Persist the refresh token on this normal render (the set commits to the
        # browser here, unlike during the sign-in run that immediately reruns).
        persist_session_cookie()
        if profile_stale():
            refresh_profile()
        if not is_account_active():
            _render_inactive_screen()
            st.stop()
        return

    clear_cookie_if_signed_out()
    _render_login_screen()
    st.stop()


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------


def _inject_auth_screen_css() -> None:
    st.markdown(
        """
        <style>
          [data-testid="stSidebar"] { display: none !important; }
          [data-testid="collapsedControl"] { display: none !important; }
          [data-testid="stHeader"] { background: transparent; }
          .block-container { padding-top: 3.5rem !important; }

          .stApp {
              background:
                radial-gradient(circle at 20% 20%, rgba(99,102,241,.18), transparent 55%),
                radial-gradient(circle at 80% 80%, rgba(168,85,247,.18), transparent 55%);
          }
          .ts-login-brand {
              display: flex; align-items: center; gap: 12px;
              justify-content: center; margin-bottom: 1.25rem;
          }
          .ts-login-logo {
              width: 44px; height: 44px; border-radius: 12px;
              display: flex; align-items: center; justify-content: center;
              color: #fff; font-weight: 700; font-size: 16px;
              background: linear-gradient(135deg,#6366f1,#a855f7);
              box-shadow: 0 6px 18px rgba(99,102,241,.45);
          }
          .ts-login-brand-name { font-weight: 700; font-size: 20px; letter-spacing: -.01em; }
          .ts-login-brand-tag  { font-size: 12px; opacity: .65; }
          .ts-login-h1 { text-align:center; margin: .25rem 0 .35rem 0; font-size: 22px; font-weight: 600; }
          .ts-login-sub { text-align:center; font-size: 14px; opacity: .75; margin-bottom: 1.25rem; line-height: 1.45; }
          .ts-login-footnote { margin-top: 1.1rem; font-size: 12px; opacity: .6; text-align: center; }

          .ts-pending-card {
              padding: 2rem; border-radius: 16px;
              border: 1px solid rgba(245,158,11,.45);
              background: rgba(245,158,11,.08);
          }
          .ts-pending-title { font-size: 22px; font-weight: 600; margin: 0 0 .5rem 0; }
          .ts-pending-body { opacity: .85; line-height: 1.55; margin-bottom: 1.25rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Login / signup
# ---------------------------------------------------------------------------


def _render_login_screen() -> None:
    _inject_auth_screen_css()
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(
            f"""
            <div style="display:flex;justify-content:center;margin-bottom:1rem">
              {logo_img_html(50)}
            </div>
            <div class="ts-login-h1">Welcome back</div>
            <div class="ts-login-sub">
              Sign in to <strong>Trispoke Outbound</strong> with your email and password.
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            tab_signin, tab_signup = st.tabs(["Sign in", "Create account"])
            with tab_signin:
                _render_signin_form()
            with tab_signup:
                _render_signup_form()

        st.markdown(
            """
            <div class="ts-login-footnote">
              Forgot your password? Ask a workspace admin to reset it.
            </div>
            """,
            unsafe_allow_html=True,
        )

    render_footer()


def _render_signin_form() -> None:
    with st.form("auth_signin_form", clear_on_submit=False):
        email = st.text_input("Email", placeholder="you@company.com", key="auth_signin_email")
        password = st.text_input(
            "Password", type="password", key="auth_signin_pw"
        )
        submitted = st.form_submit_button(
            "Sign in", type="primary", use_container_width=True
        )
    if submitted:
        ok, msg = sign_in(email, password)
        if ok:
            st.rerun()
        else:
            st.error(msg)


def _render_signup_form() -> None:
    st.caption(
        "New here? Create an account, then a workspace admin activates it before "
        "you can use the app."
    )
    with st.form("auth_signup_form", clear_on_submit=False):
        email = st.text_input("Email", placeholder="you@company.com", key="auth_signup_email")
        password = st.text_input(
            "Password", type="password", key="auth_signup_pw",
            help="At least 8 characters.",
        )
        confirm = st.text_input(
            "Confirm password", type="password", key="auth_signup_pw2"
        )
        submitted = st.form_submit_button(
            "Create account", type="primary", use_container_width=True
        )
    if submitted:
        if password != confirm:
            st.error("Passwords don't match.")
            return
        ok, msg = sign_up(email, password)
        if ok:
            st.success(msg + " It's now pending admin approval.")
            st.rerun()
        else:
            st.error(msg)


def _render_inactive_screen() -> None:
    """Shown when a signed-in user's profile.is_active = false."""
    from trispoke.auth.session import current_user_email

    _inject_auth_screen_css()
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown(
            f"""
            <div class="ts-pending-card">
              <div class="ts-pending-title">⏳ Pending admin approval</div>
              <div class="ts-pending-body">
                You're signed in as <strong>{current_user_email() or ""}</strong>, but
                your account isn't activated yet. Ask a workspace admin to enable
                your account from the Users page.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Sign out", type="primary", use_container_width=True):
            sign_out()
            st.rerun()
