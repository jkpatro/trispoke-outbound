"""Admin operations on profiles + invitations.

Profile / invitation reads + writes use the direct Postgres session
(DATABASE_URL), bypassing PostgREST/RLS. Password resets go through the
Supabase Auth admin API (service-role client). UI-level gating on is_admin()
is what limits all of this to admins.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from trispoke.auth.client import get_service_client, has_service_role
from trispoke.db.session import get_session

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def list_profiles() -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT id::text, email, full_name, role::text, is_active,
                       created_at, updated_at
                FROM public.profiles
                ORDER BY created_at DESC
                """
            )
        ).fetchall()
    return [
        {
            "id": r[0],
            "email": r[1],
            "full_name": r[2],
            "role": r[3],
            "is_active": bool(r[4]),
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]


def set_profile_role(user_id: str, role: str) -> tuple[bool, str]:
    if role not in ("admin", "user"):
        return False, "Role must be 'admin' or 'user'."
    with get_session() as session:
        result = session.execute(
            text(
                """
                UPDATE public.profiles
                SET role = CAST(:r AS public.user_role), updated_at = NOW()
                WHERE id = CAST(:id AS UUID)
                  AND (
                    CAST(:r AS public.user_role) <> 'user'::public.user_role
                    OR EXISTS (
                      SELECT 1 FROM public.profiles
                      WHERE role = 'admin' AND is_active = TRUE
                        AND id <> CAST(:id AS UUID)
                    )
                  )
                """
            ),
            {"r": role, "id": user_id},
        )
        session.commit()
        if result.rowcount > 0:
            return True, f"Role set to {role}."
        # Either the row doesn't exist, or the last-admin guard tripped.
        if role == "user":
            # Check whether the target profile actually exists to differentiate
            # the two failure modes.
            exists = session.execute(
                text(
                    "SELECT 1 FROM public.profiles WHERE id = CAST(:id AS UUID)"
                ),
                {"id": user_id},
            ).fetchone()
            if exists:
                return False, (
                    "Cannot demote the last active admin. Promote another "
                    "user to admin first."
                )
        return False, "No matching profile."


def set_profile_active(user_id: str, is_active: bool) -> tuple[bool, str]:
    with get_session() as session:
        if not is_active:
            target = session.execute(
                text(
                    "SELECT role::text FROM public.profiles "
                    "WHERE id = CAST(:id AS UUID)"
                ),
                {"id": user_id},
            ).fetchone()
            if target and target[0] == "admin":
                other_admin_count = session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM public.profiles
                        WHERE role = 'admin' AND is_active = TRUE
                          AND id <> CAST(:id AS UUID)
                        """
                    ),
                    {"id": user_id},
                ).scalar()
                if not other_admin_count:
                    return False, "Cannot deactivate the last active admin."
        result = session.execute(
            text(
                "UPDATE public.profiles SET is_active = :a, updated_at = NOW() "
                "WHERE id = CAST(:id AS UUID)"
            ),
            {"a": is_active, "id": user_id},
        )
        session.commit()
    if result.rowcount == 0:
        return False, "No matching profile."
    return True, "Activated." if is_active else "Deactivated."


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def list_invitations() -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT i.email, i.role::text, i.invited_at,
                       p.email AS invited_by_email
                FROM public.user_invitations i
                LEFT JOIN public.profiles p ON p.id = i.invited_by
                ORDER BY i.invited_at DESC
                """
            )
        ).fetchall()
    return [
        {
            "email": r[0],
            "role": r[1],
            "invited_at": r[2],
            "invited_by_email": r[3],
        }
        for r in rows
    ]


