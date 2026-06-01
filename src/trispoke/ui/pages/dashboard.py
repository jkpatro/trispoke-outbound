"""Dashboard — global pipeline visibility across all campaigns.

Surfaces the metrics that matter for a fully-automated pipeline:
  - lead distribution by status (the pipeline depth chart)
  - daily sends and bounce rate
  - slot pool utilization (free / in_use / disabled)
  - reply classification breakdown
  - auto-approval policy summary per campaign
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from trispoke.db.models import (
    ApolloSlot,
    Campaign,
    Lead,
    Reply,
    Send,
    SendStatus,
)
from trispoke.db.session import get_session


_PIPELINE_ORDER = [
    "new", "enriched", "drafted", "qc_pending", "qc_flagged",
    "approved", "queued_in_apollo", "sent", "replied", "bounced",
    "follow_up_pending", "closed_no_reply", "unsubscribed",
]


def render() -> None:
    # Compact one-line header (st.title + caption wasted two rows and forced a
    # scroll). The whole dashboard is tuned to fit a laptop viewport without
    # vertical scrolling.
    st.markdown(
        "##### 📊 Dashboard "
        "<span style='font-size:12px;opacity:.55;font-weight:400'>"
        "&nbsp;· live pipeline state across all campaigns</span>",
        unsafe_allow_html=True,
    )

    with get_session() as session:
        _render_kpi_row(session)

        # Row 1: pipeline depth + replies, side by side.
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            _render_pipeline_depth(session)
        with c2:
            _render_reply_breakdown(session)

        # Row 2: slot pool (narrow) + campaign policies table (wide).
        c3, c4 = st.columns([1, 2.4], gap="medium")
        with c3:
            _render_slot_pool(session)
        with c4:
            _render_campaign_policies(session)


# ---------- KPI strip ----------


def _render_kpi_row(session) -> None:
    sent_today = (
        session.query(Send)
        .filter(
            Send.status == SendStatus.sent.value,
            Send.sent_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        )
        .count()
    )
    sent_7d = (
        session.query(Send)
        .filter(
            Send.status == SendStatus.sent.value,
            Send.sent_at >= datetime.utcnow() - timedelta(days=7),
        )
        .count()
    )
    bounce_rate, recent_sample = _bounce_rate(session)
    free_slots = session.query(ApolloSlot).filter_by(status="free").count()
    total_slots = session.query(ApolloSlot).count()
    positive_7d = (
        session.query(Reply)
        .filter(
            Reply.classification == "positive",
            Reply.received_at >= datetime.utcnow() - timedelta(days=7),
        )
        .count()
    )

    cols = st.columns(5)
    cols[0].metric("Sent today", sent_today)
    cols[1].metric("Sent (7d)", sent_7d)
    cols[2].metric(
        "Bounce rate (last 100)",
        f"{bounce_rate*100:.1f}%" if recent_sample else "—",
        delta=("over threshold" if bounce_rate > 0.03 else None),
        delta_color=("inverse" if bounce_rate > 0.03 else "normal"),
    )
    cols[3].metric(
        "Slots", f"{free_slots} / {total_slots} free" if total_slots else "0 / 0"
    )
    cols[4].metric("Positive replies (7d)", positive_7d)


def _bounce_rate(session) -> tuple[float, int]:
    rows = (
        session.query(Send.status)
        .order_by(Send.created_at.desc())
        .limit(100)
        .all()
    )
    if not rows:
        return 0.0, 0
    bounces = sum(1 for (s,) in rows if s == SendStatus.bounced.value)
    return bounces / len(rows), len(rows)


# ---------- pipeline depth chart ----------


def _render_pipeline_depth(session) -> None:
    st.markdown("**Pipeline depth**")
    counts = {s: 0 for s in _PIPELINE_ORDER}
    for status, n in (
        session.query(Lead.status, _count(Lead.id))
        .group_by(Lead.status)
        .all()
    ):
        counts[status] = n
    # Compact bar chart via st.bar_chart on a DataFrame
    df = pd.DataFrame(
        {"count": [counts.get(s, 0) for s in _PIPELINE_ORDER]},
        index=_PIPELINE_ORDER,
    )
    df = df[df["count"] > 0]
    if df.empty:
        st.info("No leads yet. Start by uploading a CSV in Campaigns.")
    else:
        st.bar_chart(df, height=200)


def _count(col):
    from sqlalchemy import func
    return func.count(col)


# ---------- slot pool utilization ----------


def _render_slot_pool(session) -> None:
    st.markdown("**Apollo slot pool**")
    rows = session.query(ApolloSlot).order_by(ApolloSlot.id.asc()).all()
    if not rows:
        st.info(
            "No slot pool configured. Run "
            "`scripts/setup_apollo_slot_pool.py --count 100` then activate "
            "in Apollo."
        )
        return
    free = sum(1 for r in rows if r.status == "free")
    in_use = sum(1 for r in rows if r.status == "in_use")
    disabled = sum(1 for r in rows if r.status == "disabled")
    sub = st.columns(3)
    sub[0].metric("Free", free)
    sub[1].metric("In use", in_use)
    sub[2].metric("Disabled", disabled)
    # Show next 5 slots scheduled to free up
    in_flight = [
        r for r in rows
        if r.status == "in_use" and r.last_message_status not in
        ("completed", "failed", "bounced")
    ]
    if in_flight:
        st.caption(f"Currently in flight: {len(in_flight)} slot(s)")


# ---------- reply classification breakdown ----------


def _render_reply_breakdown(session) -> None:
    st.markdown("**Replies — last 30 days**")
    cutoff = datetime.utcnow() - timedelta(days=30)
    rows = (
        session.query(Reply.classification, _count(Reply.id))
        .filter(Reply.received_at >= cutoff)
        .group_by(Reply.classification)
        .all()
    )
    if not rows:
        st.info("No replies yet.")
        return
    df = pd.DataFrame(rows, columns=["classification", "count"]).set_index(
        "classification"
    )
    st.bar_chart(df, height=200)


# ---------- per-campaign policies + state ----------


def _render_campaign_policies(session) -> None:
    st.markdown("**Campaigns**")
    campaigns = session.query(Campaign).order_by(Campaign.created_at.desc()).all()
    if not campaigns:
        st.info("No campaigns yet.")
        return

    rows: list[dict] = []
    for c in campaigns:
        # Sent count from successful sends, used as warmup-completion gauge
        sent = (
            session.query(Send)
            .join(Lead, Lead.id != None)  # noqa
            .filter(Send.status == SendStatus.sent.value)
            .count()
        )
        try:
            settings_payload = json.loads(c.settings_json or "{}")
        except (TypeError, ValueError):
            settings_payload = {}
        sending_mode = settings_payload.get("sending_mode", "apollo")
        mode = c.auto_approve_mode or "after_warmup"
        warmup_progress = ""
        if mode == "after_warmup":
            warmup_progress = f"{min(sent, c.warmup_threshold)} / {c.warmup_threshold}"
        rows.append({
            "Campaign": c.name,
            "LLM mode": c.mode,
            "Sending": sending_mode,
            "Auto-approve": mode,
            "Warmup progress": warmup_progress,
        })

    # Capped height: the table scrolls internally instead of growing the page.
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, use_container_width=True, height=180
    )
