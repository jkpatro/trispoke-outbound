"""Global settings — API keys + webhook URLs.

All non-Supabase credentials live in the `app_secrets` Supabase table, Fernet-
encrypted with TRISPOKE_SECRET_KEY (only the app can decrypt). The DB column
holds opaque ciphertext, so anyone browsing the table sees `gAAAA...` rather
than the raw key.

This page only writes through secrets_store.set_secret(); legacy `.env`
values are picked up by secret() at read time, so existing campaigns keep
working until you migrate each key here.
"""

from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st

from trispoke.app_config import config_float, config_int, set_config, set_json
from trispoke.auth import (
    change_own_password,
    current_user,
    current_user_email,
    current_user_name,
    is_admin,
    update_full_name,
)
from trispoke.config import get_settings
from trispoke.secrets_store import (
    delete_secret,
    has_secret,
    is_encryption_ready,
    list_secrets,
    set_secret,
)
from trispoke.sender.ramp import DEFAULT_RAMP_SCHEDULE, get_daily_cap, get_ramp_schedule
from trispoke.ui.utils.styling import badge


_APOLLO_VERIFIED = "settings_apollo_verified"
_ANTHROPIC_VERIFIED = "settings_anthropic_verified"
_ABACUS_VERIFIED = "settings_abacus_verified"
_WEBHOOK_VERIFIED = "settings_webhook_verified"


def render() -> None:
    st.title("Settings")

    # Profile is for everyone; the operational tabs are admin-only. Profile is
    # the first tab so the avatar's "Edit profile" launcher lands on it.
    if is_admin():
        tab_profile, tab_cred, tab_send, tab_auto = st.tabs(
            ["👤 Profile", "🔐 Credentials", "📤 Sending & ramp", "🤖 Automation"]
        )
        with tab_profile:
            _render_profile_tab()
        with tab_cred:
            _render_credentials_tab()
        with tab_send:
            _render_sending_tab()
        with tab_auto:
            _render_automation_tab()
    else:
        # Non-admins manage only their own profile.
        _render_profile_tab()


# ---------------------------------------------------------------------------
# Profile tab — the user's own account. Home for all profile-related edits.
# ---------------------------------------------------------------------------


def _render_profile_tab() -> None:
    """Self-service profile management. Starts with change password; future
    profile edits land here too."""
    email = current_user_email() or "—"
    st.caption(f"Signed in as **{email}**")

    st.subheader("🔑 Change password")
    with st.form("profile_pw_form", clear_on_submit=True):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input(
            "New password", type="password", help="At least 8 characters."
        )
        confirm_pw = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", type="primary")
    if submitted:
        if new_pw != confirm_pw:
            st.error("New passwords don't match.")
        else:
            ok, msg = change_own_password(current_pw, new_pw)
            (st.success if ok else st.error)(msg)

    st.divider()
    st.subheader("Display name")
    st.caption("Shown in the header and in audit / invite trails.")
    new_name = st.text_input(
        "Display name",
        value=current_user_name() or "",
        key="profile_name_input",
        placeholder="e.g. Jignesh Patro",
        label_visibility="collapsed",
    )
    if st.button("Save name", type="primary", key="profile_save_name"):
        ok, msg = update_full_name(new_name)
        (st.success if ok else st.error)(msg)


def _render_credentials_tab() -> None:
    st.caption(
        "API keys and passwords are encrypted before they reach the database. "
        "The stored value is ciphertext only — your raw key is never readable "
        "from the database."
    )

    if not is_encryption_ready():
        st.error(
            "The encryption key isn't configured on the server, so credentials "
            "can't be saved yet. Ask whoever set up the server to configure it."
        )
        return

    _render_stored_secrets_panel()

    st.divider()
    _render_apollo_section()

    st.divider()
    _render_anthropic_section()

    st.divider()
    _render_abacus_section()

    st.divider()
    _render_webhook_section()

    st.divider()
    _render_secret_section(
        title='SMTP password',
        description='Gmail app password for legacy direct-SMTP sending.',
        key_name='SMTP_PASSWORD',
        legacy_settings_attr='smtp_password',
        placeholder='xxxx xxxx xxxx xxxx',
        verified_state_key='settings_smtp_verified',
        verifier=lambda v: (True, 'Stored — connection tested at send time.'),
    )

    st.divider()
    _render_secret_section(
        title='IMAP password',
        description='IMAP password for legacy reply tracking.',
        key_name='IMAP_PASSWORD',
        legacy_settings_attr='imap_password',
        placeholder='xxxx xxxx xxxx xxxx',
        verified_state_key='settings_imap_verified',
        verifier=lambda v: (True, 'Stored — connection tested at poll time.'),
    )


