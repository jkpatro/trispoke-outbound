"""Review queue — wireframe 2."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

import streamlit as st

from trispoke.db.models import Campaign, Email, Lead, LeadStatus, PainAnalysis
from trispoke.db.session import get_session
from trispoke.ui.utils.state import get_campaign, set_page
from trispoke.ui.utils.styling import badge


PAGE_SIZE = 25

_FILTER_CHIPS = [
    ("all", "All"),
    ("drafted", "Needs review"),
    ("qc_flagged", "⚠ QC flagged"),
    ("approved", "Approved"),
    ("queued_in_apollo", "In Apollo"),
    ("sent", "Sent"),
    ("replied", "Replied"),
]

_SOURCE_ICON = {
    "apollo_csv": "📁",
    "apollo_search": "🔍",
    "manual_form": "✍️",
}


def render() -> None:
    campaign_name = get_campaign()
    if not campaign_name:
        st.info("Select a campaign in the sidebar to view its drafts.")
        return

    with get_session() as session:
        campaign = session.query(Campaign).filter_by(name=campaign_name).first()
        if not campaign:
            st.warning(f"Campaign '{campaign_name}' not found.")
            return

        counts = _count_by_status(session, campaign.id)
        approved_count = counts.get("approved", 0)
        sending_mode = _campaign_sending_mode(campaign)

        # Header
        h_cols = st.columns([3, 1, 1, 1])
        with h_cols[0]:
            st.markdown(
                f"<div class='ts-breadcrumb'>Campaign / {campaign_name}</div>",
                unsafe_allow_html=True,
            )
            tag = ""
            if sending_mode == "smtp_direct":
                tag = (
                    "<span style='margin-left:8px;padding:2px 8px;border-radius:10px;"
                    "background:#fef3c7;color:#92400e;border:0.5px solid #f59e0b;"
                    "font-size:11px;font-weight:600'>⚠ SMTP</span>"
                )
            st.markdown(
                f"<h1 style='margin-bottom:0'>Review queue{tag}</h1>",
                unsafe_allow_html=True,
            )
        with h_cols[1]:
            st.write("")
            if st.button(
                f"Send approved ({approved_count})",
                type="primary",
                use_container_width=True,
                disabled=approved_count == 0,
            ):
                _kick_sender(approved_count, sending_mode)
        with h_cols[2]:
            st.write("")
            if st.button(
                "📊 Export (filtered)", use_container_width=True
            ):
                _trigger_export(
                    session, campaign, filtered=True,
                    status_filter=st.session_state.get("queue_filter", "all"),
                )
        with h_cols[3]:
            st.write("")
            if st.button("📊 Export all", use_container_width=True):
                _trigger_export(session, campaign, filtered=False)

        # Metric cards
        m_cols = st.columns(4)
        m_cols[0].metric("Total", counts.get("total", 0))
        m_cols[1].metric("Drafted", counts.get("drafted", 0))
        m_cols[2].metric("Approved", counts.get("approved", 0))
        m_cols[3].metric("Sent", counts.get("sent", 0))

        st.divider()

        # Filter chips
        st.session_state.setdefault("queue_filter", "all")
        chip_cols = st.columns(len(_FILTER_CHIPS))
        for col, (key, label) in zip(chip_cols, _FILTER_CHIPS):
            with col:
                count = counts.get("total" if key == "all" else key, 0)
                active = st.session_state["queue_filter"] == key
                if st.button(
                    f"{label} ({count})",
                    key=f"chip_{key}",
                    use_container_width=True,
                    type="primary" if active else "secondary",
                ):
                    st.session_state["queue_filter"] = key
                    st.session_state["queue_page"] = 1
                    st.rerun()

        # Model filter
        st.selectbox(
            "Model filter",
            ["all", "local only", "claude only"],
            key="queue_model_filter",
        )

        # Build the visible row set
        rows = _query_leads_with_latest_email(
            session,
            campaign.id,
            status_filter=st.session_state["queue_filter"],
            model_filter=st.session_state["queue_model_filter"],
        )

        st.session_state.setdefault("queue_page", 1)
        page = st.session_state["queue_page"]
        total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        st.session_state["queue_page"] = page

        start = (page - 1) * PAGE_SIZE
        page_rows = rows[start : start + PAGE_SIZE]

        st.divider()
        if not page_rows:
            st.info("No leads match the current filter.")
            return

        for lead, email in page_rows:
            _render_row(lead, email)

        if total_pages > 1:
            p_cols = st.columns([1, 2, 1])
            with p_cols[0]:
                if st.button("◀ Prev", disabled=page <= 1, use_container_width=True):
                    st.session_state["queue_page"] = page - 1
                    st.rerun()
            with p_cols[1]:
                st.markdown(
                    f"<div style='text-align:center;padding-top:6px'>"
                    f"Page {page} of {total_pages}</div>",
                    unsafe_allow_html=True,
                )
            with p_cols[2]:
                if st.button(
                    "Next ▶", disabled=page >= total_pages, use_container_width=True
                ):
                    st.session_state["queue_page"] = page + 1
                    st.rerun()


# ---------- helpers ----------

def _count_by_status(session, campaign_id: int) -> dict[str, int]:
    counts: dict[str, int] = {
        "total": session.query(Lead).filter_by(campaign_id=campaign_id).count()
    }
    for status in [
        "new",
        "enriched",
        "drafted",
        "qc_pending",
        "qc_flagged",
        "approved",
        "queued_in_apollo",
        "sent",
        "replied",
        "bounced",
    ]:
        counts[status] = (
            session.query(Lead)
            .filter_by(campaign_id=campaign_id, status=status)
            .count()
        )
    return counts


def _query_leads_with_latest_email(
    session, campaign_id: int, status_filter: str, model_filter: str
) -> list[tuple[Lead, Optional[Email]]]:
    q = session.query(Lead).filter(Lead.campaign_id == campaign_id)
    if status_filter != "all":
        q = q.filter(Lead.status == status_filter)
    leads = q.order_by(Lead.created_at.desc()).all()

    out: list[tuple[Lead, Optional[Email]]] = []
    for lead in leads:
        latest = (
            session.query(Email)
            .filter_by(lead_id=lead.id)
            .order_by(Email.created_at.desc())
            .first()
        )
        if model_filter == "local only":
            if latest is None or (latest.model_used or "").startswith("claude"):
                continue
        elif model_filter == "claude only":
            if latest is None or not (latest.model_used or "").startswith("claude"):
                continue
        out.append((lead, latest))
    return out


def _render_row(lead: Lead, email: Optional[Email]) -> None:
    initials = _initials(lead)
    name = (
        f"{lead.first_name or ''} {lead.last_name or ''}".strip() or (lead.email or "?")
    )
    subject = email.subject if email else "(no draft)"
    model = (email.model_used or "") if email else ""
    is_claude = "claude" in model
    is_abacus = model.startswith("abacus:")

    source_icon = _SOURCE_ICON.get(lead.intake_source or "apollo_csv", "")

    # QC indicator (red 🚫 for errors, amber ⚠ for warnings, blank if clean/pending)
    qc_icon = ""
    qc_tooltip = ""
    if email and email.qc_flags_json:
        from trispoke.llm.qc_checker import flags_from_json, has_errors

        flags = flags_from_json(email.qc_flags_json)
        if flags:
            qc_icon = "🚫" if has_errors(flags) else "⚠"
            qc_tooltip = "; ".join(
                f.type + (f" — {f.detail}" if f.detail else "") for f in flags[:4]
            )

    with st.container(border=True):
        cols = st.columns([1, 6, 1, 1, 1, 1])
        with cols[0]:
            st.markdown(
                f"<div class='ts-avatar'>{initials}</div>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            src_prefix = f"{source_icon} " if source_icon else ""
            st.markdown(f"{src_prefix}**{name}**")
            st.caption((subject or "")[:120])
        with cols[2]:
            if email:
                if is_abacus:
                    kind = "abacus"
                elif is_claude:
                    kind = "claude"
                else:
                    kind = "local"
                st.markdown(badge(kind, kind), unsafe_allow_html=True)
        with cols[3]:
            if qc_icon:
                st.markdown(f"<span title='{qc_tooltip}'>{qc_icon}</span>", unsafe_allow_html=True)
        with cols[4]:
            st.caption(lead.status)
        with cols[5]:
            if st.button("Review", key=f"review_{lead.id}", use_container_width=True):
                st.session_state["selected_lead_id"] = lead.id
                set_page("lead_detail")
                st.rerun()


def _initials(lead: Lead) -> str:
    f = (lead.first_name or "").strip()
    l = (lead.last_name or "").strip()
    if f and l:
        return (f[0] + l[0]).upper()
    if f:
        return f[:2].upper()
    return (lead.email or "??")[:2].upper()


def _campaign_sending_mode(campaign: Campaign) -> str:
    """Read sending_mode from settings_json. Default 'apollo' for backwards-compat."""
    if not campaign.settings_json:
        return "apollo"
    try:
        return (json.loads(campaign.settings_json) or {}).get("sending_mode", "apollo")
    except (TypeError, ValueError):
        return "apollo"


def _kick_sender(approved_count: int, sending_mode: str) -> None:
    """Spawn the right sender daemon for the campaign's mode.

    Apollo-mode → apollo_push_worker (the V1.5 default).
    SMTP-direct → legacy sender_loop.
    """
    if sending_mode == "smtp_direct":
        module = "trispoke.sender.sender_loop"
        label = "SMTP sender"
    else:
        module = "trispoke.sender.apollo_push_worker"
        label = "Apollo push worker"

    pid = st.session_state.get("sender_pid")
    if pid and _pid_alive(pid):
        st.info(f"{label} already running (PID {pid}). It will pick these up.")
        return

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        st.error(f"Failed to start {label}: {e}")
        return

    st.session_state["sender_pid"] = proc.pid
    st.success(
        f"{label} started (PID {proc.pid}). "
        f"It will pick up the {approved_count} approved email(s) on its next poll."
    )


# ---------- Excel export (V1.5) ----------

_EXPORT_COLUMNS = [
    "Lead first name", "Lead last name", "Lead email", "Lead title",
    "Company name", "Company domain", "Industry", "Headcount", "Location",
    "Intake source", "LinkedIn URL",
    "Pain chronic", "Pain acute", "Pain trigger", "Pain confidence",
    "Email subject", "Email body", "Model used",
    "Generation seconds", "Tokens used",
    "QC status", "QC flags",
    "Status", "Created at",
    "Apollo sequence ID", "Apollo message ID",
]


def _build_export_rows(session, campaign_id: int, status_filter: str) -> list[dict]:
    q = session.query(Lead).filter_by(campaign_id=campaign_id)
    if status_filter not in ("all", None):
        q = q.filter(Lead.status == status_filter)
    leads = q.order_by(Lead.created_at.desc()).all()

    rows: list[dict] = []
    for lead in leads:
        email = (
            session.query(Email)
            .filter_by(lead_id=lead.id)
            .order_by(Email.created_at.desc())
            .first()
        )
        pain = (
            session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
        )
        flags_str = ""
        if email and email.qc_flags_json:
            try:
                flags_str = "; ".join(
                    f.get("type", "") + (f" — {f['detail']}" if f.get("detail") else "")
                    for f in json.loads(email.qc_flags_json) or []
                )
            except (TypeError, ValueError):
                pass

        rows.append({
            "Lead first name": lead.first_name or "",
            "Lead last name": lead.last_name or "",
            "Lead email": lead.email or "",
            "Lead title": lead.title or "",
            "Company name": lead.company_name or "",
            "Company domain": lead.company_domain or "",
            "Industry": lead.company_industry or "",
            "Headcount": lead.company_size or "",
            "Location": lead.company_location or "",
            "Intake source": lead.intake_source or "",
            "LinkedIn URL": lead.linkedin_url or "",
            "Pain chronic": (pain.chronic if pain else "") or "",
            "Pain acute": (pain.acute if pain else "") or "",
            "Pain trigger": (pain.trigger if pain else "") or "",
            "Pain confidence": (pain.confidence if pain else "") or "",
            "Email subject": email.subject if email else "",
            "Email body": email.body if email else "",
            "Model used": (email.model_used if email else "") or "",
            "Generation seconds": (email.generation_seconds if email else "") or "",
            "Tokens used": (email.tokens_used if email else "") or "",
            "QC status": (email.qc_status if email else "") or "",
            "QC flags": flags_str,
            "Status": lead.status or "",
            "Created at": lead.created_at.isoformat() if lead.created_at else "",
            "Apollo sequence ID": (email.apollo_sequence_id if email else "") or "",
            "Apollo message ID": (email.apollo_message_id if email else "") or "",
        })
    return rows


def export_to_xlsx_bytes(rows: list[dict]) -> bytes:
    """Render rows to an in-memory .xlsx. Header row always written, even
    when rows is empty."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "leads"
    ws.append(_EXPORT_COLUMNS)
    for r in rows:
        ws.append([r.get(c, "") for c in _EXPORT_COLUMNS])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _trigger_export(
    session, campaign: Campaign, filtered: bool, status_filter: str = "all"
) -> None:
    rows = _build_export_rows(
        session, campaign.id, status_filter if filtered else "all"
    )
    payload = export_to_xlsx_bytes(rows)
    slug = re.sub(r"[^a-z0-9]+", "-", (campaign.name or "campaign").lower()).strip("-")
    fname = f"trispoke_{slug}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
    st.download_button(
        label=f"Download {fname} ({len(rows)} row{'' if len(rows)==1 else 's'})",
        data=payload,
        file_name=fname,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"dl_{fname}",
    )


def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode(errors="ignore")
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False
