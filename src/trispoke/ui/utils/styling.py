import streamlit as st


_CSS = """
<style>
/* Scoped only to our `ts-*` classes — never touch Streamlit's own selectors
   (those vary across versions and break dark-mode / mobile layouts).  */
.ts-card {
    background: #ffffff;
    color: #111827;
    border: 0.5px solid #e5e7eb;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}
.ts-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    line-height: 16px;
    vertical-align: middle;
    margin-left: 6px;
}
.ts-badge-recommended {
    background: #eef4ff;
    color: #1e40af;
    border: 0.5px solid #3b82f6;
}
.ts-badge-verified {
    background: #ecfdf5;
    color: #065f46;
    border: 0.5px solid #10b981;
}
.ts-badge-reachable {
    background: #f3f4f6;
    color: #1f2937;
    border: 0.5px solid #d1d5db;
}
.ts-amber {
    background: #fffbeb;
    border: 0.5px solid #f59e0b;
    border-radius: 8px;
    padding: 12px 14px;
    color: #78350f;
    font-size: 13px;
}
.ts-inbox-row {
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 0.5px solid #f1f5f9;
    font-size: 14px;
}
.ts-badge-local {
    background: #fff1ee;
    color: #c2410c;
    border: 0.5px solid #fb923c;
}
.ts-badge-claude {
    background: #eff6ff;
    color: #1d4ed8;
    border: 0.5px solid #60a5fa;
}
.ts-badge-abacus {
    background: #f5edff;
    color: #7B3AB7;
    border: 0.5px solid #a78bfa;
}
.ts-pain-panel {
    background: #fdf2f8;
    border: 0.5px solid #f9a8d4;
    border-radius: 8px;
    padding: 16px;
    color: #831843;
    margin-bottom: 12px;
}
.ts-pain-row { margin-top: 8px; }
.ts-pain-label {
    display: inline-block;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
    margin-right: 8px;
    color: #be185d;
}
.ts-conf {
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
}
.ts-conf-high { background: #ecfdf5; color: #065f46; }
.ts-conf-med  { background: #fef9c3; color: #854d0e; }
.ts-conf-low  { background: #fee2e2; color: #991b1b; }
.ts-avatar {
    width: 32px; height: 32px;
    border-radius: 50%;
    background: #e0e7ff;
    color: #3730a3;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    font-size: 12px;
}
.ts-avatar-lg {
    width: 56px; height: 56px;
    font-size: 18px;
}
.ts-email-body {
    background: #f9fafb;
    color: #111827;
    border: 0.5px solid #e5e7eb;
    border-radius: 6px;
    padding: 12px;
    font-family: ui-sans-serif, system-ui, sans-serif;
    line-height: 1.55;
    white-space: pre-wrap;
}
/* Make the Quill rich-text editor readable in dark themes:
   white editor surface, dark text, full-width inside the Streamlit container. */
.ts-quill .stCustomComponentV1 iframe { background: #ffffff; }
.ts-quill .ql-toolbar {
    background: #f3f4f6;
    border: 0.5px solid #d1d5db !important;
    border-bottom: 0 !important;
    border-radius: 6px 6px 0 0;
}
.ts-quill .ql-container {
    background: #ffffff;
    color: #111827;
    border: 0.5px solid #d1d5db !important;
    border-radius: 0 0 6px 6px;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 14px;
    min-height: 240px;
}
.ts-enhance-panel {
    background: #f1f5f9;
    color: #0f172a;
    border: 0.5px solid #cbd5e1;
    border-radius: 8px;
    padding: 14px 16px;
    margin-top: 12px;
}
.ts-enhance-panel h4 {
    margin: 0 0 8px 0;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #334155;
}
.ts-subject-mono {
    background: #111827;
    color: #f3f4f6;
    border-radius: 6px;
    padding: 8px 12px;
    font-family: ui-monospace, "SFMono-Regular", monospace;
    font-size: 13px;
}
.ts-breadcrumb {
    color: #6b7280;
    font-size: 12px;
    margin-bottom: 4px;
}
</style>
"""


def inject_styles() -> None:
    """Inject the trispoke palette and component styles."""
    st.markdown(_CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "recommended") -> str:
    """Return HTML for a pill badge. Render with `unsafe_allow_html=True`.

    kind ∈ {recommended, verified, reachable}
    """
    return f'<span class="ts-badge ts-badge-{kind}">{text}</span>'
