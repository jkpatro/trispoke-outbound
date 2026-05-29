"""Lead detail — wireframe 3."""

from __future__ import annotations

import streamlit as st

from trispoke.db.event_log import log_event
from trispoke.db.models import (
    Campaign,
    Email,
    Lead,
    LeadStatus,
    PainAnalysis,
)
from trispoke.db.session import get_session
from trispoke.ui.components.email_card import (
    CardEdits,
    EnhanceRequest,
    html_to_plain,
    render_email_card,
)
from trispoke.ui.components.pain_panel import render_pain_panel
from trispoke.ui.utils.state import set_page


# Spec'd Anthropic per-token prices for the cost estimate.
SONNET_INPUT_PRICE_PER_M = 3.0
SONNET_OUTPUT_PRICE_PER_M = 15.0
ASSUMED_INPUT_TOKENS = 600
ASSUMED_OUTPUT_TOKENS = 300


def _estimate_claude_cost() -> float:
    return (
        ASSUMED_INPUT_TOKENS * SONNET_INPUT_PRICE_PER_M
        + ASSUMED_OUTPUT_TOKENS * SONNET_OUTPUT_PRICE_PER_M
    ) / 1_000_000


def _configured_engines() -> list[tuple[str, str, str]]:
    """Return [(mode_value, ui_label, cost_str)] for every engine usable right now.

    Abacus is filtered out when ABACUS_API_KEY is unset so we never offer a
    reroll target that will fail on click.
    """
    from trispoke.config import get_settings

    s = get_settings()
    engines: list[tuple[str, str, str]] = [
        ("local_only", "Local (Ollama)", "free"),
    ]
    if s.anthropic_api_key:
        engines.append(
            ("claude_only", "Claude Sonnet", f"~${_estimate_claude_cost():.4f}")
        )
    if s.abacus_api_key:
        engines.append(("abacus_only", "Abacus RouteLLM", "subscription"))
    return engines


def _current_engine(model_used: str) -> str:
    """Map a stored model_used string to one of the engine mode values."""
    if model_used.startswith("claude:") or "claude" in model_used:
        return "claude_only"
    if model_used.startswith("abacus:"):
        return "abacus_only"
    return "local_only"


def render() -> None:
    lead_id = st.session_state.get("selected_lead_id")
    if lead_id is None:
        st.info("No lead selected. Go to the review queue and click **Review**.")
        return

    with get_session() as session:
        lead = session.query(Lead).get(lead_id)
        if lead is None:
            st.warning(f"Lead {lead_id} not found.")
            return

        campaign = session.query(Campaign).get(lead.campaign_id)

        siblings = (
            session.query(Lead.id)
            .filter(
                Lead.campaign_id == lead.campaign_id,
                Lead.status == lead.status,
            )
            .order_by(Lead.created_at.asc())
            .all()
        )
        sibling_ids = [r[0] for r in siblings]
        try:
            current_index = sibling_ids.index(lead_id)
        except ValueError:
            current_index = -1

        _render_header(campaign, lead, current_index, sibling_ids)

        st.divider()
        _render_lead_card(lead)

        st.divider()
        pain = (
            session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
        )
        render_pain_panel(pain)

        st.divider()
        email = (
            session.query(Email)
            .filter_by(lead_id=lead.id)
            .order_by(Email.created_at.desc())
            .first()
        )
        if email is None:
            st.warning("No draft yet for this lead.")
            return

        _render_qc_panel(email)

        edits = render_email_card(email)

        if edits.enhance_request is not None:
            _apply_enhance(session, lead, email, edits)
            st.rerun()

        st.divider()
        _render_action_row(session, lead, email, edits)


