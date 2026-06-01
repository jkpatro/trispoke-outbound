"""Editable email card: subject + WYSIWYG body + AI enhance panel.

Always rendered in edit mode. Changes are persisted by the parent page via
the callbacks returned by `render_email_card`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

import streamlit as st
from streamlit_quill import st_quill

from trispoke.db.models import Email
from trispoke.secrets_store import secret
from trispoke.ui.utils.styling import badge


@dataclass
class CardEdits:
    """What the user has typed/generated this turn — handed back to the page."""

    subject: str
    body_html: str
    enhance_request: Optional["EnhanceRequest"] = None


@dataclass
class EnhanceRequest:
    instruction: str
    mode: Literal["local_only", "claude_only", "abacus_only"]
    style: Literal["few_shot", "thinking"]


def render_email_card(email: Email, *, key_prefix: str = "card") -> CardEdits:
    """Render an editable email card. Returns the user's pending edits.

    The card always shows:
      - editable Subject (st.text_input)
      - editable Body (streamlit-quill WYSIWYG)
      - AI enhance panel (instruction + model + style + Enhance button)

    The parent page is responsible for persisting the returned edits when the
    user clicks Approve / Save / Reroll.
    """
    model = email.model_used or ""
    # New-format provider:model strings take priority; legacy strings still work.
    if model.startswith("abacus:") or model.startswith("abacus "):
        kind, label = "abacus", model
    elif model.startswith("claude:") or "claude" in model:
        kind, label = "claude", model
    elif model == "human_edit":
        kind, label = "reachable", "human edit"
    elif model.startswith("local:") or model.startswith("template:"):
        kind, label = "local", model
    elif "+enhance" in model:
        kind, label = "recommended", model
    else:
        kind, label = "local", model or "local"

    with st.container(border=True):
        cols = st.columns([5, 1])
        with cols[0]:
            st.caption("SUBJECT")
            new_subject = st.text_input(
                "Subject",
                value=email.subject or "",
                label_visibility="collapsed",
                key=f"{key_prefix}_subject_{email.id}",
            )
        with cols[1]:
            st.markdown(
                f"<div style='text-align:right'>{badge(label, kind)}</div>",
                unsafe_allow_html=True,
            )

        st.caption("BODY")
        st.markdown("<div class='ts-quill'>", unsafe_allow_html=True)
        new_body_html = st_quill(
            value=_plain_to_html(email.body or ""),
            html=True,
            toolbar=[
                ["bold", "italic", "underline"],
                [{"list": "ordered"}, {"list": "bullet"}],
                ["link"],
                ["clean"],
            ],
            key=f"{key_prefix}_body_{email.id}",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        # First render returns None; treat it as "no change".
        new_body_html = new_body_html if new_body_html is not None else _plain_to_html(
            email.body or ""
        )

        st.divider()
        enhance = _render_enhance_panel(email.id, key_prefix=key_prefix)

        st.divider()
        m_cols = st.columns(3)
        m_cols[0].caption(f"Model: `{model or '—'}`")
        m_cols[1].caption(f"Tokens: {email.tokens_used or 0}")
        gen_s = email.generation_seconds or 0.0
        m_cols[2].caption(f"Generated in: {gen_s:.1f}s")

    return CardEdits(
        subject=new_subject,
        body_html=new_body_html,
        enhance_request=enhance,
    )


def _render_enhance_panel(email_id: int, *, key_prefix: str) -> Optional[EnhanceRequest]:
    st.markdown(
        "<div class='ts-enhance-panel'><h4>Enhance with AI</h4></div>",
        unsafe_allow_html=True,
    )
    instruction = st.text_area(
        "What would you like to change?",
        placeholder="e.g. shorter and more direct · drop the sales pitch · "
        "lead with a question · mention their recent fundraise",
        key=f"{key_prefix}_enh_instr_{email_id}",
        height=80,
    )
    # Only show engines whose key is configured — avoid offering a click that
    # will fail. Abacus appears only if ABACUS_API_KEY is set.
    from trispoke.config import get_settings

    s = get_settings()
    engine_options = ["Local (qwen3)"]
    if secret("ANTHROPIC_API_KEY", s.anthropic_api_key):
        engine_options.append("Claude")
    if secret("ABACUS_API_KEY", s.abacus_api_key):
        engine_options.append("Abacus")

    cols = st.columns([2, 2, 2])
    mode_label = cols[0].radio(
        "Model",
        engine_options,
        horizontal=False,
        key=f"{key_prefix}_enh_mode_{email_id}",
    )
    style_label = cols[1].radio(
        "Style",
        ["Few-shot", "Thinking"],
        horizontal=False,
        key=f"{key_prefix}_enh_style_{email_id}",
        help=(
            "Few-shot: model is shown short before/after examples (faster, "
            "more consistent). Thinking: model reasons briefly before rewriting "
            "(slower, better for subtle instructions)."
        ),
    )
    with cols[2]:
        st.write("")
        clicked = st.button(
            "✨ Enhance",
            key=f"{key_prefix}_enh_btn_{email_id}",
            use_container_width=True,
            type="primary",
            disabled=not instruction.strip(),
        )

    if not clicked:
        return None
    mode_map = {
        "Local (qwen3)": "local_only",
        "Claude": "claude_only",
        "Abacus": "abacus_only",
    }
    return EnhanceRequest(
        instruction=instruction.strip(),
        mode=mode_map.get(mode_label, "local_only"),
        style="thinking" if style_label == "Thinking" else "few_shot",
    )


# ---------- helpers ----------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def html_to_plain(html_str: str) -> str:
    """Cheap HTML → plain-text for storing/sending. Preserves paragraph + line breaks."""
    if not html_str:
        return ""
    s = html_str
    s = re.sub(r"</p>\s*<p[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = _HTML_TAG_RE.sub("", s)
    # Decode the handful of entities Quill emits.
    s = (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return s.strip()


def _plain_to_html(plain: str) -> str:
    """Wrap plain text in <p> tags so Quill renders paragraphs correctly."""
    if not plain:
        return ""
    paras = [p.strip() for p in plain.split("\n\n") if p.strip()]
    return "".join(
        "<p>" + para.replace("\n", "<br>") + "</p>" for para in paras
    )
