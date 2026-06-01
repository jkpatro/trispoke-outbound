"""Users management — admin only.

Three sections:
  (1) Active users        — profiles with is_active=true.
  (2) Pending / inactive  — profiles with is_active=false (signed up, awaiting approval).
  (3) Invitations         — emails pre-approved but not yet signed in.
"""

from __future__ import annotations

import streamlit as st

from trispoke.auth import current_user, current_user_email, is_admin
from trispoke.auth.user_admin import (
    admin_reset_password,
    invite_user,
    list_invitations,
    list_profiles,
    revoke_invitation,
    set_profile_active,
    set_profile_role,
)


def render() -> None:
    st.title("Users")
    if not is_admin():
        st.error("Only admins can view this page.")
        return

    st.caption(
        "Teammates sign in with their email + password. New accounts land "
        "inactive until you activate them below. Pre-invite an email to skip "
        "approval and assign a role up front. You can change roles, reset "
        "passwords, or deactivate accounts at any time."
    )

    _render_invite_form()
    st.divider()
    _render_profiles_table()
    st.divider()
    _render_invitations_table()


# ---------------------------------------------------------------------------


def _render_invite_form() -> None:
    st.subheader("Invite a user")
    with st.form("user_invite_form", clear_on_submit=True):
        cols = st.columns([4, 2, 1])
        with cols[0]:
            email = st.text_input(
                "Email",
                placeholder="newperson@gmail.com",
                label_visibility="collapsed",
            )
        with cols[1]:
            role = st.selectbox(
                "Role",
                ["user", "admin"],
                index=0,
                label_visibility="collapsed",
            )
        with cols[2]:
            submitted = st.form_submit_button(
                "Invite", type="primary", use_container_width=True
            )
        if submitted:
            uid = (current_user() or {}).get("id")
            ok, msg = invite_user(email, role, invited_by=uid)
            (st.success if ok else st.error)(msg)


def _render_profiles_table() -> None:
    profiles = list_profiles()
    if not profiles:
        st.info("No users yet.")
        return

    active = [p for p in profiles if p["is_active"]]
    inactive = [p for p in profiles if not p["is_active"]]

    st.subheader(f"Active users ({len(active)})")
    for p in active:
        _render_profile_row(p)

    if inactive:
        st.subheader(f"Pending / inactive ({len(inactive)})")
        for p in inactive:
            _render_profile_row(p)


def _render_profile_row(p: dict) -> None:
    """One profile row with name + email + role select + activate toggle."""
    self_email = (current_user_email() or "").lower()
    is_self = (p["email"] or "").lower() == self_email

    with st.container(border=True):
        cols = st.columns([3, 2, 2, 1])
        with cols[0]:
            label = p["full_name"] or p["email"].split("@")[0]
            st.markdown(f"**{label}**")
            st.caption(p["email"])
        with cols[1]:
            new_role = st.selectbox(
                "Role",
                ["user", "admin"],
                index=0 if p["role"] != "admin" else 1,
                key=f"role_{p['id']}",
                label_visibility="collapsed",
                disabled=is_self,  # don't let an admin demote themselves
                help="Cannot change your own role — ask another admin." if is_self else None,
            )
            if not is_self and new_role != p["role"]:
                if st.button(
                    f"Set {new_role}",
                    key=f"setrole_{p['id']}",
                    use_container_width=True,
                ):
                    ok, msg = set_profile_role(p["id"], new_role)
                    (st.success if ok else st.error)(msg)
                    st.rerun()
        with cols[2]:
            if is_self:
                st.markdown("✅ **You**")
            elif p["is_active"]:
                if st.button(
                    "Deactivate",
                    key=f"deact_{p['id']}",
                    use_container_width=True,
                ):
                    ok, msg = set_profile_active(p["id"], False)
                    (st.success if ok else st.error)(msg)
                    st.rerun()
            else:
                if st.button(
                    "Activate",
                    key=f"act_{p['id']}",
                    type="primary",
                    use_container_width=True,
                ):
                    ok, msg = set_profile_active(p["id"], True)
                    (st.success if ok else st.error)(msg)
                    st.rerun()
        with cols[3]:
            badge = "🛡 Admin" if p["role"] == "admin" else "👤 User"
            st.markdown(badge)

        _render_reset_password(p, is_self)


def _render_reset_password(p: dict, is_self: bool) -> None:
    """Admin password reset for one user (works for self too)."""
    label = "Reset my password" if is_self else "Reset password"
    with st.expander(label):
        st.caption(
            "Sets a new password immediately. Share it securely; the user can "
            "change it from their profile after signing in."
        )
        with st.form(f"pwreset_form_{p['id']}", clear_on_submit=True):
            new_pw = st.text_input(
                "New password", type="password", help="At least 8 characters."
            )
            confirm_pw = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button(
                "Set new password", type="primary", use_container_width=True
            )
        if submitted:
            if new_pw != confirm_pw:
                st.error("Passwords don't match.")
                return
            ok, msg = admin_reset_password(p["id"], new_pw)
            (st.success if ok else st.error)(msg)


def _render_invitations_table() -> None:
    invites = list_invitations()
    st.subheader(f"Pending invitations ({len(invites)})")
    if not invites:
        st.info("No pending invitations. Use the form above to invite a teammate.")
        return

    for inv in invites:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 1])
            cols[0].markdown(f"**{inv['email']}**")
            cols[1].markdown(
                "🛡 Admin" if inv["role"] == "admin" else "👤 User"
            )
            cols[2].caption(
                f"Invited by {inv['invited_by_email'] or '—'} on "
                f"{inv['invited_at'].strftime('%Y-%m-%d %H:%M') if inv['invited_at'] else '—'}"
            )
            with cols[3]:
                if st.button(
                    "Revoke",
                    key=f"rev_{inv['email']}",
                    use_container_width=True,
                ):
                    ok, msg = revoke_invitation(inv["email"])
                    (st.success if ok else st.error)(msg)
                    st.rerun()