def _render_header(
    campaign: Campaign,
    lead: Lead,
    current_index: int,
    sibling_ids: list[int],
) -> None:
    cols = st.columns([4, 1, 1, 1])
    with cols[0]:
        st.markdown(
            f"<div class='ts-breadcrumb'>"
            f"Campaign / {campaign.name} / Review queue"
            f"</div>",
            unsafe_allow_html=True,
        )
        name = (
            f"{lead.first_name or ''} {lead.last_name or ''}".strip()
            or (lead.email or "(no name)")
        )
        st.title(name)

    with cols[1]:
        st.write("")
        if current_index > 0 and st.button("◀ Prev", use_container_width=True):
            st.session_state["selected_lead_id"] = sibling_ids[current_index - 1]
            st.session_state.pop("edit_mode_lead_id", None)
            st.rerun()

    with cols[2]:
        st.write("")
        if (
            0 <= current_index < len(sibling_ids) - 1
            and st.button("Next ▶", use_container_width=True)
        ):
            st.session_state["selected_lead_id"] = sibling_ids[current_index + 1]
            st.session_state.pop("edit_mode_lead_id", None)
            st.rerun()

    with cols[3]:
        st.write("")
        if st.button("← Queue", use_container_width=True):
            set_page("review")
            st.rerun()

    if current_index >= 0 and sibling_ids:
        st.caption(f"{current_index + 1} of {len(sibling_ids)} {lead.status}")


def _render_lead_card(lead: Lead) -> None:
    initials = _initials(lead)
    name = (
        f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        or (lead.email or "?")
    )

    with st.container(border=True):
        head = st.columns([1, 4, 1])
        with head[0]:
            st.markdown(
                f"<div class='ts-avatar ts-avatar-lg'>{initials}</div>",
                unsafe_allow_html=True,
            )
        with head[1]:
            st.markdown(f"### {name}")
            role_co = f"{lead.title or '—'} · {lead.company_name or '—'}"
            st.caption(role_co)
        with head[2]:
            if lead.linkedin_url:
                st.markdown(f"[linkedin ↗]({lead.linkedin_url})")

        grid = st.columns(2)
        grid[0].markdown(f"**Email**\n\n{lead.email or '—'}")
        grid[1].markdown(f"**Industry**\n\n{lead.company_industry or '—'}")
        grid[0].markdown(f"**Headcount**\n\n{lead.company_size or '—'}")
        grid[1].markdown(
            f"**Specializes in**\n\n{lead.company_industry or '—'}"
        )


