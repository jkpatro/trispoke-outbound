"""Campaign settings — wireframe 1.

Source → Email generation → BYOK → Model selectors → Sending → Actions.
"""

from __future__ import annotations

import json
from typing import Optional

import httpx
import pandas as pd
import streamlit as st

from trispoke.config import get_settings
from trispoke.db.models import Campaign, Lead, LeadStatus
from trispoke.db.session import get_session
from trispoke.secrets_store import secret
from trispoke.sender.inbox_manager import _parse_inboxes_config, today_sends_for_inbox
from trispoke.app_config import config_int
from trispoke.sender.ramp import get_daily_cap, get_ramp_schedule
from trispoke.ui.utils.state import (
    ABACUS_MODEL_KEY,
    get_campaign,
    set_campaign,
    set_page,
)
from trispoke.ui.utils.styling import badge

# Models Abacus.AI's RouteLLM exposes. Verbatim per the integration spec —
# update this list if Abacus renames or adds models.
ABACUS_MODEL_OPTIONS = [
    "route-llm",            # auto-route
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "gpt-5-5",
    "gemini-3-1-pro",
]


_EMAIL_COL_CANDIDATES = ("email", "email address", "work_email", "work email")


_WIZ_STEP_KEY = "campaign_wizard_step"
_WIZ_STEPS = [
    ("Basics & leads", "📝"),
    ("Email generation", "✍️"),
    ("Sending", "📤"),
    ("Automation", "🤖"),
    ("Review & launch", "🚀"),
]

_WIZ_CSS = """
<style>
  [class*="st-key-wizstep_"] button {
      border-radius: 10px; font-size: 13px; font-weight: 600;
      padding: 8px 6px; white-space: nowrap; box-shadow: none;
  }
  [class*="st-key-wizstep_"] button[kind="primary"],
  [class*="st-key-wizstep_"] button[data-testid="stBaseButton-primary"] {
      background: linear-gradient(135deg,#1f3a6e,#2b5fa0) !important;
      border-color: transparent !important; color: #fff !important;
  }
</style>
"""


def render() -> None:
    """Guided multi-step campaign builder. State lives in widget keys, so steps
    can be revisited freely and the campaign can be saved as a draft at any point."""
    editing = bool(get_campaign())
    st.title("Edit campaign" if editing else "Create a campaign")

    step = int(st.session_state.get(_WIZ_STEP_KEY, 0))
    step = max(0, min(step, len(_WIZ_STEPS) - 1))

    _render_stepper(step)
    st.progress((step + 1) / len(_WIZ_STEPS))
    st.caption(f"Step {step + 1} of {len(_WIZ_STEPS)} — {_WIZ_STEPS[step][0]}")
    st.divider()

    (_wiz_basics, _wiz_generation, _wiz_sending, _wiz_automation, _wiz_review)[step]()

    st.divider()
    _wiz_nav(step)


def _render_stepper(active: int) -> None:
    st.markdown(_WIZ_CSS, unsafe_allow_html=True)
    cols = st.columns(len(_WIZ_STEPS))
    for i, (label, icon) in enumerate(_WIZ_STEPS):
        marker = "✓" if i < active else icon
        if cols[i].button(
            f"{marker}  {label}",
            key=f"wizstep_{i}",
            use_container_width=True,
            type="primary" if i == active else "secondary",
        ):
            st.session_state[_WIZ_STEP_KEY] = i
            st.rerun()


# ---------- wizard steps ----------


def _wiz_basics() -> None:
    st.text_input(
        "Campaign name",
        placeholder="e.g. q2-staffing-toronto",
        key="wiz_name",
        help="A short, unique name. Save a draft any time and finish later.",
    )
    st.divider()
    _render_source_section()


def _wiz_generation() -> None:
    mode = _render_generation_section()
    needs_anthropic = mode in ("claude_only", "hybrid", "hybrid_smart")
    needs_abacus = mode in ("abacus_only", "hybrid_smart")
    _render_credential_status(needs_anthropic=needs_anthropic, needs_abacus=needs_abacus)
    _render_model_selectors(mode)


def _wiz_sending() -> None:
    _render_sending_mode_section()
    st.divider()
    _render_sending_section()


def _wiz_automation() -> None:
    _render_auto_approval_section()