def invite_user(
    email: str, role: str, invited_by: Optional[str] = None
) -> tuple[bool, str]:
    """Add or refresh an entry in user_invitations. If the email already has a
    profile (already signed up), return a clear error instead of silently
    creating an orphan invitation."""
    email_clean = (email or "").strip().lower()
    if not email_clean or "@" not in email_clean:
        return False, "Enter a valid email address."
    if role not in ("admin", "user"):
        return False, "Role must be 'admin' or 'user'."

    with get_session() as session:
        existing = session.execute(
            text("SELECT 1 FROM public.profiles WHERE LOWER(email) = :e"),
            {"e": email_clean},
        ).fetchone()
        if existing:
            return False, (
                f"{email_clean} already has an account. Adjust the role/status "
                f"on the Users list instead of inviting again."
            )
        try:
            session.execute(
                text(
                    """
                    INSERT INTO public.user_invitations (email, role, invited_by, invited_at)
                    VALUES (:e, CAST(:r AS public.user_role), CAST(:by AS UUID), NOW())
                    ON CONFLICT (email) DO UPDATE
                      SET role       = EXCLUDED.role,
                          invited_by = EXCLUDED.invited_by,
                          invited_at = NOW()
                    """
                ),
                {"e": email_clean, "r": role, "by": invited_by},
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            return False, (
                f"{email_clean} already has an account. Adjust the role/status "
                f"on the Users list instead of inviting again."
            )
    return True, f"{email_clean} invited as {role}."


def revoke_invitation(email: str) -> tuple[bool, str]:
    email_clean = (email or "").strip().lower()
    with get_session() as session:
        result = session.execute(
            text("DELETE FROM public.user_invitations WHERE LOWER(email) = :e"),
            {"e": email_clean},
        )
        session.commit()
    return (result.rowcount > 0, "Invitation revoked.")


# ---------------------------------------------------------------------------
# Password reset (Supabase Auth admin API)
# ---------------------------------------------------------------------------


def _profile_email_and_auth_uid(user_id: str) -> tuple[Optional[str], Optional[str]]:
    with get_session() as session:
        row = session.execute(
            text(
                "SELECT email, auth_uid::text FROM public.profiles "
                "WHERE id = CAST(:id AS UUID)"
            ),
            {"id": user_id},
        ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def _lookup_auth_uid_by_email(service, email: str) -> Optional[str]:
    """Fall back to the GoTrue user list when a profile has no auth_uid yet."""
    target = (email or "").strip().lower()
    try:
        try:
            users = service.auth.admin.list_users(page=1, per_page=1000)
        except TypeError:
            users = service.auth.admin.list_users()
    except Exception as exc:
        logger.warning("list_users failed during reset: %s", exc)
        return None
    # Depending on the client version this is a list or an object with .users.
    user_iter = getattr(users, "users", users) or []
    for u in user_iter:
        if (getattr(u, "email", "") or "").strip().lower() == target:
            return getattr(u, "id", None)
    return None


def _backfill_auth_uid(user_id: str, auth_uid: str) -> None:
    try:
        with get_session() as session:
            session.execute(
                text(
                    "UPDATE public.profiles SET auth_uid = CAST(:uid AS UUID) "
                    "WHERE id = CAST(:id AS UUID) AND auth_uid IS NULL"
                ),
                {"uid": auth_uid, "id": user_id},
            )
            session.commit()
    except Exception as exc:
        logger.warning("auth_uid backfill failed: %s", exc)


def admin_reset_password(user_id: str, new_password: str) -> tuple[bool, str]:
    """Set a new password for another user via the service-role admin API.

    `user_id` is the profiles.id. The GoTrue user is resolved from the profile's
    auth_uid (falling back to an email lookup if the profile predates the link).
    """
    if not has_service_role():
        return False, (
            "Password reset is unavailable — set SUPABASE_SERVICE_ROLE_KEY in the "
            "server's .env."
        )
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    email, auth_uid = _profile_email_and_auth_uid(user_id)
    if email is None:
        return False, "No matching profile."

    service = get_service_client()
    if service is None:
        return False, "Service-role client unavailable."

    if not auth_uid:
        auth_uid = _lookup_auth_uid_by_email(service, email)
        if auth_uid:
            _backfill_auth_uid(user_id, auth_uid)
    if not auth_uid:
        return False, (
            "This user has no Supabase Auth account yet (they haven't signed up). "
            "Nothing to reset."
        )

    try:
        service.auth.admin.update_user_by_id(auth_uid, {"password": new_password})
    except Exception as exc:
        return False, f"Reset failed: {exc}"
    return True, f"Password reset for {email}."
