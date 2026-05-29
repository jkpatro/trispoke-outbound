"""Streamlit session_state helpers + at-rest BYOK encryption.

Design (per agreed defaults):
- Session: API key lives in `st.session_state` only — in-memory, dies with
  the session. No "encryption in session" because there is nothing at rest.
- Disk: when the user clicks "Save as default", encrypt with Fernet keyed off
  the TRISPOKE_SECRET_KEY env var, then write to .env as ANTHROPIC_API_KEY_ENC.
  Without TRISPOKE_SECRET_KEY set, the persist action refuses (will not write
  the plaintext key to .env via the UI).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import streamlit as st
from cryptography.fernet import Fernet, InvalidToken

# Session-state keys
PAGE_KEY = "page"
CAMPAIGN_KEY = "selected_campaign"
BYOK_KEY = "byok_anthropic_key"
BYOK_VERIFIED_KEY = "byok_verified"
ABACUS_BYOK_KEY = "byok_abacus_key"
ABACUS_BYOK_VERIFIED_KEY = "byok_abacus_verified"
ABACUS_MODEL_KEY = "abacus_model_choice"

DEFAULT_PAGE = "campaigns"


def init_state() -> None:
    """Idempotent initialisation of session-state defaults."""
    st.session_state.setdefault(PAGE_KEY, DEFAULT_PAGE)
    st.session_state.setdefault(CAMPAIGN_KEY, None)
    st.session_state.setdefault(BYOK_KEY, "")
    st.session_state.setdefault(BYOK_VERIFIED_KEY, False)
    st.session_state.setdefault(ABACUS_BYOK_KEY, "")
    st.session_state.setdefault(ABACUS_BYOK_VERIFIED_KEY, False)
    st.session_state.setdefault(ABACUS_MODEL_KEY, "route-llm")


def set_page(page: str) -> None:
    st.session_state[PAGE_KEY] = page


def get_page() -> str:
    return st.session_state.get(PAGE_KEY, DEFAULT_PAGE)


def set_campaign(name: Optional[str]) -> None:
    st.session_state[CAMPAIGN_KEY] = name


def get_campaign() -> Optional[str]:
    return st.session_state.get(CAMPAIGN_KEY)


def _get_fernet() -> Optional[Fernet]:
    secret = os.environ.get("TRISPOKE_SECRET_KEY")
    if not secret:
        return None
    try:
        return Fernet(secret.encode())
    except (ValueError, InvalidToken):
        return None


def _persist_encrypted(
    plaintext: str, env_var: str, env_path: str = ".env"
) -> tuple[bool, str]:
    if not plaintext:
        return False, "No key to save."
    f = _get_fernet()
    if f is None:
        return False, (
            "TRISPOKE_SECRET_KEY env var not set (or invalid Fernet key). "
            "Generate one with `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`, export it, and try again."
        )

    encrypted = f.encrypt(plaintext.encode()).decode()

    env_file = Path(env_path)
    lines: list[str] = []
    found = False
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines(keepends=True):
            if raw.startswith(f"{env_var}="):
                lines.append(f"{env_var}={encrypted}\n")
                found = True
            else:
                lines.append(raw)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{env_var}={encrypted}\n")

    env_file.write_text("".join(lines), encoding="utf-8")
    return True, f"Saved encrypted key to {env_path} as {env_var}."


def persist_byok_to_env(plaintext: str, env_path: str = ".env") -> tuple[bool, str]:
    """Encrypt the Anthropic BYOK key and persist as ANTHROPIC_API_KEY_ENC."""
    return _persist_encrypted(plaintext, "ANTHROPIC_API_KEY_ENC", env_path)


def persist_abacus_byok_to_env(
    plaintext: str, env_path: str = ".env"
) -> tuple[bool, str]:
    """Encrypt the Abacus BYOK key and persist as ABACUS_API_KEY_ENC."""
    return _persist_encrypted(plaintext, "ABACUS_API_KEY_ENC", env_path)


def persist_plaintext_to_env(
    var_name: str, value: str, env_path: str = ".env"
) -> tuple[bool, str]:
    """Write `VAR_NAME=value` to .env in plaintext. Used by the Settings page.

    `.env` is already gitignored, so plaintext credentials there are no
    worse than what's already on the machine. Returns (ok, message).
    """
    if not var_name or not var_name.strip():
        return False, "Missing variable name."
    var_name = var_name.strip().upper()

    env_file = Path(env_path)
    lines: list[str] = []
    found = False
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines(keepends=True):
            # Match exactly "VAR=…" at the start of the line (ignoring inline comments)
            stripped = raw.lstrip()
            if stripped.startswith(f"{var_name}="):
                lines.append(f"{var_name}={value}\n")
                found = True
            else:
                lines.append(raw)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{var_name}={value}\n")

    try:
        env_file.write_text("".join(lines), encoding="utf-8")
    except Exception as e:
        return False, f"Failed to write {env_path}: {e}"

    # Reflect immediately in the process environment AND clear settings cache
    # so the next get_settings() call re-reads the file.
    os.environ[var_name] = value
    try:
        from trispoke.config import get_settings
        get_settings.cache_clear()
    except Exception:
        pass

    return True, f"Saved to {env_path} as {var_name}."