def _wiz_review() -> None:
    v = _wiz_values()
    st.subheader("Review")

    gen_labels = {
        "local_only": "Local only", "claude_only": "Claude only",
        "abacus_only": "Abacus", "hybrid": "Hybrid (legacy)",
        "hybrid_smart": "Smart Hybrid",
    }
    aa_labels = {
        "never": "Never (human review)", "after_warmup": "After warmup",
        "always": "Always (full auto)",
    }
    auto_txt = aa_labels.get(v["auto_approve_mode"], v["auto_approve_mode"])
    if v["auto_approve_mode"] == "after_warmup":
        auto_txt += f" · warmup {v['warmup_threshold']}"

    rows = [
        ("Campaign name", v["name"] or "— not set —"),
        ("Email generation", gen_labels.get(v["mode"], v["mode"])),
        ("Sending mode", "Apollo" if v["sending_mode"] == "apollo" else "Direct SMTP (legacy)"),
        ("Auto-approval", auto_txt),
    ]
    for key_label, val in rows:
        c1, c2 = st.columns([1, 2])
        c1.markdown(f"**{key_label}**")
        c2.write(val)

    df = st.session_state.get("uploaded_df")
    if df is not None:
        st.caption(f"📎 {len(df)} rows queued from the uploaded file.")

    st.divider()
    if not v["name"]:
        st.warning("Set a campaign name on **Basics & leads** before launching.")
    if v["sending_mode"] == "smtp_direct" and not v["smtp_ack"]:
        st.warning("Confirm the SMTP acknowledgement on the **Sending** step first.")

    blocked = (not v["name"]) or (
        v["sending_mode"] == "smtp_direct" and not v["smtp_ack"]
    )
    if st.button(
        "🚀  Save & generate drafts",
        type="primary",
        use_container_width=True,
        disabled=blocked,
        key="wiz_launch",
    ):
        if (
            st.session_state.get("source_type") == "Apollo CSV upload"
            and st.session_state.get("uploaded_df") is None
        ):
            st.error("Upload an Apollo file on the first step before generating.")
            return
        _save_and_generate(
            name=v["name"], mode=v["mode"], sending_mode=v["sending_mode"],
            auto_approve_mode=v["auto_approve_mode"],
            warmup_threshold=v["warmup_threshold"],
        )


# ---------- wizard helpers ----------


def _wiz_values() -> dict:
    """Derive the campaign config from the persisted widget keys, so saving works
    no matter which step is currently on screen."""
    name = (st.session_state.get("wiz_name") or get_campaign() or "").strip()
    mode = st.session_state.get("gen_mode", "hybrid_smart")
    sm = str(st.session_state.get("sending_mode_radio", "Send via Apollo"))
    sending_mode = "apollo" if sm.startswith("Send via Apollo") else "smtp_direct"
    aa_map = {
        "Never (always human review)": "never",
        "After warmup (recommended)": "after_warmup",
        "Always (full auto)": "always",
    }
    auto_mode = aa_map.get(st.session_state.get("auto_approve_radio"), "after_warmup")
    threshold = int(st.session_state.get("auto_approve_threshold", 20) or 20)
    smtp_ack = bool(st.session_state.get("smtp_ack", False))
    return {
        "name": name, "mode": mode, "sending_mode": sending_mode,
        "auto_approve_mode": auto_mode, "warmup_threshold": threshold,
        "smtp_ack": smtp_ack,
    }


def _wiz_save_draft() -> None:
    v = _wiz_values()
    if not v["name"]:
        st.error("Enter a campaign name on the first step first.")
        return
    try:
        _save_campaign(
            name=v["name"], mode=v["mode"], sending_mode=v["sending_mode"],
            auto_approve_mode=v["auto_approve_mode"],
            warmup_threshold=v["warmup_threshold"],
        )
        set_campaign(v["name"])
        st.success(f"Draft saved — '{v['name']}'. You can continue any time.")
    except Exception as e:
        st.error(f"Save failed: {e}")


def _wiz_validate(step: int) -> tuple[bool, str]:
    v = _wiz_values()
    if step == 0 and not v["name"]:
        return False, "Enter a campaign name to continue."
    if step == 2 and v["sending_mode"] == "smtp_direct" and not v["smtp_ack"]:
        return False, "Tick the SMTP acknowledgement to continue."
    return True, ""