def _render_action_row(session, lead: Lead, email: Email, edits: CardEdits) -> None:
    from trispoke.llm.qc_checker import flags_from_json, has_errors

    current = _current_engine(email.model_used or "")
    engines = _configured_engines()
    # Reroll targets = everything except whatever produced the current draft.
    targets = [(m, l, c) for (m, l, c) in engines if m != current]

    qc_flags = flags_from_json(email.qc_flags_json)
    qc_blocks = has_errors(qc_flags)
    override_key = f"qc_override_{email.id}"
    override_active = st.session_state.get(override_key, False)
    approve_blocked = qc_blocks and not override_active

    cols = st.columns([2, 2, 2, 3, 1])

    if cols[0].button(
        "✓ Approve & Send Now",
        type="primary",
        use_container_width=True,
        disabled=approve_blocked,
        help=(
            "Resolve QC errors first, or click 'Override QC' below."
            if approve_blocked
            else "Bypasses Apollo's slot schedule — fires within seconds."
        ),
    ):
        _persist_manual_edits_if_any(session, lead, email, edits)
        email.send_mode = "now"
        lead.status = LeadStatus.approved.value
        session.commit()
        log_event(session, lead.id, "email_approved", {
            "email_id": email.id, "qc_override": override_active, "send_mode": "now",
        })
        st.session_state.pop(override_key, None)
        st.success("Approved · will send immediately on next push cycle.")
        st.rerun()

    if cols[1].button(
        "⏰ Approve & Schedule",
        use_container_width=True,
        disabled=approve_blocked,
        help=(
            "Resolve QC errors first, or click 'Override QC' below."
            if approve_blocked
            else "Fires at the next valid window of the slot's Apollo schedule."
        ),
    ):
        _persist_manual_edits_if_any(session, lead, email, edits)
        email.send_mode = "scheduled"
        lead.status = LeadStatus.approved.value
        session.commit()
        log_event(session, lead.id, "email_approved", {
            "email_id": email.id, "qc_override": override_active, "send_mode": "scheduled",
        })
        st.session_state.pop(override_key, None)
        st.success("Approved · will send at the slot's next scheduled window.")
        st.rerun()

    if cols[2].button("💾 Save edits", use_container_width=True):
        saved = _persist_manual_edits_if_any(session, lead, email, edits)
        if saved:
            st.success("Edits saved as a new draft revision.")
            st.rerun()
        else:
            st.info("No changes to save.")

    with cols[3]:
        if not targets:
            st.button(
                "↺ Re-roll (no other engine configured)",
                use_container_width=True,
                disabled=True,
            )
        else:
            labels = [f"{l} · {c}" for (_, l, c) in targets]
            sub_cols = st.columns([3, 2])
            picked_label = sub_cols[0].selectbox(
                "Re-roll engine",
                labels,
                key=f"reroll_pick_{email.id}",
                label_visibility="collapsed",
            )
            picked = targets[labels.index(picked_label)]
            if sub_cols[1].button(
                "↺ Re-roll", use_container_width=True, key=f"reroll_btn_{email.id}"
            ):
                _reroll_with(session, lead, email, picked[0])
                st.rerun()

    with cols[4]:
        st.markdown(
            "<div style='text-align:right;color:#dc2626'>", unsafe_allow_html=True
        )
        if st.button("Reject", key="reject_btn", use_container_width=True):
            lead.status = LeadStatus.closed_no_reply.value
            session.commit()
            log_event(session, lead.id, "email_rejected", {"email_id": email.id})
            st.warning("Rejected.")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


def _persist_manual_edits_if_any(
    session, lead: Lead, email: Email, edits: CardEdits
) -> bool:
    """If the reviewer typed changes, insert a new draft revision. Return True if persisted."""
    new_subject = (edits.subject or "").strip()
    new_body = html_to_plain(edits.body_html or "")
    if new_subject == (email.subject or "") and new_body == (email.body or ""):
        return False

    new_email = Email(
        lead_id=lead.id,
        campaign_id=lead.campaign_id,
        subject=new_subject,
        body=new_body,
        model_used="human_edit",
        tokens_used=0,
        generation_seconds=0.0,
        parent_email_id=email.id,
    )
    session.add(new_email)
    session.commit()
    log_event(
        session,
        lead.id,
        "email_edited",
        {"email_id": new_email.id, "parent_email_id": email.id},
    )
    return True


def _apply_enhance(
    session, lead: Lead, email: Email, edits: CardEdits
) -> None:
    """Run the enhance request through the router and save as a new draft revision."""
    from trispoke.llm.router import LLMRouter

    req: EnhanceRequest = edits.enhance_request  # type: ignore[assignment]

    # First, persist any pending manual edits so the enhance operates on what
    # the reviewer actually sees on screen — not the stale DB row.
    base_subject = (edits.subject or "").strip() or (email.subject or "")
    base_body = html_to_plain(edits.body_html or "") or (email.body or "")

    with st.spinner(f"Enhancing with {req.mode} · {req.style}…"):
        try:
            router = LLMRouter()
            draft = router.enhance_email(
                current_subject=base_subject,
                current_body=base_body,
                instruction=req.instruction,
                mode=req.mode,
                style=req.style,
            )
        except Exception as e:
            st.error(f"Enhance failed: {e}")
            return

    new_email = Email(
        lead_id=lead.id,
        campaign_id=lead.campaign_id,
        subject=draft.subject,
        body=draft.body,
        model_used=draft.model_used,
        tokens_used=draft.tokens_used,
        generation_seconds=draft.generation_seconds,
        parent_email_id=email.id,
    )
    session.add(new_email)
    session.commit()
    log_event(
        session,
        lead.id,
        "email_generated",
        {
            "email_id": new_email.id,
            "parent_email_id": email.id,
            "type": "enhance",
            "instruction": req.instruction,
            "mode": req.mode,
            "style": req.style,
            "model_used": draft.model_used,
            "tokens_used": draft.tokens_used,
        },
    )
    st.success(f"Enhanced by {draft.model_used} ({draft.generation_seconds:.1f}s).")


