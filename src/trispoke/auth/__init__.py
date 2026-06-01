"""Supabase Auth (GoTrue) integration — email + password.

Two-role model: 'admin' and 'user'. Sign-in/sign-up run against Supabase Auth;
each GoTrue user is mirrored by a public.profiles row (keyed by email) carrying
the app-level role / is_active / display name. A signed-in user must be
is_active to use the app (admin approves from the Users page). Forgotten
passwords are reset by an admin — there is no email flow.
"""

from trispoke.auth.session import (
    change_own_password,
    current_user,
    current_user_email,
    current_user_name,
    current_user_role,
    hydrate_from_cookie,
    is_account_active,
    is_admin,
    is_authenticated,
    is_auth_enabled,
    profile_stale,
    refresh_profile,
    sign_in,
    sign_out,
    sign_up,
    update_full_name,
)
from trispoke.auth.login import render_login_gate

__all__ = [
    "change_own_password",
    "current_user",
    "current_user_email",
    "current_user_name",
    "current_user_role",
    "hydrate_from_cookie",
    "is_account_active",
    "is_admin",
    "is_authenticated",
    "is_auth_enabled",
    "profile_stale",
    "refresh_profile",
    "render_login_gate",
    "sign_in",
    "sign_out",
    "sign_up",
    "update_full_name",
]
