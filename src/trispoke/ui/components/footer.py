"""Trispoke Managed Services Pvt. Ltd. branding — logo, banner, and footer.

All company details (links, contact, copyright) live here so the brand is
consistent across the login screen and the signed-in app. The logo is embedded
as a base64 data URI from src/trispoke/ui/assets so the app stays self-contained
(no hot-linking the company website).
"""

from __future__ import annotations

import base64
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import streamlit as st

# --- Company facts (sourced from trispokeservices.com) -----------------------
COMPANY = "Trispoke Managed Services Pvt. Ltd."
TAGLINE = "Offshore Services to Grow Your Business"
WEBSITE = "https://www.trispokeservices.com/"
LINKEDIN = "https://www.linkedin.com/company/trispoke-managed-services-pvt-ltd"
FACEBOOK = "https://www.facebook.com/p/Trispoke-Managed-Services-100093648062073/"
INSTAGRAM = "https://www.instagram.com/trispokeservices/"
EMAIL = "services@trispokeservices.com"
PHONE = "+1 630-445-1262"
ADDRESS = "H-1208 Titanium City Center, Prahladnagar, Ahmedabad"
PRIVACY = "https://www.trispokeservices.com/privacy-policy/"
TERMS = "https://www.trispokeservices.com/terms-condition/"

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "trispoke_logo.png"

# Brand colors picked from the logo.
NAVY = "#1f3a6e"


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    """Base64 data URI for the logo, or '' if the asset is missing."""
    try:
        raw = _LOGO_PATH.read_bytes()
    except Exception:
        return ""
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def logo_img_html(height_px: int = 40, *, padded: bool = True) -> str:
    """Logo <img> wrapped in a white card so the navy mark stays legible on any
    theme. Returns a brand-name text fallback if the asset can't be loaded."""
    uri = logo_data_uri()
    if not uri:
        return (
            f"<span style='font-weight:800;color:{NAVY};font-size:18px;"
            f"letter-spacing:.02em'>TRISPOKE</span>"
        )
    pad = "6px 12px" if padded else "0"
    bg = "background:#ffffff;border-radius:10px;" if padded else ""
    return (
        f"<span style='display:inline-flex;{bg}padding:{pad};"
        f"box-shadow:0 1px 4px rgba(0,0,0,.10) inset, 0 1px 2px rgba(0,0,0,.06)'>"
        f"<img src='{uri}' alt='{COMPANY}' "
        f"style='height:{height_px}px;display:block' /></span>"
    )


# --- Social icons (inline SVG, brand circles) --------------------------------

_LI_SVG = (
    "<svg viewBox='0 0 24 24' width='18' height='18' fill='currentColor'>"
    "<path d='M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 9h4v12H3zM9 9h3.8v1.7h.05c.53-.95 1.83-1.95 "
    "3.77-1.95 4.03 0 4.78 2.65 4.78 6.1V21h-4v-5.4c0-1.29-.02-2.95-1.8-2.95-1.8 0-2.08 1.4-2.08 "
    "2.85V21H9z'/></svg>"
)
_FB_SVG = (
    "<svg viewBox='0 0 24 24' width='18' height='18' fill='currentColor'>"
    "<path d='M22 12a10 10 0 1 0-11.56 9.88v-6.99H7.9V12h2.54V9.8c0-2.5 1.49-3.89 3.78-3.89 "
    "1.09 0 2.24.2 2.24.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56V12h2.78l-.44 2.89h-2.34v6.99A10 10 0 0 0 22 12z'/></svg>"
)
_IG_SVG = (
    "<svg viewBox='0 0 24 24' width='18' height='18' fill='currentColor'>"
    "<path d='M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 "
    "1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 "
    "2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 "
    "3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 "
    "12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 "
    "1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zm0 1.8c-3.15 0-3.5.01-4.74.07-1.14.05-1.76.24-2.17.4-.55.21-.94.47-1.35.88-.41.41-.67.8-.88 "
    "1.35-.16.41-.35 1.03-.4 2.17-.06 1.24-.07 1.59-.07 4.74s.01 3.5.07 4.74c.05 1.14.24 1.76.4 "
    "2.17.21.55.47.94.88 1.35.41.41.8.67 1.35.88.41.16 1.03.35 2.17.4 1.24.06 1.59.07 4.74.07s3.5-.01 "
    "4.74-.07c1.14-.05 1.76-.24 2.17-.4.55-.21.94-.47 1.35-.88.41-.41.67-.8.88-1.35.16-.41.35-1.03.4-2.17.06-1.24.07-1.59.07-4.74s-.01-3.5-.07-4.74c-.05-1.14-.24-1.76-.4-2.17a3.6 "
    "3.6 0 0 0-.88-1.35 3.6 3.6 0 0 0-1.35-.88c-.41-.16-1.03-.35-2.17-.4-1.24-.06-1.59-.07-4.74-.07zm0 "
    "3.06a5.98 5.98 0 1 1 0 11.96 5.98 5.98 0 0 1 0-11.96zm0 1.8a4.18 4.18 0 1 0 0 8.36 4.18 4.18 0 0 0 "
    "0-8.36zm6.2-1.45a1.4 1.4 0 1 1-2.8 0 1.4 1.4 0 0 1 2.8 0z'/></svg>"
)