def _reroll_with(
    session, lead: Lead, current_email: Email, new_mode: str
) -> None:
    from trispoke.llm.router import LLMRouter
    from trispoke.pain_analyzer import PainTriple, analyze

    pain_record = (
        session.query(PainAnalysis).filter_by(lead_id=lead.id).first()
    )
    if pain_record is None:
        pain = analyze(lead)
    else:
        pain = PainTriple(
            chronic=pain_record.chronic,
            acute=pain_record.acute,
            trigger=pain_record.trigger,
            confidence=pain_record.confidence or 0.0,
        )

    with st.spinner(f"Re-rolling with {new_mode}…"):
        try:
            router = LLMRouter()
            draft = router.generate_email(lead, pain, new_mode)  # type: ignore[arg-type]
        except Exception as e:
            st.error(f"Re-roll failed: {e}")
            return

    new_email = Email(
        lead_id=lead.id,
        campaign_id=lead.campaign_id,
        subject=draft.subject,
        body=draft.body,
        model_used=draft.model_used,
        tokens_used=draft.tokens_used,
        generation_seconds=draft.generation_seconds,
        parent_email_id=current_email.id,
    )
    session.add(new_email)
    session.commit()
    log_event(
        session,
        lead.id,
        "email_generated",
        {
            "email_id": new_email.id,
            "parent_email_id": current_email.id,
            "type": "reroll",
            "model_used": draft.model_used,
            "tokens_used": draft.tokens_used,
        },
    )
    st.success(f"New draft generated by {draft.model_used}.")


def _render_qc_panel(email: Email) -> None:
    """V1.5 QC warnings panel. Errors red, warnings amber. Shown above the card."""
    from trispoke.llm.qc_checker import QCFlag, flags_from_json, has_errors

    flags = flags_from_json(email.qc_flags_json)
    if not flags:
        return
    errors = [f for f in flags if f.severity == "error"]
    warnings = [f for f in flags if f.severity == "warning"]

    border = "#dc2626" if errors else "#f59e0b"
    bg = "#fef2f2" if errors else "#fffbeb"
    title_color = "#991b1b" if errors else "#92400e"
    icon = "🚫" if errors else "⚠"

    def _line(f: QCFlag, color: str) -> str:
        detail = f" — {f.detail}" if f.detail else ""
        return (
            f"<div style='margin:4px 0;color:{color}'>"
            f"<strong>{f.type}</strong>{detail}"
            f"</div>"
        )

    parts = [
        f"<div style='background:{bg};border:1px solid {border};"
        f"border-radius:8px;padding:12px 14px;color:{title_color};margin-bottom:8px'>",
        f"<div style='font-weight:600;font-size:13px;text-transform:uppercase;"
        f"letter-spacing:0.5px'>{icon} QC findings — "
        f"{len(errors)} error(s), {len(warnings)} warning(s)</div>",
    ]
    for f in errors:
        parts.append(_line(f, "#991b1b"))
    for f in warnings:
        parts.append(_line(f, "#92400e"))
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)

    # Override toggle — exposed inline so the reviewer doesn't have to scroll.
    if errors:
        override_key = f"qc_override_{email.id}"
        st.checkbox(
            "Override QC and allow approval anyway",
            key=override_key,
            help="Use only if you've manually verified the flagged issues are false positives.",
        )


def _initials(lead: Lead) -> str:
    f = (lead.first_name or "").strip()
    l = (lead.last_name or "").strip()
    if f and l:
        return (f[0] + l[0]).upper()
    if f:
        return f[:2].upper()
    return (lead.email or "??")[:2].upper()
