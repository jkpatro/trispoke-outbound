"""DB-backed plaintext config store + typed resolvers.

Reads/writes public.app_config over the same SQLAlchemy session as the rest of
the app (DATABASE_URL → Supabase Postgres, bypasses RLS). Values are cached for
60s per process so the hot paths (sender loop, pollers) don't hit the DB every
iteration; set/delete invalidate the cache.

Everything degrades gracefully: if the table doesn't exist yet (migration not
run) or the DB is unreachable, the typed getters fall back to the env var of the
same name, then to the caller's default.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from sqlalchemy import text

from trispoke.db.session import get_session

logger = logging.getLogger(__name__)

# key -> (value, fetched_at_unix)
_CACHE: dict[str, tuple[Optional[str], float]] = {}
_CACHE_TTL_SECONDS = 60


def _invalidate(key: str) -> None:
    _CACHE.pop(key, None)


def get_config(key: str) -> Optional[str]:
    """Raw stored value for `key`, or None if not stored / DB unavailable."""
    key = key.strip().upper()
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[1] < _CACHE_TTL_SECONDS:
        return hit[0]
    try:
        with get_session() as session:
            row = session.execute(
                text("SELECT value FROM public.app_config WHERE key = :k"),
                {"k": key},
            ).fetchone()
    except Exception as exc:
        # Table missing / DB down — don't poison the cache, just fall back.
        logger.warning("get_config(%s) failed; falling back: %s", key, exc)
        return None
    value = row[0] if row else None
    _CACHE[key] = (value, now)
    return value


def set_config(key: str, value: str) -> None:
    key = key.strip().upper()
    with get_session() as session:
        session.execute(
            text(
                """
                INSERT INTO public.app_config (key, value, updated_at)
                VALUES (:k, :v, NOW())
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value, updated_at = NOW()
                """
            ),
            {"k": key, "v": str(value)},
        )
        session.commit()
    _invalidate(key)


def delete_config(key: str) -> None:
    key = key.strip().upper()
    with get_session() as session:
        session.execute(
            text("DELETE FROM public.app_config WHERE key = :k"),
            {"k": key},
        )
        session.commit()
    _invalidate(key)


def list_config() -> dict[str, str]:
    try:
        with get_session() as session:
            rows = session.execute(
                text("SELECT key, value FROM public.app_config")
            ).fetchall()
    except Exception:
        return {}
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Typed resolvers: app_config row → env var → default
# ---------------------------------------------------------------------------


def config_str(key: str, default: Optional[str] = None) -> Optional[str]:
    val = get_config(key)
    if val is not None and val != "":
        return val
    env = os.environ.get(key.strip().upper())
    if env is not None and env != "":
        return env
    return default


def config_int(key: str, default: int) -> int:
    raw = config_str(key)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def config_float(key: str, default: float) -> float:
    raw = config_str(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def config_json(key: str, default: Any) -> Any:
    """Parse a JSON-encoded config value. Env fallback is skipped (JSON blobs
    don't live in .env); returns the stored value or the default."""
    raw = get_config(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def set_json(key: str, obj: Any) -> None:
    set_config(key, json.dumps(obj))
