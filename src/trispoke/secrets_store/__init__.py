"""Encrypted secret store backed by Supabase `app_secrets`.

Public API:
    get_secret(key)       -> plaintext str | None
    secret(key, fallback) -> plaintext, falling back to env then to the
                             passed default. Use this in call-sites.
    set_secret(key, value, user_id)
    delete_secret(key)
    list_secrets()        -> [{key_name, last_set_by_email, updated_at}]
    is_encryption_ready() -> bool

All values are Fernet-encrypted before they reach the database; the DB sees
only opaque ciphertext. The encryption key lives in TRISPOKE_SECRET_KEY env
var and never leaves the host.
"""

from trispoke.secrets_store.store import (
    decrypt_health,
    delete_secret,
    get_secret,
    has_secret,
    is_encryption_ready,
    list_secrets,
    secret,
    set_secret,
)

__all__ = [
    "decrypt_health",
    "delete_secret",
    "get_secret",
    "has_secret",
    "is_encryption_ready",
    "list_secrets",
    "secret",
    "set_secret",
]