# ---------------------------------------------------------------------------
# Sending & ramp tab
# ---------------------------------------------------------------------------


def _render_sending_tab() -> None:
    st.caption(
        "Controls how fast each inbox sends. New mailboxes warm up gradually "
        "(the ramp schedule), then settle at the daily cap. These apply to every "
        "campaign."
    )

    st.subheader("Daily cap per inbox")
    st.caption(
        "The most emails one inbox will send per day once it's fully warmed up."
    )
    daily_cap = st.number_input(
        "Daily cap per inbox",
        min_value=1,
        max_value=2000,
        value=int(get_daily_cap()),
        step=1,
        key="cfg_daily_cap",
        label_visibility="collapsed",
    )

    st.subheader("Warm-up ramp schedule")
    st.caption(
        "Each new inbox climbs to the cap over these weeks. The last row is the "
        "steady state (e.g. “week 3 onward”). Edit the values, add or remove rows."
    )
    schedule = get_ramp_schedule() or [dict(r) for r in DEFAULT_RAMP_SCHEDULE]
    ramp_df = pd.DataFrame(schedule)[["week", "cap"]]
    edited = st.data_editor(
        ramp_df,
        key="cfg_ramp_editor",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "week": st.column_config.NumberColumn(
                "Week (from)", min_value=1, step=1, required=True,
                help="The week this cap takes effect, counting from campaign start.",
            ),
            "cap": st.column_config.NumberColumn(
                "Emails / inbox / day", min_value=1, step=1, required=True,
            ),
        },
    )

    st.subheader("Pace")
    st.caption("Seconds to wait between individual sends from the same inbox.")
    pace = st.number_input(
        "Pace (seconds between sends)",
        min_value=1,
        max_value=3600,
        value=config_int("PACE_SECONDS", 90),
        step=5,
        key="cfg_pace_seconds",
        label_visibility="collapsed",
    )

    if st.button("Save sending settings", type="primary", key="cfg_save_sending"):
        cleaned = _clean_ramp(edited)
        if cleaned is None:
            st.error("Ramp schedule needs at least one row with a week and a cap.")
            return
        try:
            set_config("DAILY_CAP_PER_INBOX", str(int(daily_cap)))
            set_config("PACE_SECONDS", str(int(pace)))
            set_json("RAMP_SCHEDULE", cleaned)
            st.success("Sending settings saved.")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")


def _clean_ramp(df) -> list[dict] | None:
    """Coerce the edited ramp table into a sorted list of {week, cap}."""
    rows: list[dict] = []
    try:
        records = df.to_dict("records")
    except Exception:
        return None
    for r in records:
        try:
            week = int(r["week"])
            cap = int(r["cap"])
        except (KeyError, TypeError, ValueError):
            continue
        if week >= 1 and cap >= 1:
            rows.append({"week": week, "cap": cap})
    if not rows:
        return None
    # De-dupe by week (last wins), then sort.
    by_week = {r["week"]: r["cap"] for r in rows}
    return [{"week": w, "cap": by_week[w]} for w in sorted(by_week)]


# ---------------------------------------------------------------------------
# Automation tab
# ---------------------------------------------------------------------------