def _wiz_nav(step: int) -> None:
    last = len(_WIZ_STEPS) - 1
    back_col, draft_col, next_col = st.columns([1, 1, 1])
    with back_col:
        if step > 0 and st.button("←  Back", use_container_width=True, key="wiz_back"):
            st.session_state[_WIZ_STEP_KEY] = step - 1
            st.rerun()
    with draft_col:
        if st.button("💾  Save draft", use_container_width=True, key="wiz_draft"):
            _wiz_save_draft()
    with next_col:
        if step < last and st.button(
            "Next  →", type="primary", use_container_width=True, key="wiz_next"
        ):
            ok, msg = _wiz_validate(step)
            if ok:
                st.session_state[_WIZ_STEP_KEY] = step + 1
                st.rerun()
            else:
                st.error(msg)


# ---------- sections ----------

def _render_source_section() -> None:
    st.subheader("How do you want to add leads?")
    source_type = st.radio(
        "Intake path",
        options=[
            "Apollo CSV upload",
            "Apollo search query",
            "Add a single lead manually",
        ],
        horizontal=True,
        key="source_type",
        label_visibility="collapsed",
    )

    if source_type == "Apollo CSV upload":
        _render_csv_intake()
    elif source_type == "Apollo search query":
        _render_search_intake()
    else:
        _render_manual_intake()


def _render_csv_intake() -> None:
    uploaded = st.file_uploader("Upload Apollo .xlsx or .csv", type=["xlsx", "csv"])
    if uploaded is None:
        return

    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        return

    st.session_state["uploaded_df"] = df
    st.session_state["uploaded_name"] = uploaded.name

    rows = len(df)
    email_col = _find_email_col(df)
    reachable = int(df[email_col].notna().sum()) if email_col else 0
    missing = rows - reachable

    col1, col2 = st.columns([1, 4])
    col1.metric("Rows", rows)
    col2.markdown(
        f"<div style='padding-top:18px'>{badge(f'{reachable} reachable', 'reachable')}</div>",
        unsafe_allow_html=True,
    )

    if missing > 0:
        st.checkbox(
            f"Run Apollo waterfall on the {missing} missing-email rows (~50% recovery)",
            key="run_waterfall",
        )


def _render_search_intake() -> None:
    """V1.5: Apollo search-query intake. Preview + bulk-add into the campaign."""
    from trispoke.apollo.client import ApolloClient
    from trispoke.ui.utils.state import get_campaign

    titles = st.text_input(
        "Job titles (comma-separated)",
        placeholder="VP Sales, Head of Operations",
        key="search_titles",
    )
    industries = st.text_input(
        "Industries (comma-separated)",
        placeholder="logistics, manufacturing",
        key="search_industries",
    )
    sizes = st.text_input(
        "Employee count range (one range like 50,100; use ; for multiple, e.g. 1,10; 50,100)",
        placeholder="50,200",
        key="search_sizes",
    )
    locations = st.text_input(
        "Locations (separate multiple with ;)",
        placeholder="Bhubaneswar; Toronto",
        key="search_locations",
    )
    keywords = st.text_input(
        "Keywords (free-text, includes industry / company names)",
        placeholder="pharmaceutical Ezrx",
        key="search_keywords",
    )

    def _build_query() -> dict:
        q: dict = {}
        if titles.strip():
            q["person_titles"] = [s.strip() for s in titles.split(",") if s.strip()]
        if locations.strip():
            q["person_locations"] = [
                s.strip() for s in locations.split(";") if s.strip()
            ]
        if sizes.strip():
            # Each range is one "min,max" string. Multiple ranges via ';'.
            q["organization_num_employees_ranges"] = [
                s.strip() for s in sizes.split(";") if "," in s
            ]
        # Industries are folded into q_keywords because Apollo's industry-tag
        # param requires plan-specific tag IDs (not free-text on this tier).
        kw_parts = []
        if industries.strip():
            kw_parts.append(industries.strip())
        if keywords.strip():
            kw_parts.append(keywords.strip())
        if kw_parts:
            q["q_keywords"] = " ".join(kw_parts)
        return q

    cols = st.columns([1, 1])
    if cols[0].button("Preview matches", use_container_width=True):
        try:
            ac = ApolloClient()
            body = ac.search_contacts(_build_query(), page=1, page_size=10)
            # Apollo's api_search returns `people`; legacy `contacts` kept as fallback.
            results = body.get("people") or body.get("contacts") or []
            total = body.get("pagination", {}).get(
                "total_entries", len(results)
            )
            st.session_state["search_preview"] = results
            if results:
                st.success(
                    f"{len(results)} shown"
                    + (f" of {total} total" if total else "")
                )
            else:
                st.warning(
                    "No matches. Try broader keywords or a wider employee range."
                )
        except Exception as e:
            st.error(f"Search failed: {e}")

    preview = st.session_state.get("search_preview") or []
    if preview:
        for c in preview[:10]:
            org_name = (c.get("organization") or {}).get("name", "")
            st.markdown(
                f"- **{c.get('first_name','') or ''} {c.get('last_name','') or ''}** "
                f"· {c.get('title','') or '—'} · {org_name or '—'}"
            )

    if cols[1].button(
        "Add all matches to campaign", use_container_width=True, type="primary"
    ):
        campaign_name = get_campaign()
        if not campaign_name:
            st.error("Save the campaign first, then add leads.")
            return
        try:
            ac = ApolloClient()
            inserted = _bulk_add_search_results(ac, _build_query(), campaign_name)
            st.success(f"Added {inserted} lead(s) from Apollo search.")
        except Exception as e:
            st.error(f"Bulk add failed: {e}")


