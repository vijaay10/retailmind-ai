"""Theme and responsive layout.

**The console follows Streamlit's theme rather than fighting it.** The obvious
design here is an in-app dark-mode switch that swaps a set of CSS variables,
and it does not work: Streamlit's own colours are compiled at boot, so a page
switched to light gets a white background with grey-on-white metric labels,
black input boxes, and — unfixable from CSS — a dataframe still painted dark,
because the grid draws itself onto a canvas from JavaScript. A control that
produces that is worse than no control.

So the theme is set where Streamlit can honour it, in `.streamlit/config.toml`,
and this module reads that same setting back and layers the matching palette on
top. A deployment that prefers light changes one line and everything follows;
nothing here can put the page into a state Streamlit's own widgets disagree
with.

The responsive rules exist because Streamlit's columns do not wrap: at a
narrow viewport it keeps dividing the same width until a currency figure
truncates mid-number. Below the breakpoint the metric row stacks instead,
because a stacked figure is readable and a clipped one is a wrong number.
"""

import contextlib
from typing import Any

import streamlit as st
from streamlit import config

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0E1117",
        "surface": "#161B26",
        "border": "#2A3140",
        "text": "#E6E9EF",
        "muted": "#9AA4B8",
        "accent": "#4C8DFF",
    },
    "light": {
        "bg": "#FFFFFF",
        "surface": "#F5F7FB",
        "border": "#D9DEE8",
        "text": "#111827",
        "muted": "#5B6478",
        "accent": "#1F3864",
    },
}


def current() -> str:
    """The theme Streamlit compiled its widgets against.

    Read from the configuration rather than from our own state, so the palette
    cannot disagree with what Streamlit has already drawn. Note this is *not*
    `st.context.theme`, which reports the viewer's browser preference and says
    "dark" for a light-configured app viewed in a dark browser — precisely the
    mismatch this module exists to avoid.
    """
    with contextlib.suppress(Exception):
        base = str(config.get_option("theme.base") or "")
        if base in PALETTES:
            return base
    return "dark"


def apply(palette_name: str | None = None) -> None:
    """Inject the palette and layout rules for this rerun."""
    palette: dict[str, Any] = PALETTES[palette_name or current()]

    st.markdown(
        f"""
        <style>
        :root {{
            --rm-bg: {palette["bg"]};
            --rm-surface: {palette["surface"]};
            --rm-border: {palette["border"]};
            --rm-text: {palette["text"]};
            --rm-muted: {palette["muted"]};
            --rm-accent: {palette["accent"]};
        }}

        .stApp, [data-testid="stAppViewContainer"] {{
            background: var(--rm-bg);
            color: var(--rm-text);
        }}
        [data-testid="stSidebar"] {{
            background: var(--rm-surface);
            border-right: 1px solid var(--rm-border);
        }}

        /* Bordered containers carry findings and recommendations. Giving them
           a surface makes the qualification beneath each one read as part of
           the same object rather than as loose page text. */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            background: var(--rm-surface);
            border: 1px solid var(--rm-border) !important;
            border-radius: 10px;
        }}

        [data-testid="stMetric"] {{
            background: var(--rm-surface);
            border: 1px solid var(--rm-border);
            border-radius: 10px;
            padding: 0.75rem 1rem;
        }}
        [data-testid="stMetricValue"] {{
            font-size: 1.55rem;
            font-variant-numeric: tabular-nums;
        }}

        /* Captions carry every caveat in this product. Muting them to
           near-invisible is the standard way a console quietly deletes its own
           qualifications, so they stay legible. */
        [data-testid="stCaptionContainer"], .stCaption {{
            color: var(--rm-muted) !important;
            font-size: 0.83rem;
            line-height: 1.45;
        }}

        h2, h3, h4 {{ color: var(--rm-text); letter-spacing: -0.01em; }}
        .stDataFrame {{ border: 1px solid var(--rm-border); border-radius: 8px; }}

        /* Streamlit columns divide rather than wrap, so a four-across metric
           row keeps shrinking until a currency figure clips. Stack instead. */
        @media (max-width: 820px) {{
            [data-testid="stHorizontalBlock"] {{
                flex-direction: column;
                gap: 0.5rem;
            }}
            [data-testid="stHorizontalBlock"] > div {{
                width: 100% !important;
                min-width: 100% !important;
            }}
            [data-testid="stMetricValue"] {{ font-size: 1.3rem; }}
        }}

        @media (max-width: 480px) {{
            .block-container {{ padding: 1rem 0.75rem; }}
        }}

        /* Tables scroll inside themselves; the page never scrolls sideways. */
        [data-testid="stDataFrame"] > div {{ overflow-x: auto; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def configure(page_title: str) -> None:
    """Page config plus theme. Call once, first, on every page."""
    st.set_page_config(
        page_title=f"{page_title} · RetailMind",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply()
