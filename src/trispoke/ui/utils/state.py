"""Streamlit session_state helpers.

API keys and operational settings are no longer written to .env from the UI —
secrets live (Fernet-encrypted) in trispoke.secrets_store and operational
settings live in trispoke.app_config, both edited from the Settings page.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

# Session-state keys
PAGE_KEY = "page"
CAMPAIGN_KEY = "selected_campaign"
ABACUS_MODEL_KEY = "abacus_model_choice"

DEFAULT_PAGE = "dashboard"


def init_state() -> None:
    """Idempotent initialisation of session-state defaults."""
    st.session_state.setdefault(PAGE_KEY, DEFAULT_PAGE)
    st.session_state.setdefault(CAMPAIGN_KEY, None)
    st.session_state.setdefault(ABACUS_MODEL_KEY, "route-llm")


def set_page(page: str) -> None:
    st.session_state[PAGE_KEY] = page


def get_page() -> str:
    return st.session_state.get(PAGE_KEY, DEFAULT_PAGE)


def set_campaign(name: Optional[str]) -> None:
    st.session_state[CAMPAIGN_KEY] = name


def get_campaign() -> Optional[str]:
    return st.session_state.get(CAMPAIGN_KEY)