def _social_link(href: str, svg: str, label: str) -> str:
    return (
        f"<a href='{href}' target='_blank' rel='noopener' title='{label}' "
        f"aria-label='{label}' class='ts-foot-social'>{svg}</a>"
    )


def _ff_social(href: str, svg: str, label: str) -> str:
    return (
        f"<a href='{href}' target='_blank' rel='noopener' title='{label}' "
        f"aria-label='{label}' class='ts-ff-social'>{svg}</a>"
    )


def render_footer() -> None:
    """Slim, fixed, full-width footer bar pinned to the bottom of the viewport
    with centered content. Rendered on every page; the matching bottom padding
    keeps page/sidebar content from hiding behind it."""
    year = datetime.now().year
    uri = logo_data_uri()
    logo = (
        f"<img class='ts-ff-logo' src='{uri}' alt='{COMPANY}' />"
        if uri
        else "<span style='font-weight:800;color:#fff'>TRISPOKE</span>"
    )
    socials = (
        _ff_social(LINKEDIN, _LI_SVG, "LinkedIn")
        + _ff_social(FACEBOOK, _FB_SVG, "Facebook")
        + _ff_social(INSTAGRAM, _IG_SVG, "Instagram")
    )
    st.markdown(
        f"""
        <style>
          /* Keep content clear of the fixed bar. */
          .block-container {{ padding-bottom: 64px !important; }}
          section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
          section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-bottom: 60px; }}

          .ts-footer-bar {{
              position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000;
              min-height: 44px;
              display: flex; align-items: center; justify-content: center;
              flex-wrap: wrap; gap: 6px 16px;
              padding: 5px 18px;
              background: linear-gradient(90deg, #16284c 0%, #1f3a6e 50%, #16284c 100%);
              border-top: 1px solid rgba(255,255,255,.10);
              box-shadow: 0 -4px 18px rgba(0,0,0,.20);
              color: #dfe7f3; font-size: 12.5px; line-height: 1;
          }}
          .ts-footer-bar .ts-ff-logo {{
              height: 22px; display: block; background: #fff;
              border-radius: 6px; padding: 3px 7px;
          }}
          .ts-footer-bar a {{ color: #dfe7f3; text-decoration: none; }}
          .ts-footer-bar a:hover {{ color: #fff; }}
          .ts-ff-copy {{ opacity: .9; }}
          .ts-ff-sep {{ opacity: .35; }}
          .ts-ff-social {{
              display: inline-flex; align-items: center; justify-content: center;
              width: 26px; height: 26px; border-radius: 50%; margin-left: 5px;
              background: rgba(255,255,255,.13); color: #fff;
              transition: background .15s ease, transform .12s ease;
          }}
          .ts-ff-social:hover {{ background: #f59e0b; transform: translateY(-1px); }}
          .ts-ff-social svg {{ width: 14px; height: 14px; }}
          @media (max-width: 620px) {{
              .ts-ff-hide-sm {{ display: none; }}
              .ts-footer-bar {{ font-size: 11px; gap: 3px 10px; padding: 5px 10px; }}
              .ts-footer-bar .ts-ff-logo {{ height: 18px; padding: 2px 5px; }}
              .ts-ff-social {{ width: 24px; height: 24px; margin-left: 4px; }}
              /* Extra bottom room in case the bar wraps to two lines on phones. */
              .block-container {{ padding-bottom: 88px !important; }}
          }}
        </style>
        <div class="ts-footer-bar">
          {logo}
          <span class="ts-ff-copy">© {year} {COMPANY} · All rights reserved.</span>
          <span class="ts-ff-sep ts-ff-hide-sm">|</span>
          <a class="ts-ff-hide-sm" href="{WEBSITE}" target="_blank" rel="noopener">trispokeservices.com</a>
          <span>{socials}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Branded page loader (splash)
# ---------------------------------------------------------------------------

_SPLASH_SHOWN_KEY = "_ts_splash_shown"


def render_page_loader() -> None:
    """Full-screen branded splash shown once per page load.

    Rendered before the rest of the app on the first run of a session (i.e. on
    initial load / hard refresh), so it also masks Streamlit's first-paint
    flashes (e.g. the sidebar appearing then hiding on the login screen). It
    fades itself out via CSS after a beat; on later reruns it isn't re-emitted,
    so in-app interactions don't replay it.
    """
    if st.session_state.get(_SPLASH_SHOWN_KEY):
        return
    st.session_state[_SPLASH_SHOWN_KEY] = True

    uri = logo_data_uri()
    mark = (
        f"<img src='{uri}' class='ts-splash-logo' alt='{COMPANY}' />"
        if uri
        else "<div class='ts-splash-word'>TRISPOKE</div>"
    )
    st.markdown(
        f"""
        <style>
          @keyframes tsRing {{ to {{ transform: rotate(360deg); }} }}
          @keyframes tsLogoIn {{
              0% {{ opacity: 0; transform: scale(.82); }}
              60% {{ opacity: 1; transform: scale(1.04); }}
              100% {{ opacity: 1; transform: scale(1); }}
          }}
          @keyframes tsBob {{ 0%,100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-5px); }} }}
          @keyframes tsSplashOut {{
              to {{ opacity: 0; visibility: hidden; pointer-events: none; }}
          }}
          @keyframes tsBar {{ 0% {{ left: -40%; }} 100% {{ left: 100%; }} }}

          .ts-splash {{
              position: fixed; inset: 0; z-index: 99999;
              display: flex; flex-direction: column;
              align-items: center; justify-content: center; gap: 26px;
              background:
                radial-gradient(circle at 30% 25%, rgba(43,95,160,.16), transparent 55%),
                radial-gradient(circle at 75% 80%, rgba(245,158,11,.14), transparent 55%),
                #ffffff;
              animation: tsSplashOut .55s ease 1.45s forwards;
          }}
          @media (prefers-color-scheme: dark) {{
              .ts-splash {{
                  background:
                    radial-gradient(circle at 30% 25%, rgba(43,95,160,.28), transparent 55%),
                    radial-gradient(circle at 75% 80%, rgba(245,158,11,.20), transparent 55%),
                    #0d1117;
              }}
          }}
          .ts-splash-logo {{ width: 240px; max-width: 68vw;
              animation: tsLogoIn .75s cubic-bezier(.2,.8,.2,1) both,
                         tsBob 2.6s ease-in-out .75s infinite;
              filter: drop-shadow(0 10px 24px rgba(31,58,110,.18)); }}
          .ts-splash-word {{ font-weight: 800; font-size: 30px; color: #1f3a6e;
              letter-spacing: .06em; animation: tsLogoIn .75s ease both; }}
          .ts-splash-spinner {{
              width: 52px; height: 52px; border-radius: 50%;
              background: conic-gradient(from 0deg, #1f3a6e, #f59e0b, #14b8a6, #6366f1, #1f3a6e);
              -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 6px));
                      mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 6px));
              animation: tsRing 1s linear infinite;
              filter: drop-shadow(0 4px 12px rgba(31,58,110,.28));
          }}
          .ts-splash-cap {{ font-size: 12px; letter-spacing: .18em; text-transform: uppercase;
              color: #1f3a6e; opacity: .6; }}
        </style>
        <div class="ts-splash">
          {mark}
          <div class="ts-splash-spinner"></div>
          <div class="ts-splash-cap">Loading your workspace…</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page-to-page transition overlay
# ---------------------------------------------------------------------------


def render_transition_overlay() -> None:
    """A prominent, opaque loader that covers the screen during page/step changes.

    Streamlit re-runs the whole script on every interaction. While it does, it
    keeps the *previous* run's elements on screen (marked ``data-stale="true"``)
    until the new ones mount — so for a beat you see both the old and new buttons
    overlapping ("buttons appear twice") and the swap looks janky.

    This overlay reacts to ``data-stale`` purely in CSS (no Python round-trip), so
    it appears the instant a rerun begins and masks that overlap behind a clean,
    fully-opaque branded loader.

    It is driven by a one-shot *animation* (fade in → hold → auto fade out by
    ~0.9s), not a persistent visible state. That matters: long synchronous work
    (e.g. "Save & generate" with its ``st.status`` progress) keeps elements stale
    for many seconds — a persistent overlay would hide that progress, but the
    one-shot animation gets out of the way after the initial transition. Emitted
    on every run (unlike the once-only splash) so it's always armed.
    """
    uri = logo_data_uri()
    mark = (
        f"<img src='{uri}' class='ts-xover-logo' alt='{COMPANY}' />"
        if uri
        else "<div class='ts-xover-word'>TRISPOKE</div>"
    )
    st.markdown(
        f"""
        <style>
          @keyframes tsXoverRing {{ to {{ transform: rotate(360deg); }} }}
          /* fade in fast (~120ms), hold, then auto fade out — even if the run
             keeps elements stale for seconds afterwards. */
          @keyframes tsXoverCycle {{
              0%   {{ opacity: 0; visibility: visible; }}
              14%  {{ opacity: 1; }}
              68%  {{ opacity: 1; }}
              100% {{ opacity: 0; visibility: hidden; }}
          }}
          .ts-xover {{
              position: fixed; inset: 0; z-index: 99990;
              display: flex; align-items: center; justify-content: center;
              flex-direction: column; gap: 22px;
              background:
                radial-gradient(circle at 30% 25%, rgba(43,95,160,.16), transparent 55%),
                radial-gradient(circle at 75% 80%, rgba(245,158,11,.14), transparent 55%),
                #ffffff;
              opacity: 0; visibility: hidden; pointer-events: none;
              transition: opacity .18s ease;
          }}
          @media (prefers-color-scheme: dark) {{
              .ts-xover {{
                  background:
                    radial-gradient(circle at 30% 25%, rgba(43,95,160,.28), transparent 55%),
                    radial-gradient(circle at 75% 80%, rgba(245,158,11,.20), transparent 55%),
                    #0d1117;
              }}
          }}
          /* The trigger: any stale element anywhere in the app = a rerun is in
             flight. Runs the cycle once (forwards holds the hidden end-state). */
          [data-testid="stApp"]:has([data-stale="true"]) .ts-xover {{
              animation: tsXoverCycle 900ms ease forwards;
          }}
          .ts-xover-logo {{ width: 168px; max-width: 58vw;
              filter: drop-shadow(0 8px 20px rgba(31,58,110,.18)); }}
          .ts-xover-word {{ font-weight: 800; font-size: 26px; color: #1f3a6e;
              letter-spacing: .06em; }}
          .ts-xover-spin {{
              width: 44px; height: 44px; border-radius: 50%;
              background: conic-gradient(from 0deg, #1f3a6e, #f59e0b, #14b8a6, #6366f1, #1f3a6e);
              -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 5px));
                      mask: radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 5px));
              animation: tsXoverRing 1s linear infinite;
              filter: drop-shadow(0 4px 12px rgba(31,58,110,.28));
          }}
        </style>
        <div class="ts-xover" aria-hidden="true">
          {mark}
          <div class="ts-xover-spin"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
