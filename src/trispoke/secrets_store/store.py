"""DB-backed encrypted secret store.

Uses the existing SQLAlchemy session — same connection as the rest of the
app. The DATABASE_URL points at Supabase Postgres, so writes bypass RLS
(service-role / direct Postgres conn). For BYO-secret use from the UI we
still gate access at the Streamlit layer via auth.is_admin().

Reads are cached for 60s per process to avoid hitting the DB on every
generation call. set/delete invalidate the cache.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from sqlalchemy import text

from trispoke.db.session import get_session
from trispoke.secrets_store.crypto import decrypt, encrypt, is_ready

logger = logging.getLogger(__name__)

# In-process cache: key_name -> (plaintext, fetched_at_unix)
_CACHE: dict[str, tuple[Optional[str], float]] = {}
_CACHE_TTL_SECONDS = 60


def is_encryption_ready() -> bool:
    return is_ready()


def _invalidate(key_name: str) -> None:
    _CACHE.pop(key_name, None)


def get_secret(key_name: str) -> Optional[str]:
    """Return decrypted plaintext or None if not stored / undecryptable."""
    key_name = key_name.strip().upper()
    now = time.time()
    hit = _CACHE.get(key_name)
    if hit is not None and now - hit[1] < _CACHE_TTL_SECONDS:
        return hit[0]
    try:
        with get_session() as session:
            row = session.execute(
                text(
                    "SELECT encrypted_value FROM public.app_secrets WHERE key_name = :k"
                ),
                {"k": key_name},
            ).fetchone()
    except Exception as exc:
        # DB might be down or table not yet created. Fall back to env via secret() below.
        # Do NOT poison the cache — a transient error shouldn't mask a real value for 60s.
        logger.warning("get_secret(%s) failed; falling back to env: %s", key_name, exc)
        return None
    plaintext = decrypt(row[0]) if row else None
    _CACHE[key_name] = (plaintext, now)
    return plaintext


def secret(key_name: str, env_fallback: Optional[str] = None) -> Optional[str]:
    """DB-first resolver: app_secrets → env var of the same name → passed fallback.

    Use this in API client call-sites so the same code keeps working whether the
    secret lives in the DB (preferred) or in .env (legacy/dev fallback)."""
    val = get_secret(key_name)
    if val:
        return val
    val = os.environ.get(key_name.strip().upper())
    if val:
        return val
    return env_fallback


def set_secret(key_name: str, plaintext: str, user_id: Optional[str] = None) -> None:
    """Encrypt + upsert. Raises if TRISPOKE_SECRET_KEY is missing."""
    key_name = key_name.strip().upper()
    if not plaintext:
        raise ValueError("Cannot store an empty secret. Use delete_secret() instead.")
    ciphertext = encrypt(plaintext)
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO public.app_secrets (key_name, encrypted_value, last_set_by, updated_at)
                VALUES (:k, :v, CAST(:u AS UUID), NOW())
                ON CONFLICT (key_name) DO UPDATE
                  SET encrypted_value = EXCLUDED.encrypted_value,
                      last_set_by    = EXCLUDED.last_set_by,
                      updated_at     = NOW()
                """
            ),
            {"k": key_name, "v": ciphertext, "u": user_id},
        )
        session.commit()
    _invalidate(key_name)


def delete_secret(key_name: str) -> None:
    key_name = key_name.strip().upper()
    with get_session() as session:
        session.execute(
            text("DELETE FROM public.app_secrets WHERE key_name = :k"),
            {"k": key_name},
        )
        session.commit()
    _invalidate(key_name)


def has_secret(key_name: str) -> bool:
    """Cheap existence check — does this key live in app_secrets at all?

    Bypasses the 60s read cache so the Settings UI reflects writes immediately.
    """
    key_name = key_name.strip().upper()
    try:
        with get_session() as session:
            row = session.execute(
                text("SELECT 1 FROM public.app_secrets WHERE key_name = :k LIMIT 1"),
                {"k": key_name},
            ).fetchone()
    except Exception as exc:
        logger.warning("has_secret(%s) failed: %s", key_name, exc)
        return False
    return row is not None


def decrypt_health() -> list[dict]:
    """Probe every stored secret to see if the current TRISPOKE_SECRET_KEY can decrypt it.

    Returns a list of {key_name, ok, updated_at}. Useful for the Settings page to
    surface silent Fernet-key-rotation breakage (rows present but undecryptable).
    """
    try:
        with get_session() as session:
            rows = session.execute(
                text(
                    "SELECT key_name, encrypted_value, updated_at "
                    "FROM public.app_secrets ORDER BY key_name"
                )
            ).fetchall()
    except Exception as exc:
        logger.warning("decrypt_health() failed to read app_secrets: %s", exc)
        return []
    out: list[dict] = []
    for r in rows:
        key_name, encrypted_value, updated_at = r[0], r[1], r[2]
        plaintext = decrypt(encrypted_value)
        out.append(
            {
                "key_name": key_name,
                "ok": plaintext is not None,
                "updated_at": updated_at,
            }
        )
    return out


def list_secrets() -> list[dict]:
    """Return metadata for every stored secret (NEVER the plaintext)."""
    try:
        with get_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT s.key_name,
                           s.updated_at,
                           p.email AS last_set_by_email
                    FROM public.app_secrets s
                    LEFT JOIN public.profiles p ON p.id = s.last_set_by
                    ORDER BY s.key_name
                    """
                )
            ).fetchall()
    except Exception:
        return []
    return [
        {
            "key_name": r[0],
            "updated_at": r[1],
            "last_set_by_email": r[2],
        }
        for r in rows
    ]
