"""Top header bar — brand + floating nav + a single avatar account menu.

Layout: [ logo ] [ ☰ Menu ] ···· [ 🟣 avatar ]. Clicking the round avatar opens a
dropdown (popover) that holds the whole account section — identity, inline name
edit, change password, and sign out. No modal: edits happen inline inside the
dropdown and save in place (the script reruns, the popover stays open).
"""

from __future__ import annotations

from typing import Callable, Optional

import streamlit as st

from trispoke.auth import (
    current_user_email,
    current_user_name,
    current_user_role,
    is_auth_enabled,
    sign_out,
)
from trispoke.ui.components.footer import logo_img_html
from trispoke.ui.utils.state import set_page


def _initials(name: str | None, email: str | None) -> str:
    if name:
        parts = [p for p in name.strip().split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        if parts:
            return parts[0][:2].upper()
    if email:
        local = email.split("@")[0]
        return local[:2].upper()
    return "??"


def _role_chip_html(role: str) -> str:
    if role == "admin":
        return '<span class="ts-hb-chip ts-hb-chip-admin">Admin</span>'
    return '<span class="ts-hb-chip ts-hb-chip-user">User</span>'


def _inject_css() -> None:
    # Inject on every render: Streamlit drops the <style> element on the next
    # rerun, so a once-per-session guard would leave the bar unstyled after the
    # first navigation.
    st.markdown(
        """
        <style>
          /* The bar = the horizontal block immediately following our anchor div. */
          .ts-hb-anchor + div [data-testid="stHorizontalBlock"] {
              display: flex;
              align-items: center;
              gap: .5rem;
              padding: 6px 14px;
              margin: -0.6rem -1rem 0.8rem -1rem;
              background: linear-gradient(180deg, rgba(120,120,130,.10), rgba(120,120,130,.04));
              border-bottom: 1px solid rgba(150,150,160,.16);
          }
          .ts-hb-logo { display: flex; align-items: center; }
          .ts-hb-chip {
              display: inline-block; padding: 1px 8px; border-radius: 999px;
              font-size: 11px; font-weight: 600; margin-left: 6px;
              vertical-align: middle; border: 1px solid;
          }
          .ts-hb-chip-admin { background: rgba(16,185,129,.12); color: #10b981; border-color: rgba(16,185,129,.45); }
          .ts-hb-chip-user  { background: rgba(96,165,250,.12); color: #60a5fa; border-color: rgba(96,165,250,.45); }

          /* ☰ Menu trigger — filled navy pill. */
          .ts-hb-anchor + div [data-testid="stPopover"] [data-testid="stPopoverButton"] {
              background: linear-gradient(135deg,#1f3a6e,#2b5fa0);
              color: #fff; font-weight: 600; border-color: transparent;
              box-shadow: 0 4px 12px rgba(31,58,110,.28);
          }
          .ts-hb-anchor + div [data-testid="stPopover"] [data-testid="stPopoverButton"]:hover { filter: brightness(1.08); }

          /* Account trigger — a round avatar. Overrides the navy pill above.
             Grid + place-items centers the bare initials on BOTH axes regardless
             of Streamlit's nested label wrappers (which defeated flex/align). */
          .st-key-acct_pop [data-testid="stPopoverButton"] {
              width: 42px !important; min-width: 42px !important;
              height: 42px !important; min-height: 42px !important;
              padding: 0 !important; margin: 0 !important; gap: 0 !important;
              border-radius: 50% !important;
              display: grid !important; place-items: center !important;
              line-height: 1 !important; overflow: hidden !important;
              background: linear-gradient(135deg,#6366f1,#a855f7) !important;
              color: #fff !important; font-weight: 700 !important;
              font-size: 13px !important; letter-spacing: .02em;
              border: 2px solid rgba(255,255,255,.65) !important;
              box-shadow: 0 4px 12px rgba(99,102,241,.40) !important;
              transition: filter .15s ease, transform .12s ease;
          }
          .st-key-acct_pop [data-testid="stPopoverButton"]:hover {
              filter: brightness(1.08); transform: translateY(-1px);
          }
          /* Hide the expand chevron (more specific, so it wins over the * rule). */
          .st-key-acct_pop [data-testid="stPopoverButton"] [data-testid="stIconMaterial"],
          .st-key-acct_pop [data-testid="stPopoverButton"] svg { display: none !important; }
          /* Collapse the label wrappers to inline so the grid centers "JP"
             (was wrapping to two lines, then sitting low). */
          .st-key-acct_pop [data-testid="stPopoverButton"] * {
              margin: 0 !important; padding: 0 !important;
              line-height: 1 !important; white-space: nowrap !important;
              display: inline !important;
          }

          /* Dropdown panel: fade in + comfortable width. IMPORTANT: opacity only.
             baseweb positions the popover via `transform`, so animating transform
             here hijacks its position for the animation's duration and makes it
             visibly jump into place (and flash a horizontal scrollbar). Fading
             sidesteps that — baseweb keeps it anchored under the avatar. */
          [data-testid="stPopoverBody"] {
              min-width: 290px;
              animation: tsAcctIn .15s ease;
          }
          @keyframes tsAcctIn {
              from { opacity: 0; }
              to   { opacity: 1; }
          }

          /* Account panel header (avatar + name + email). */
          .ts-acct-head { display: flex; align-items: center; gap: 12px; margin: .1rem 0 .35rem; }
          .ts-acct-avatar {
              width: 46px; height: 46px; border-radius: 50%; flex-shrink: 0;
              display: flex; align-items: center; justify-content: center;
              background: linear-gradient(135deg,#6366f1,#a855f7);
              color: #fff; font-weight: 700; font-size: 16px;
              box-shadow: 0 4px 12px rgba(99,102,241,.35);
          }
          .ts-acct-name { font-weight: 600; font-size: 15px; line-height: 1.2; }
          .ts-acct-email { font-size: 12px; opacity: .65; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(nav_menu: Optional[Callable[[], None]] = None) -> None:
    """Render the top bar: logo, floating ☰ Menu, and a round avatar that opens
    the account dropdown. `nav_menu` (passed in to avoid a circular import) is the
    callable that renders the navigation controls. No-op when auth is disabled.
    """
    if not is_auth_enabled():
        return
    _inject_css()

    name = current_user_name()
    email = current_user_email() or "(unknown)"
    initials = _initials(name, email)

    # Anchor div — the next horizontal block becomes the styled bar.
    st.markdown("<div class='ts-hb-anchor'></div>", unsafe_allow_html=True)
    with st.container():
        # logo | menu | flexible spacer | avatar
        cols = st.columns([0.6, 1.1, 6.0, 0.8], gap="small")
        with cols[0]:
            st.markdown(
                f"<div class='ts-hb-logo'>{logo_img_html(26)}</div>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            if nav_menu is not None:
                with st.popover("☰  Menu", use_container_width=True):
                    nav_menu()
        # cols[2]: intentional flexible spacer pinning the avatar to the right.
        with cols[3]:
            with st.popover(initials, key="acct_pop"):
                _render_account_panel()


def _render_account_panel() -> None:
    """Account dropdown: identity + a launcher into the global Settings → Profile
    tab (change password & profile edits live there now) + sign out."""
    name = current_user_name()
    email = current_user_email() or "(unknown)"
    role = current_user_role()
    initials = _initials(name, email)
    display_name = name or email.split("@")[0]

    st.markdown(
        f"""
        <div class="ts-acct-head">
          <div class="ts-acct-avatar">{initials}</div>
          <div>
            <div class="ts-acct-name">{display_name} {_role_chip_html(role)}</div>
            <div class="ts-acct-email">{email}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "✏️  Edit profile",
        key="acct_edit_profile",
        type="primary",
        use_container_width=True,
    ):
        # Lands on Settings → Profile (the first tab), where change password and
        # all profile edits live.
        set_page("settings")
        st.rerun()
    st.caption("Change password & profile settings")

    st.divider()
    if st.button("⏻  Sign out", key="acct_sign_out", use_container_width=True):
        sign_out()
        st.rerun()
