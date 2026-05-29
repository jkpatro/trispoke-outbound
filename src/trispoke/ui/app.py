"""trispoke-outbound — Streamlit entry point.

Run with: `uv run streamlit run src/trispoke/ui/app.py`
"""

import streamlit as st

from trispoke.db.models import Campaign
from trispoke.db.session import get_session
from trispoke.ui.utils.state import (
    get_campaign,
    get_page,
    init_state,
    set_campaign,
    set_page,
)
from trispoke.ui.utils.styling import inject_styles


# Streamlit must see set_page_config first.
st.set_page_config(
    page_title="trispoke-outbound",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_state()
inject_styles()


_NAV = [
    ("dashboard", "Dashboard"),
    ("campaigns", "Campaigns"),
    ("review", "Review queue"),
    ("insights", "Insights"),
    ("settings", "⚙ Settings"),
]


def _list_campaign_names() -> list[str]:
    with get_session() as session:
        return [
            c.name
            for c in session.query(Campaign).order_by(Campaign.created_at.desc()).all()
        ]


def _list_campaign_rows():
    with get_session() as session:
        return [
            (c.id, c.name, c.mode, c.created_at)
            for c in session.query(Campaign).order_by(Campaign.created_at.desc()).all()
        ]


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### trispoke")

        names = _list_campaign_names()
        if names:
            current = get_campaign()
            idx = names.index(current) if current in names else 0
            selected = st.selectbox("Campaign", names, index=idx)
            if selected != get_campaign():
                set_campaign(selected)
        else:
            st.caption("No campaigns yet")

        if st.button("+ New campaign", use_container_width=True):
            set_campaign(None)
            set_page("campaign_settings")
            st.rerun()

        st.divider()
        st.markdown("**Navigation**")
        for key, label in _NAV:
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                set_page(key)
                st.rerun()


def render_campaign_list() -> None:
    st.title("Campaigns")
    rows = _list_campaign_rows()
    if not rows:
        st.info("No campaigns yet. Click **+ New campaign** in the sidebar.")
        return

    for cid, name, mode, created in rows:
        with st.container(border=True):
            st.markdown(f"**{name}**")
            st.caption(f"Mode: `{mode}` • Created: {created}")
            if st.button("Open settings", key=f"open_{cid}"):
                set_campaign(name)
                set_page("campaign_settings")
                st.rerun()


def render_main() -> None:
    page = get_page()

    if page == "campaign_settings":
        from trispoke.ui.pages.campaign_settings import render

        render()
    elif page == "review":
        from trispoke.ui.pages.review_queue import render

        render()
    elif page == "lead_detail":
        from trispoke.ui.pages.lead_detail import render

        render()
    elif page == "dashboard":
        from trispoke.ui.pages.dashboard import render as _dash_render
        _dash_render()
    elif page == "settings":
        from trispoke.ui.pages.settings import render as _settings_render
        _settings_render()
    elif page == "insights":
        st.title("Insights")
        st.caption("Reply rates and classification breakdown — coming soon.")
    else:
        render_campaign_list()


render_sidebar()
render_main()