def _render_automation_tab() -> None:
    st.caption(
        "Safety rails and throughput for the automated sending + tracking "
        "workers. Changes take effect within a minute (long-running workers may "
        "need a restart to pick them up)."
    )

    st.subheader("Bounce safety")
    st.caption(
        "Automatically pause sending if too many recent emails bounce — protects "
        "your domain reputation."
    )
    cols = st.columns(2)
    bounce_pct = cols[0].number_input(
        "Pause if bounce rate exceeds (%)",
        min_value=0.0,
        max_value=100.0,
        value=round(config_float("BOUNCE_PAUSE_THRESHOLD", 0.03) * 100, 2),
        step=0.5,
        key="cfg_bounce_pct",
    )
    bounce_min = cols[1].number_input(
        "…but only after at least this many sends",
        min_value=1,
        max_value=100000,
        value=config_int("BOUNCE_PAUSE_MIN_SENDS", 20),
        step=1,
        key="cfg_bounce_min",
    )

    st.subheader("Apollo throughput")
    st.caption("How aggressively the workers enroll leads and poll Apollo.")
    cols2 = st.columns(2)
    max_enroll = cols2[0].number_input(
        "Max enrollments per minute",
        min_value=1,
        max_value=10000,
        value=config_int("APOLLO_MAX_ENROLLMENTS_PER_MINUTE", 25),
        step=1,
        key="cfg_max_enroll",
    )
    poll_interval = cols2[1].number_input(
        "Reply-poll interval (seconds)",
        min_value=30,
        max_value=86400,
        value=config_int("APOLLO_POLL_INTERVAL_SECONDS", 900),
        step=30,
        key="cfg_poll_interval",
    )

    if st.button("Save automation settings", type="primary", key="cfg_save_auto"):
        try:
            set_config("BOUNCE_PAUSE_THRESHOLD", str(round(bounce_pct / 100.0, 6)))
            set_config("BOUNCE_PAUSE_MIN_SENDS", str(int(bounce_min)))
            set_config("APOLLO_MAX_ENROLLMENTS_PER_MINUTE", str(int(max_enroll)))
            set_config("APOLLO_POLL_INTERVAL_SECONDS", str(int(poll_interval)))
            st.success("Automation settings saved.")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")


# ---------------------------------------------------------------------------
# Stored secrets overview
# ---------------------------------------------------------------------------


def _render_stored_secrets_panel() -> None:
    st.subheader("Stored secrets")
    rows = list_secrets()
    if not rows:
        st.info(
            "No secrets stored yet. Use the sections below to save Apollo, "
            "Anthropic, Abacus, and webhook credentials. Each gets encrypted "
            "before it touches the database."
        )
        return

    header = st.columns([3, 2, 2, 1])
    header[0].markdown("**Key**")
    header[1].markdown("**Last set by**")
    header[2].markdown("**Updated**")
    header[3].markdown("**Value**")

    for r in rows:
        row_cols = st.columns([3, 2, 2, 1])
        row_cols[0].code(r["key_name"], language=None)
        row_cols[1].write(r["last_set_by_email"] or "—")
        row_cols[2].write(
            r["updated_at"].strftime("%Y-%m-%d %H:%M") if r["updated_at"] else "—"
        )
        row_cols[3].markdown(
            "`••••••••`",
            help="Decryption only happens at the moment a client needs the value.",
        )


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _user_id() -> str | None:
    u = current_user()
    return (u or {}).get("id")


