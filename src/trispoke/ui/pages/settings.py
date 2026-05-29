"""Global settings — API keys + webhook URLs configured once, not per-campaign.

All three intake providers (Apollo, Anthropic, Abacus) are keyed at the
workspace level, not per campaign. This page is the operator's one-stop
shop: paste keys once, verify each, save to .env. Subsequent runs pick
them up automatically.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

from trispoke.config import get_settings
from trispoke.ui.utils.state import (
    ABACUS_BYOK_KEY,
    ABACUS_BYOK_VERIFIED_KEY,
    BYOK_KEY,
    BYOK_VERIFIED_KEY,
    persist_plaintext_to_env,
)
from trispoke.ui.utils.styling import badge


_APOLLO_VERIFIED_KEY = "settings_apollo_verified"
_WEBHOOK_VERIFIED_KEY = "settings_webhook_verified"


def render() -> None:
    st.title("Settings")
    st.caption(
        "Global credentials and webhooks. Configured here once, used by all "
        "campaigns. Saved to `.env` in plaintext (the file is gitignored)."
    )

    s = get_settings()

    st.divider()
    _render_apollo_section(s)

    st.divider()
    _render_anthropic_section(s)

    st.divider()
    _render_abacus_section(s)

    st.divider()
    _render_webhook_section(s)


# ---------------------------------------------------------------------------


def _render_apollo_section(s) -> None:
    st.subheader("Apollo API key")
    st.caption(
        "Required. Drives lead enrichment, search intake, and all "
        "Apollo-mode sending."
    )
    cols = st.columns([4, 1, 1])

    current = s.apollo_api_key or ""
    masked_display = ("•" * 6 + current[-4:]) if current else ""

    with cols[0]:
        new_key = st.text_input(
            "Apollo API key",
            value=current,
            type="password",
            label_visibility="collapsed",
            placeholder=masked_display or "Apollo API key",
            key="settings_apollo_input",
        )

    with cols[1]:
        if st.button("Verify", key="settings_apollo_verify",
                     use_container_width=True):
            ok, msg = _verify_apollo(new_key)
            st.session_state[_APOLLO_VERIFIED_KEY] = ok
            (st.success if ok else st.error)(msg or ("Verified." if ok else ""))

    with cols[2]:
        if st.session_state.get(_APOLLO_VERIFIED_KEY):
            st.markdown(
                f"<div style='padding-top:8px'>{badge('verified', 'verified')}</div>",
                unsafe_allow_html=True,
            )

    if st.button("Save to .env", key="settings_apollo_save", type="primary",
                 use_container_width=True):
        ok, msg = persist_plaintext_to_env("APOLLO_API_KEY", new_key.strip())
        (st.success if ok else st.error)(msg)


def _render_anthropic_section(s) -> None:
    st.subheader("Anthropic (Claude) API key")
    st.caption(
        "Optional. Required for Claude / Hybrid / Hybrid Smart generation. "
        "Get one at https://console.anthropic.com/."
    )
    cols = st.columns([4, 1, 1])

    current = s.anthropic_api_key or ""

    with cols[0]:
        new_key = st.text_input(
            "Anthropic API key",
            value=current,
            type="password",
            label_visibility="collapsed",
            placeholder="sk-ant-…",
            key="settings_anthropic_input",
        )
        # Keep session-state in sync so the rest of the app sees it.
        if new_key != st.session_state.get(BYOK_KEY, ""):
            st.session_state[BYOK_KEY] = new_key
            st.session_state[BYOK_VERIFIED_KEY] = False

    with cols[1]:
        if st.button("Verify", key="settings_anthropic_verify",
                     use_container_width=True):
            ok, msg = _verify_anthropic(new_key)
            st.session_state[BYOK_VERIFIED_KEY] = ok
            (st.success if ok else st.error)(msg or ("Verified." if ok else ""))

    with cols[2]:
        if st.session_state.get(BYOK_VERIFIED_KEY):
            st.markdown(
                f"<div style='padding-top:8px'>{badge('verified', 'verified')}</div>",
                unsafe_allow_html=True,
            )

    if st.button(
        "Save to .env", key="settings_anthropic_save", type="primary",
        use_container_width=True,
    ):
        ok, msg = persist_plaintext_to_env("ANTHROPIC_API_KEY", new_key.strip())
        (st.success if ok else st.error)(msg)


def _render_abacus_section(s) -> None:
    st.subheader("Abacus.AI RouteLLM API key")
    st.caption(
        "Optional. Required for the Abacus and Hybrid Smart modes. "
        "ChatLLM Teams subscription needed — get one at https://abacus.ai/."
    )
    cols = st.columns([4, 1, 1])

    current = s.abacus_api_key or ""

    with cols[0]:
        new_key = st.text_input(
            "Abacus API key",
            value=current,
            type="password",
            label_visibility="collapsed",
            placeholder="ABACUS API key",
            key="settings_abacus_input",
        )
        if new_key != st.session_state.get(ABACUS_BYOK_KEY, ""):
            st.session_state[ABACUS_BYOK_KEY] = new_key
            st.session_state[ABACUS_BYOK_VERIFIED_KEY] = False

    with cols[1]:
        if st.button("Verify", key="settings_abacus_verify",
                     use_container_width=True):
            ok, msg = _verify_abacus(new_key, s.abacus_base_url)
            st.session_state[ABACUS_BYOK_VERIFIED_KEY] = ok
            (st.success if ok else st.error)(msg or ("Verified." if ok else ""))

    with cols[2]:
        if st.session_state.get(ABACUS_BYOK_VERIFIED_KEY):
            st.markdown(
                f"<div style='padding-top:8px'>{badge('verified', 'verified')}</div>",
                unsafe_allow_html=True,
            )

    if st.button(
        "Save to .env", key="settings_abacus_save", type="primary",
        use_container_width=True,
    ):
        ok, msg = persist_plaintext_to_env("ABACUS_API_KEY", new_key.strip())
        (st.success if ok else st.error)(msg)


def _render_webhook_section(s) -> None:
    st.subheader("Reply notification webhook")
    st.caption(
        "Optional. POSTs a Slack-compatible JSON on every positive reply. "
        "Works with Slack incoming webhooks, Zapier catch-hooks, n8n, etc."
    )
    cols = st.columns([4, 1, 1])

    current = s.reply_webhook_url or ""

    with cols[0]:
        new_url = st.text_input(
            "Webhook URL",
            value=current,
            label_visibility="collapsed",
            placeholder="https://hooks.slack.com/services/T00000000/B00000000/XXX",
            key="settings_webhook_input",
        )

    with cols[1]:
        if st.button("Test", key="settings_webhook_test",
                     use_container_width=True):
            ok, msg = _test_webhook(new_url)
            st.session_state[_WEBHOOK_VERIFIED_KEY] = ok
            (st.success if ok else st.error)(msg)

    with cols[2]:
        if st.session_state.get(_WEBHOOK_VERIFIED_KEY):
            st.markdown(
                f"<div style='padding-top:8px'>{badge('verified', 'verified')}</div>",
                unsafe_allow_html=True,
            )

    if st.button(
        "Save to .env", key="settings_webhook_save", type="primary",
        use_container_width=True,
    ):
        ok, msg = persist_plaintext_to_env("REPLY_WEBHOOK_URL", new_url.strip())
        (st.success if ok else st.error)(msg)


# ---------- verification ----------


def _verify_apollo(key: str) -> tuple[bool, str]:
    if not key or not key.strip():
        return False, "Enter a key first."
    try:
        r = httpx.get(
            "https://api.apollo.io/api/v1/auth/health",
            headers={"x-api-key": key.strip()},
            timeout=5.0,
        )
        if r.status_code in (401, 403):
            return False, "Apollo rejected the key (401/403)."
        r.raise_for_status()
        return True, "Apollo auth OK."
    except Exception as e:
        return False, f"Verification failed: {e}"


def _verify_anthropic(key: str) -> tuple[bool, str]:
    if not key or not key.strip():
        return False, "Enter a key first."
    if not key.strip().startswith("sk-ant-"):
        return False, "Keys should start with 'sk-ant-'."
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key.strip())
        list(client.models.list(limit=1))
        return True, "Anthropic auth OK."
    except Exception as e:
        return False, f"Verification failed: {e}"


def _verify_abacus(key: str, base_url: str) -> tuple[bool, str]:
    if not key or not key.strip():
        return False, "Enter a key first."
    try:
        url = f"{(base_url or '').rstrip('/')}/models"
        r = httpx.get(
            url, headers={"Authorization": f"Bearer {key.strip()}"}, timeout=5.0
        )
        if r.status_code in (401, 403):
            return False, "Abacus rejected the key (401/403)."
        r.raise_for_status()
        return True, "Abacus auth OK."
    except Exception as e:
        return False, f"Verification failed: {e}"


def _test_webhook(url: str) -> tuple[bool, str]:
    if not url or not url.strip():
        return False, "Enter a URL first."
    payload = {
        "text": ":wave: Test ping from trispoke-outbound Settings.",
        "event": "settings_test",
    }
    try:
        r = httpx.post(url.strip(), json=payload, timeout=5.0)
        if 200 <= r.status_code < 300:
            return True, "Webhook responded 2xx."
        return False, f"Webhook returned {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, f"POST failed: {e}"
