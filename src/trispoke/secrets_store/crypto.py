"""Fernet symmetric encryption wrapper.

Reads TRISPOKE_SECRET_KEY from the environment. Empty or missing key →
`is_ready()` returns False; callers should refuse to write or surface a
config error.
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Optional[Fernet]:
    key = os.environ.get("TRISPOKE_SECRET_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, InvalidToken):
        return None


def is_ready() -> bool:
    return _fernet() is not None


def encrypt(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError(
            "TRISPOKE_SECRET_KEY not set or invalid. Generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and add it to .env.'
        )
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt; return None on any failure (missing key, tampered, wrong key).
    Callers can fall back to env without leaking a stack trace to logs."""
    f = _fernet()
    if f is None or not ciphertext:
        return None
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