def _render_manual_intake() -> None:
    """V1.5: single-lead manual form. Does NOT auto-generate — lead waits
    for the normal enrich → pain → generate → QC pipeline cycle."""
    from trispoke.ui.utils.state import get_campaign

    with st.form("manual_lead_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        first = c1.text_input("First name *")
        last = c2.text_input("Last name *")
        email = st.text_input("Email *")
        title = st.text_input("Job title *")
        company = st.text_input("Company name *")
        domain = st.text_input("Company domain *")
        linkedin = st.text_input("LinkedIn URL (optional)")
        submitted = st.form_submit_button("Add lead to campaign", type="primary")

    if not submitted:
        return

    import re as _re

    required = {
        "first name": first.strip(),
        "last name": last.strip(),
        "email": email.strip(),
        "title": title.strip(),
        "company name": company.strip(),
        "company domain": domain.strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        st.error(f"Missing required: {', '.join(missing)}")
        return
    if not _re.match(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", email.strip()):
        st.error("Invalid email address.")
        return

    campaign_name = get_campaign()
    if not campaign_name:
        st.error("Save the campaign first, then add leads.")
        return

    try:
        _insert_manual_lead(
            campaign_name=campaign_name,
            first_name=first.strip(),
            last_name=last.strip(),
            email=email.strip(),
            title=title.strip(),
            company_name=company.strip(),
            company_domain=domain.strip(),
            linkedin_url=linkedin.strip() or None,
        )
    except ValueError as e:
        st.error(str(e))
        return
    st.success(
        "Lead added. The pipeline runner will enrich, generate a draft, and "
        "QC-check it in the next cycle."
    )


def _insert_manual_lead(**fields) -> int:
    """Insert a single lead with intake_source='manual_form' and status='new'.
    Returns the new lead id (or 0 if skipped as unsubscribed)."""
    import json as _json

    from trispoke.db.models import Campaign as _C
    from trispoke.db.models import Lead as _L
    from trispoke.db.models import LeadStatus as _LS
    from trispoke.db.session import get_session as _gs
    from trispoke.db.unsubscribe import is_unsubscribed as _is_unsubbed

    campaign_name = fields.pop("campaign_name")
    with _gs() as session:
        campaign = session.query(_C).filter_by(name=campaign_name).first()
        if not campaign:
            raise RuntimeError(f"campaign '{campaign_name}' missing")
        if _is_unsubbed(session, fields.get("email", "")):
            raise ValueError(
                f"Cannot add {fields.get('email')} — globally unsubscribed."
            )
        lead = _L(
            campaign_id=campaign.id,
            intake_source="manual_form",
            source_row_json=_json.dumps(fields),
            status=_LS.new.value,
            **fields,
        )
        session.add(lead)
        session.commit()
        return lead.id


def _bulk_add_search_results(apollo, query: dict, campaign_name: str) -> int:
    """V1.5: pull up to 500 search results and insert as new leads.

    V1.5.2: globally-unsubscribed emails are filtered out before insert.
    """
    import json as _json

    from trispoke.db.models import Campaign as _C
    from trispoke.db.models import Lead as _L
    from trispoke.db.models import LeadStatus as _LS
    from trispoke.db.session import get_session as _gs
    from trispoke.db.unsubscribe import is_unsubscribed as _is_unsubbed

    inserted = 0
    skipped_unsubbed = 0
    cap = 500
    page = 1
    with _gs() as session:
        campaign = session.query(_C).filter_by(name=campaign_name).first()
        if not campaign:
            raise RuntimeError(f"campaign '{campaign_name}' missing")
        while inserted < cap:
            body = apollo.search_contacts(query, page=page, page_size=25)
            contacts = body.get("people") or body.get("contacts") or []
            if not contacts:
                break
            for c in contacts:
                if inserted >= cap:
                    break
                email_addr = c.get("email")
                if not email_addr:
                    continue
                if _is_unsubbed(session, email_addr):
                    skipped_unsubbed += 1
                    continue
                exists = (
                    session.query(_L)
                    .filter_by(campaign_id=campaign.id, email=email_addr)
                    .first()
                )
                if exists:
                    continue
                org = c.get("organization") or {}
                lead = _L(
                    campaign_id=campaign.id,
                    email=email_addr,
                    first_name=c.get("first_name"),
                    last_name=c.get("last_name"),
                    title=c.get("title"),
                    company_name=org.get("name"),
                    company_domain=org.get("website_url"),
                    linkedin_url=c.get("linkedin_url"),
                    apollo_contact_id=c.get("id"),
                    intake_source="apollo_search",
                    source_row_json=_json.dumps({"apollo_id": c.get("id")}),
                    status=_LS.new.value,
                )
                session.add(lead)
                inserted += 1
            session.commit()
            page += 1
            if len(contacts) < 25:
                break
    if skipped_unsubbed:
        print(f"[search-intake] skipped {skipped_unsubbed} unsubscribed address(es)")
    return inserted


def _render_generation_section() -> str:
    st.subheader("Email generation")

    if "gen_mode" not in st.session_state:
        st.session_state["gen_mode"] = "hybrid_smart"

    cols = st.columns(4)
    _mode_card(
        cols[0],
        "local_only",
        "Local only",
        "Ollama only. Free, slowest, decent quality.",
    )
    _mode_card(
        cols[1],
        "claude_only",
        "Claude only",
        "Claude API direct. Best quality, costs $.",
    )
    _mode_card(
        cols[2],
        "abacus_only",
        "Abacus",
        "Abacus.AI RouteLLM. One subscription, many models.",
    )
    _mode_card(
        cols[3],
        "hybrid_smart",
        "Smart Hybrid",
        "Claude → Abacus → Local. Resilient default.",
        recommended=True,
    )

    if st.session_state["gen_mode"] == "hybrid":
        st.caption(
            "Note: `hybrid` is the legacy Claude → Local mode. "
            "New campaigns should use **Smart Hybrid** (Claude → Abacus → Local)."
        )

    return st.session_state["gen_mode"]


def _mode_card(
    col, value: str, label: str, description: str, recommended: bool = False
) -> None:
    with col:
        with st.container(border=True):
            if recommended:
                st.markdown(
                    f"**{label}** {badge('recommended', 'recommended')}",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{label}**")
            st.caption(description)
            selected = st.session_state["gen_mode"] == value
            if st.button(
                "Selected" if selected else "Select",
                key=f"mode_{value}",
                use_container_width=True,
                type="primary" if selected else "secondary",
            ):
                st.session_state["gen_mode"] = value
                st.rerun()


def _render_credential_status(*, needs_anthropic: bool, needs_abacus: bool) -> None:
    """Compact strip showing whether the credentials this mode needs are
    configured. Replaces the per-campaign BYOK pasting workflow — keys live
    on the global Settings page now.
    """
    settings = get_settings()
    anthropic_key = secret("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    abacus_key = secret("ABACUS_API_KEY", settings.abacus_api_key)
    items: list[str] = []
    if needs_anthropic:
        ok = bool(anthropic_key)
        items.append(
            f"<span style='color:{'#065f46' if ok else '#991b1b'};font-weight:600'>"
            f"{'✓' if ok else '✗'} Anthropic</span>"
        )
    if needs_abacus:
        ok = bool(abacus_key)
        items.append(
            f"<span style='color:{'#065f46' if ok else '#991b1b'};font-weight:600'>"
            f"{'✓' if ok else '✗'} Abacus</span>"
        )
    if not items:
        return

    missing = (
        (needs_anthropic and not anthropic_key)
        or (needs_abacus and not abacus_key)
    )
    sep = " &nbsp;·&nbsp; "
    st.markdown(
        f"<div style='padding:8px 12px;border:0.5px solid #d1d5db;"
        f"border-radius:8px;background:#f8fafc;color:#0f172a'>"
        f"<strong>Credentials</strong> &nbsp; {sep.join(items)}"
        f"{'  &nbsp;—&nbsp; <em>Configure missing keys in <strong>⚙ Settings</strong></em>' if missing else ''}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_model_selectors(mode: str) -> None:
    if mode in ("claude_only", "hybrid", "hybrid_smart"):
        st.selectbox(
            "Claude model",
            ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-7"],
            key="claude_model",
        )

    if mode in ("abacus_only", "hybrid_smart"):
        default_abacus = st.session_state.get(ABACUS_MODEL_KEY, "route-llm")
        idx = (
            ABACUS_MODEL_OPTIONS.index(default_abacus)
            if default_abacus in ABACUS_MODEL_OPTIONS
            else 0
        )
        st.selectbox(
            "Abacus model",
            ABACUS_MODEL_OPTIONS,
            index=idx,
            key=ABACUS_MODEL_KEY,
            help="`route-llm` lets Abacus pick per request. Pin a specific "
            "model for deterministic behaviour.",
        )

    if mode in ("local_only", "hybrid", "hybrid_smart"):
        models = _list_ollama_models()
        if models:
            default_model = get_settings().ollama_model
            idx = models.index(default_model) if default_model in models else 0
            st.selectbox("Local model (Ollama)", models, index=idx, key="local_model")
        else:
            st.warning(
                "Ollama unreachable — start `ollama serve` and reload to populate "
                "the local model list."
            )


def _render_auto_approval_section() -> tuple[str, int]:
    """Auto-approval policy: how aggressively to ship without human review.

    Defaults read from the currently-selected campaign in session_state if
    available so settings round-trip per campaign.
    """
    st.subheader("Auto-approval")

    # Load campaign defaults
    campaign_name = get_campaign()
    current_mode = "after_warmup"
    current_threshold = 20
    if campaign_name:
        with get_session() as _session:
            existing = (
                _session.query(Campaign).filter_by(name=campaign_name).first()
            )
            if existing:
                current_mode = existing.auto_approve_mode or "after_warmup"
                current_threshold = int(existing.warmup_threshold or 20)

    label_map = {
        "Never (always human review)": "never",
        "After warmup (recommended)": "after_warmup",
        "Always (full auto)": "always",
    }
    label_inv = {v: k for k, v in label_map.items()}

    options = list(label_map.keys())
    idx = options.index(label_inv.get(current_mode, options[1]))
    chosen_label = st.radio(
        "Auto-approval policy",
        options,
        index=idx,
        key="auto_approve_radio",
        label_visibility="collapsed",
        help=(
            "Never: every QC-passed draft waits for human approval.\n\n"
            "After warmup: once `warmup_threshold` sends have completed, "
            "QC-passed drafts auto-approve and ship via Apollo's schedule.\n\n"
            "Always: QC-passed drafts auto-approve immediately. Use only "
            "after you trust your QC + LLM prompts."
        ),
    )
    chosen = label_map[chosen_label]

    threshold = current_threshold
    if chosen == "after_warmup":
        threshold = st.number_input(
            "Warmup threshold (successful sends before auto-approval kicks in)",
            value=current_threshold,
            min_value=0,
            step=5,
            key="auto_approve_threshold",
        )

    if chosen == "always":
        st.warning(
            "⚠ Full-auto mode bypasses the human review queue entirely. "
            "QC-flagged drafts still get held; QC-passed drafts ship "
            "immediately on the next push cycle."
        )

    return chosen, int(threshold)


def _render_sending_mode_section() -> tuple[str, bool]:
    """V1.5: pick Apollo (recommended) or SMTP-direct (legacy, friction-gated).

    Returns (sending_mode, smtp_confirmation_checked).
    """
    st.subheader("Sending mode")

    mode_label = st.radio(
        "Sending mode",
        options=[
            "Send via Apollo  (recommended)",
            "Send via direct SMTP  (legacy fallback)",
        ],
        key="sending_mode_radio",
        label_visibility="collapsed",
    )
    sending_mode = "apollo" if mode_label.startswith("Send via Apollo") else "smtp_direct"

    smtp_ack = False
    if sending_mode == "smtp_direct":
        st.markdown(
            "<div class='ts-amber'>"
            "<strong>⚠ Legacy SMTP sending — read before using</strong><br><br>"
            "This bypasses Apollo's pacing and tracking entirely. Use only for "
            "one-off sends or testing.<br><br>"
            "<strong>What you lose:</strong><ul>"
            "<li>Apollo's warmup-aware pacing</li>"
            "<li>Apollo's open/reply tracking dashboard</li>"
            "<li>Automatic mailbox rotation</li>"
            "<li>Apollo's bounce auto-handling</li>"
            "</ul>"
            "Reply tracking falls back to local IMAP polling (requires the IMAP "
            "password under <strong>⚙ Settings → Credentials</strong>).<br><br>"
            "If you're sending more than 5 emails, use Apollo mode."
            "</div>",
            unsafe_allow_html=True,
        )
        smtp_ack = st.checkbox(
            "I understand this campaign will not appear in the Apollo dashboard",
            key="smtp_ack",
        )

    return sending_mode, smtp_ack


def _render_sending_section() -> None:
    st.subheader("Sending")

    inboxes = _parse_inboxes_config()
    for ib in inboxes:
        try:
            sent_today = today_sends_for_inbox(ib.address)
        except Exception:
            sent_today = 0
        st.markdown(
            f"<div class='ts-inbox-row'>"
            f"<span><strong>{ib.address}</strong></span>"
            f"<span>{sent_today} sent today</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Sending limits are workspace-wide and live on the Settings page; show a
    # read-only summary here so the campaign screen stays purely about content.
    cap = get_daily_cap()
    pace = config_int("PACE_SECONDS", 90)
    ramp = get_ramp_schedule()
    ramp_txt = " · ".join(f"Week {r['week']} → {r['cap']}/day" for r in ramp)
    st.markdown(
        '<div class="ts-amber">'
        f"<strong>Sending limits</strong> (apply to every campaign): up to "
        f"<strong>{cap}/day per inbox</strong>, one send every "
        f"<strong>{pace}s</strong>.<br>"
        f"New inboxes warm up gradually — {ramp_txt}.<br>"
        "Adjust these in <strong>⚙ Settings → Sending &amp; ramp</strong>."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------- helpers ----------

def _find_email_col(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).strip().lower() in _EMAIL_COL_CANDIDATES:
            return c
    return None


def _verify_anthropic_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "Enter a key first."
    if not key.startswith("sk-ant-"):
        return False, "Keys should start with 'sk-ant-'."
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=key)
        # Cheapest auth check: list models (metadata, no token spend).
        list(client.models.list(limit=1))
        return True, ""
    except Exception as e:
        return False, f"Verification failed: {e}"


def _verify_abacus_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "Enter a key first."
    import httpx

    settings = get_settings()
    url = f"{settings.abacus_base_url.rstrip('/')}/models"
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=5.0)
        if r.status_code in (401, 403):
            return False, "Key rejected (401/403)."
        r.raise_for_status()
        return True, ""
    except Exception as e:
        return False, f"Verification failed: {e}"


def _list_ollama_models() -> list[str]:
    settings = get_settings()
    try:
        r = httpx.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _save_campaign(
    *, name: str, mode: str, sending_mode: str = "apollo",
    auto_approve_mode: str = "after_warmup",
    warmup_threshold: int = 20,
) -> int:
    """Upsert campaign and append leads from the uploaded DataFrame, if any.

    `sending_mode` ('apollo' | 'smtp_direct') is stashed in settings_json so
    downstream UI surfaces (review queue tag, etc.) can read it.
    """
    df: Optional[pd.DataFrame] = st.session_state.get("uploaded_df")

    with get_session() as session:
        campaign = session.query(Campaign).filter_by(name=name).first()
        if campaign is None:
            initial_settings = {"sending_mode": sending_mode}
            campaign = Campaign(
                name=name, mode=mode, settings_json=json.dumps(initial_settings),
                auto_approve_mode=auto_approve_mode,
                warmup_threshold=warmup_threshold,
            )
            session.add(campaign)
            session.commit()
        else:
            campaign.mode = mode
            campaign.auto_approve_mode = auto_approve_mode
            campaign.warmup_threshold = warmup_threshold
            try:
                existing = json.loads(campaign.settings_json or "{}")
            except (TypeError, ValueError):
                existing = {}
            existing["sending_mode"] = sending_mode
            campaign.settings_json = json.dumps(existing)
            session.commit()

        if df is not None:
            email_col = _find_email_col(df)
            if email_col is None:
                # Allow leads without an email column — they'll need waterfall.
                return campaign.id

            from trispoke.db.unsubscribe import is_unsubscribed as _is_unsubbed
            skipped_unsubbed = 0
            for _, row in df.iterrows():
                raw_email = row[email_col]
                if pd.isna(raw_email) or not str(raw_email).strip():
                    continue
                email_addr = str(raw_email).strip()

                # V1.5.2: skip globally-unsubscribed addresses
                if _is_unsubbed(session, email_addr):
                    skipped_unsubbed += 1
                    continue

                exists = (
                    session.query(Lead)
                    .filter_by(campaign_id=campaign.id, email=email_addr)
                    .first()
                )
                if exists:
                    continue

                lead = Lead(
                    campaign_id=campaign.id,
                    email=email_addr,
                    first_name=_str_or_none(row, ("First Name", "first_name")),
                    last_name=_str_or_none(row, ("Last Name", "last_name")),
                    title=_str_or_none(row, ("Title", "title")),
                    company_name=_str_or_none(row, ("Company", "company_name", "Organization")),
                    company_domain=_str_or_none(row, ("Website", "company_domain", "Domain")),
                    source_row_json=json.dumps(row.dropna().to_dict(), default=str),
                    status=LeadStatus.new.value,
                    intake_source="apollo_csv",
                )
                session.add(lead)
            session.commit()
            if skipped_unsubbed:
                # Surface to the Streamlit UI so the user sees what got dropped
                try:
                    st.info(
                        f"Skipped {skipped_unsubbed} address(es) — globally unsubscribed."
                    )
                except Exception:
                    pass

        return campaign.id


def _str_or_none(row: pd.Series, candidates: tuple[str, ...]) -> Optional[str]:
    for c in candidates:
        if c in row.index:
            v = row[c]
            if pd.notna(v) and str(v).strip():
                return str(v).strip()
    return None


def _save_and_generate(
    *, name: str, mode: str, sending_mode: str = "apollo",
    auto_approve_mode: str = "after_warmup",
    warmup_threshold: int = 20,
) -> None:
    """Save campaign, then run enrich + generate inline inside `st.status`.

    Per agreed default: synchronous (no worker thread) with phase updates.
    """
    from trispoke.enrich import enrich_leads
    from trispoke.generate import generate_drafts

    with st.status("Saving campaign…", expanded=True) as status:
        st.write("Persisting campaign and leads…")
        try:
            _save_campaign(
                name=name, mode=mode, sending_mode=sending_mode,
                auto_approve_mode=auto_approve_mode,
                warmup_threshold=warmup_threshold,
            )
        except Exception as e:
            status.update(label=f"Save failed: {e}", state="error")
            return

        st.write("Enriching leads with Apollo…")
        try:
            enrich_leads(name)
        except SystemExit:
            # enrich_leads calls sys.exit(1) when campaign missing — should not
            # happen here since we just saved, but be defensive.
            status.update(label="Enrichment failed — campaign not found.", state="error")
            return
        except Exception as e:
            st.warning(f"Enrichment skipped: {e}")

        st.write(f"Generating drafts (mode: {mode})…")
        try:
            generate_drafts(name, mode)
        except SystemExit:
            status.update(label="Generation failed — campaign not found.", state="error")
            return
        except Exception as e:
            status.update(label=f"Generation failed: {e}", state="error")
            return

        status.update(label="Done — drafts ready for review.", state="complete")

    set_campaign(name)
    set_page("review")
    st.rerun()