def _render_secret_section(
    *,
    title: str,
    description: str,
    key_name: str,
    legacy_settings_attr: str | None,
    placeholder: str,
    verified_state_key: str,
    verifier,
) -> None:
    st.subheader(title)
    st.caption(description)

    s = get_settings()
    legacy_value = (
        (getattr(s, legacy_settings_attr, None) or "")
        if legacy_settings_attr
        else ""
    )
    stored = has_secret(key_name)  # DB-only — used to decide source label
    if stored:
        source = "🔐 Supabase (encrypted)"
    elif legacy_value:
        source = "⚠ from server config — import below to encrypt"
    else:
        source = "— not set —"

    st.caption(f"Current source: **{source}**")

    input_placeholder = (
        "(stored — leave blank to keep)" if stored else placeholder
    )

    cols = st.columns([4, 1, 1])
    with cols[0]:
        new_value = st.text_input(
            title,
            value="",
            type="password",
            label_visibility="collapsed",
            placeholder=input_placeholder,
            key=f"settings_input_{key_name}",
        )
    with cols[1]:
        if st.button("Verify", key=f"settings_verify_{key_name}",
                     use_container_width=True):
            ok, msg = verifier(new_value)
            st.session_state[verified_state_key] = (ok, new_value.strip())
            (st.success if ok else st.error)(msg)
    with cols[2]:
        verified_state = st.session_state.get(verified_state_key)
        if (
            isinstance(verified_state, tuple)
            and verified_state[0]
            and verified_state[1] == new_value.strip()
        ):
            st.markdown(
                f"<div style='padding-top:8px'>{badge('verified', 'verified')}</div>",
                unsafe_allow_html=True,
            )

    action_cols = st.columns([1, 1, 1])
    with action_cols[0]:
        if st.button("Save encrypted", key=f"settings_save_{key_name}",
                     type="primary", use_container_width=True):
            if not new_value.strip():
                st.info(
                    f"No change — leave blank to keep the existing "
                    f"{key_name}, or enter a new value to overwrite."
                )
            else:
                try:
                    set_secret(key_name, new_value.strip(), user_id=_user_id())
                    st.success(f"{key_name} encrypted and stored in Supabase.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")
    with action_cols[1]:
        if legacy_value and st.button(
            "Import", key=f"settings_import_{key_name}",
            help="Encrypt the value currently set in the server config.",
            use_container_width=True,
        ):
            try:
                set_secret(key_name, legacy_value.strip(), user_id=_user_id())
                st.success(
                    f"{key_name} imported from the server config and encrypted."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
    with action_cols[2]:
        if stored and st.button(
            "Delete", key=f"settings_delete_{key_name}",
            use_container_width=True,
        ):
            try:
                delete_secret(key_name)
                st.warning(f"{key_name} removed from Supabase.")
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")


def _render_apollo_section() -> None:
    _render_secret_section(
        title="Apollo API key",
        description=(
            "Required. Drives lead enrichment, search intake, and all "
            "Apollo-mode sending."
        ),
        key_name="APOLLO_API_KEY",
        legacy_settings_attr="apollo_api_key",
        placeholder="Apollo API key",
        verified_state_key=_APOLLO_VERIFIED,
        verifier=_verify_apollo,
    )


def _render_anthropic_section() -> None:
    _render_secret_section(
        title="Anthropic (Claude) API key",
        description=(
            "Optional. Required for Claude / Hybrid / Smart Hybrid modes. "
            "Get one at https://console.anthropic.com/."
        ),
        key_name="ANTHROPIC_API_KEY",
        legacy_settings_attr="anthropic_api_key",
        placeholder="sk-ant-…",
        verified_state_key=_ANTHROPIC_VERIFIED,
        verifier=_verify_anthropic,
    )


def _render_abacus_section() -> None:
    _render_secret_section(
        title="Abacus.AI RouteLLM API key",
        description=(
            "Optional. Required for Abacus and Smart Hybrid modes. "
            "ChatLLM Teams subscription needed."
        ),
        key_name="ABACUS_API_KEY",
        legacy_settings_attr="abacus_api_key",
        placeholder="Abacus API key",
        verified_state_key=_ABACUS_VERIFIED,
        verifier=lambda k: _verify_abacus(k, get_settings().abacus_base_url),
    )


def _render_webhook_section() -> None:
    _render_secret_section(
        title="Reply notification webhook",
        description=(
            "Optional. POSTs a Slack-compatible JSON on every positive reply. "
            "Works with Slack incoming webhooks, Zapier, n8n, etc."
        ),
        key_name="REPLY_WEBHOOK_URL",
        legacy_settings_attr="reply_webhook_url",
        placeholder="https://hooks.slack.com/services/T00000000/B00000000/XXX",
        verified_state_key=_WEBHOOK_VERIFIED,
        verifier=_test_webhook,
    )


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------


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
