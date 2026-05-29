"""Reusable pain-triple display (chronic / acute / trigger + confidence)."""

from __future__ import annotations

import html
from typing import Optional

import streamlit as st

from trispoke.db.models import PainAnalysis


def render_pain_panel(pain: Optional[PainAnalysis]) -> None:
    if pain is None:
        st.markdown(
            "<div class='ts-pain-panel'>"
            "<strong>Pain analysis</strong><br>"
            "<em>not yet analyzed</em>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    confidence = pain.confidence or 0.0
    if confidence >= 0.75:
        conf_cls = "high"
    elif confidence >= 0.5:
        conf_cls = "med"
    else:
        conf_cls = "low"

    chronic = html.escape(pain.chronic or "—")
    acute = html.escape(pain.acute or "—")
    trigger = html.escape(pain.trigger or "—")

    st.markdown(
        f"""
        <div class='ts-pain-panel'>
            <div style='display:flex;justify-content:space-between;align-items:center'>
                <strong>Pain analysis</strong>
                <span class='ts-conf ts-conf-{conf_cls}'>{confidence:.0%}</span>
            </div>
            <div class='ts-pain-row'><span class='ts-pain-label'>chronic</span>{chronic}</div>
            <div class='ts-pain-row'><span class='ts-pain-label'>acute</span>{acute}</div>
            <div class='ts-pain-row'><span class='ts-pain-label'>trigger</span>{trigger}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
