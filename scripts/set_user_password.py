"""Set (or reset) a Supabase Auth user's password from the command line.

Why this exists: the in-app admin reset needs an admin who can already sign in.
For the very first admin — or any account that pre-dates email/password sign-in
(e.g. a Google-only account with no password) — there's a chicken-and-egg
problem. This script breaks it using the service-role key.

Usage (from the repo root):

    uv run python scripts/set_user_password.py you@example.com
    uv run python scripts/set_user_password.py you@example.com 'your-new-password'

If the password is omitted you'll be prompted for it (input is hidden). If the
GoTrue user doesn't exist yet it's created (email pre-confirmed); if it exists,
its password is updated. The profile row / activation is handled by the app on
first sign-in.

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.
"""

from __future__ import annotations

import getpass
import os
import sys

from dotenv import load_dotenv


def _find_user_id(service, email: str) -> str | None:
    target = email.strip().lower()
    try:
        users = service.auth.admin.list_users(page=1, per_page=1000)
    except TypeError:
        users = service.auth.admin.list_users()
    user_iter = getattr(users, "users", users) or []
    for u in user_iter:
        if (getattr(u, "email", "") or "").strip().lower() == target:
            return getattr(u, "id", None)
    return None


def main(argv: list[str]) -> int:
    load_dotenv(override=False)

    if len(argv) < 2:
        print("Usage: set_user_password.py <email> [password]", file=sys.stderr)
        return 2
    email = argv[1].strip().lower()
    password = argv[2] if len(argv) > 2 else getpass.getpass("New password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 2

    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    service_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not service_key:
        print(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env.",
            file=sys.stderr,
        )
        return 1

    from supabase import create_client

    service = create_client(url, service_key)

    uid = _find_user_id(service, email)
    if uid:
        service.auth.admin.update_user_by_id(
            uid, {"password": password, "email_confirm": True}
        )
        print(f"Updated password for existing user {email} ({uid}).")
    else:
        created = service.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True}
        )
        new_uid = getattr(getattr(created, "user", None), "id", None)
        print(f"Created user {email} ({new_uid}) with the given password.")

    print("Done. Sign in at the app with this email + password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
