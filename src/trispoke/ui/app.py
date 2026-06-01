"""trispoke-outbound — Streamlit entry point.

Run with: `uv run streamlit run src/trispoke/ui/app.py`
"""

import streamlit as st

from trispoke.auth import is_admin, render_login_gate
from trispoke.db.models import Campaign
from trispoke.db.session import get_session
from trispoke.ui.components.footer import (
    render_footer,
    render_page_loader,
    render_transition_overlay,
)
from trispoke.ui.components.header import render_header
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
    page_title="Trispoke Managed Services",
    page_icon="🛰️",
    layout="wide",
    # "auto" keeps the sidebar open on desktop but collapsed (hamburger) on
    # phones, so the nav doesn't cover the whole small screen on load.
    initial_sidebar_state="auto",
)
init_state()
inject_styles()
render_page_loader()
# Armed every run: masks the rerun "double render" behind an opaque branded
# loader during page/step transitions (see footer.render_transition_overlay).
render_transition_overlay()
render_login_gate()


# (page_key, label, icon)
_NAV = [
    ("dashboard", "Dashboard", "📊"),
    ("campaigns", "Campaigns", "🗂️"),
    ("review", "Review queue", "✅"),
    ("insights", "Insights", "📈"),
]

# Admin-only entries. Filtered into the sidebar at render time.
_ADMIN_NAV = [
    ("settings", "Settings", "⚙️"),
    ("users", "Users", "👥"),
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


# Chrome CSS: hide the native sidebar entirely (we navigate via the floating
# ☰ Menu in the header now) and style the menu's nav buttons. Re-injected each
# run because Streamlit drops <style> elements on rerun.
_CHROME_CSS = """
<style>
  /* No left panel: remove the sidebar and its expand/collapse control so it
     can't flash on first paint. */
  section[data-testid="stSidebar"],
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] { display: none !important; }

  /* Reclaim the big empty band at the top: Streamlit reserves ~6rem for its
     toolbar/header, which we've hidden (toolbarMode=minimal). Collapse it and
     start the content near the top of the viewport. */
  [data-testid="stHeader"] { height: 0 !important; min-height: 0 !important;
      background: transparent !important; }
  .block-container { padding-top: 1.2rem !important; }

  /* Nav buttons inside the ☰ Menu popover — full-width, left-aligned pills. */
  [class*="st-key-navmenu_"] button {
      width: 100%;
      justify-content: flex-start; text-align: left;
      gap: 10px; padding: 9px 14px;
      border-radius: 11px; border: 1px solid transparent;
      background: transparent; color: inherit;
      font-weight: 500; font-size: 14px; box-shadow: none;
  }
  [class*="st-key-navmenu_"] button:hover {
      background: rgba(31,58,110,.09); border-color: rgba(31,58,110,.16);
  }
  /* Active page = primary -> filled navy pill (cover legacy + new testids). */
  [class*="st-key-navmenu_"] button[kind="primary"],
  [class*="st-key-navmenu_"] button[data-testid="stBaseButton-primary"] {
      background: linear-gradient(135deg,#1f3a6e,#2b5fa0) !important;
      color: #fff !important; border-color: transparent !important;
      box-shadow: 0 6px 16px rgba(31,58,110,.30) !important;
  }
  /* New-campaign action: amber accent, centered. */
  .st-key-navmenu_new_campaign button {
      background: linear-gradient(135deg,#f59e0b,#f97316) !important;
      color: #fff !important; font-weight: 600 !important;
      justify-content: center !important; text-align: center !important;
      border-color: transparent !important;
      box-shadow: 0 6px 16px rgba(245,158,11,.32) !important;
  }
  .ts-nav-label { font-size: 11px; font-weight: 700; letter-spacing: .12em;
      text-transform: uppercase; opacity: .5; margin: .4rem 0 .4rem 4px; }
</style>
"""


def _render_nav_menu() -> None:
    """Contents of the floating ☰ Menu popover: active-campaign picker, New
    campaign, and the page links. (The brand logo lives in the top bar now.)"""
    names = _list_campaign_names()
    if names:
        current = get_campaign()
        idx = names.index(current) if current in names else 0
        selected = st.selectbox(
            "Active campaign", names, index=idx, key="navmenu_active_campaign"
        )
        if selected != get_campaign():
            set_campaign(selected)
    else:
        st.caption("No campaigns yet — create your first below.")

    if st.button("➕  New campaign", use_container_width=True, key="navmenu_new_campaign"):
        set_campaign(None)
        st.session_state["campaign_wizard_step"] = 0
        st.session_state.pop("wiz_name", None)
        set_page("campaign_settings")
        st.rerun()

    st.markdown("<div class='ts-nav-label'>Menu</div>", unsafe_allow_html=True)
    current_page = get_page()
    nav_items = list(_NAV)
    if is_admin():
        nav_items.extend(_ADMIN_NAV)
    for key, label, icon in nav_items:
        active = key == current_page or (
            key == "campaigns" and current_page == "campaign_settings"
        )
        if st.button(
            f"{icon}  {label}",
            key=f"navmenu_{key}",
            use_container_width=True,
            type="primary" if active else "secondary",
        ):
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
                st.session_state["campaign_wizard_step"] = 0
                st.session_state["wiz_name"] = name
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
    elif page == "users":
        from trispoke.ui.pages.users import render as _users_render
        _users_render()
    elif page == "insights":
        st.title("Insights")
        st.caption("Reply rates and classification breakdown — coming soon.")
    else:
        render_campaign_list()


def render_topbar() -> None:
    """Top chrome: hide the native sidebar and render the header with the
    floating ☰ Menu. Falls back to a standalone menu when auth is disabled."""
    st.markdown(_CHROME_CSS, unsafe_allow_html=True)
    from trispoke.auth import is_auth_enabled

    if is_auth_enabled():
        render_header(nav_menu=_render_nav_menu)
    else:
        st.markdown("<div class='ts-hb-anchor'></div>", unsafe_allow_html=True)
        menu_col, _ = st.columns([1.3, 6])
        with menu_col, st.popover("☰  Menu", use_container_width=True):
            _render_nav_menu()


render_topbar()
render_main()
render_footer()
