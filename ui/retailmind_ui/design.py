"""The design system: tokens, global styling, and motion.

**One place decides what things look like.** Every colour, radius, shadow and
duration in this console comes from the tokens below and is consumed through a
CSS custom property. A component that hard-codes `#6366F1` is a component that
will be wrong the day the accent changes, and in a product where colour carries
*meaning* — amber is a hedge, rose is a loss, cyan is machine-generated — a
drifted palette is a drifted claim.

**Colour is semantic before it is decorative.** The evidence tiers, estimate
bases and severity bands the API returns each map to exactly one hue, used
nowhere else. A reader who learns that violet means "assumed" learns it once.

**Motion is under 220ms and never blocks reading.** Panels rise 4px and fade in
on first paint; nothing slides, bounces, or animates a number upward. An
executive re-reading a figure should not have to wait for it to finish moving,
and a count that ticks up from zero is a number you cannot screenshot.

**Glass is used sparingly and only where depth means something** — the command
bar, the investigation rail, modal surfaces. Blurred translucency over a busy
chart is decoration that costs legibility, which is a bad trade on a screen
someone makes decisions from.
"""

from typing import Any

import streamlit as st

# ── Tokens ───────────────────────────────────────────────────────────

INK = {
    "canvas": "#07090D",
    "surface": "#0E121A",
    "raised": "#141A24",
    "line": "rgba(148, 163, 184, 0.14)",
    "line_strong": "rgba(148, 163, 184, 0.26)",
    "text": "#E8ECF4",
    "muted": "#94A3B8",
    "faint": "#64748B",
}

#: Semantic hues. Each appears in exactly one meaning across the product.
SEMANTIC = {
    "accent": "#6366F1",  # the platform itself: navigation, focus, primary action
    "ai": "#22D3EE",  # machine-generated narrative and inference
    "positive": "#10B981",  # growth, availability, on-time
    "warning": "#F59E0B",  # deterioration that has not yet cost anything
    "critical": "#F43F5E",  # money already lost or certain to be
    "capital": "#A78BFA",  # working capital — never added to profit
}

#: Evidence tiers and estimate bases, in descending strength. The colour is
#: the ceiling made visible: blue claims are arithmetic, violet ones rest on a
#: parameter nobody measured.
TIER_COLOUR = {
    "arithmetic": "#38BDF8",
    "measured": "#38BDF8",
    "mechanical": "#34D399",
    "derived": "#34D399",
    "modelled": "#FBBF24",
    "statistical": "#FBBF24",
    "assumed": "#A78BFA",
    "associative": "#A78BFA",
    "inferred": "#A78BFA",
    "unknown": "#64748B",
}

TIER_MEANING = {
    "arithmetic": "exact decomposition",
    "measured": "arithmetic over observed data",
    "mechanical": "a mechanism that necessarily applies",
    "derived": "computed through a stated relationship",
    "modelled": "uses a forecast or documented model",
    "statistical": "measured deviation, mechanism plausible",
    "assumed": "rests on an unmeasured parameter",
    "associative": "correlated, no mechanism established",
    "inferred": "consistent with the data, not established by it",
    "unknown": "the platform cannot answer this",
}

SEVERITY_COLOUR = {
    "critical": SEMANTIC["critical"],
    "warn": SEMANTIC["warning"],
    "info": SEMANTIC["ai"],
}

RISK_COLOUR = {
    "low": SEMANTIC["positive"],
    "medium": SEMANTIC["warning"],
    "high": SEMANTIC["critical"],
}

#: Type scale. Display sizes are for one number on a briefing; body text stays
#: at 14–15px because this is read for an hour at a time, not glanced at.
TYPE = {
    "display": "2.75rem",
    "headline": "1.75rem",
    "title": "1.125rem",
    "body": "0.9375rem",
    "small": "0.8125rem",
    "micro": "0.75rem",
}

SPACE = {"xs": "0.25rem", "sm": "0.5rem", "md": "0.875rem", "lg": "1.25rem", "xl": "2rem"}
RADIUS = {"sm": "8px", "md": "12px", "lg": "16px", "pill": "999px"}
MOTION = {"fast": "140ms", "base": "200ms", "curve": "cubic-bezier(0.2, 0.8, 0.2, 1)"}

FONT_STACK = (
    '-apple-system, BlinkMacSystemFont, "Inter", "SF Pro Text", "Segoe UI", '
    "Roboto, Helvetica, Arial, sans-serif"
)
MONO_STACK = '"SF Mono", "JetBrains Mono", "Fira Code", ui-monospace, Menlo, monospace'


def tier_colour(tier: str) -> str:
    return TIER_COLOUR.get(str(tier).lower(), INK["faint"])


def tier_meaning(tier: str) -> str:
    return TIER_MEANING.get(str(tier).lower(), str(tier))


def severity_colour(severity: str) -> str:
    return SEVERITY_COLOUR.get(str(severity).lower(), INK["faint"])


def risk_colour(band: str) -> str:
    return RISK_COLOUR.get(str(band).lower(), INK["faint"])


def confidence_colour(value: float) -> str:
    """Confidence bands share the semantic palette rather than a gradient.

    A continuous colour ramp implies the platform can distinguish 61% from 64%
    confidence. It cannot — these are graded estimates with hard ceilings, and
    three bands is the resolution the underlying method actually supports.
    """
    if value >= 0.7:
        return SEMANTIC["positive"]
    if value >= 0.45:
        return SEMANTIC["warning"]
    return SEMANTIC["critical"]


# ── Global stylesheet ────────────────────────────────────────────────


def stylesheet() -> str:
    """The whole design system as one stylesheet."""
    return f"""
    <style>
    :root {{
        --rm-canvas: {INK["canvas"]};
        --rm-surface: {INK["surface"]};
        --rm-raised: {INK["raised"]};
        --rm-line: {INK["line"]};
        --rm-line-strong: {INK["line_strong"]};
        --rm-text: {INK["text"]};
        --rm-muted: {INK["muted"]};
        --rm-faint: {INK["faint"]};

        --rm-accent: {SEMANTIC["accent"]};
        --rm-ai: {SEMANTIC["ai"]};
        --rm-positive: {SEMANTIC["positive"]};
        --rm-warning: {SEMANTIC["warning"]};
        --rm-critical: {SEMANTIC["critical"]};
        --rm-capital: {SEMANTIC["capital"]};

        --rm-radius: {RADIUS["md"]};
        --rm-radius-lg: {RADIUS["lg"]};
        --rm-motion: {MOTION["base"]} {MOTION["curve"]};
        --rm-font: {FONT_STACK};
        --rm-mono: {MONO_STACK};
    }}

    /* ── Canvas ────────────────────────────────────────────────── */

    .stApp {{
        background:
            radial-gradient(1200px 600px at 15% -10%, rgba(99, 102, 241, 0.10), transparent 60%),
            radial-gradient(900px 500px at 90% 0%, rgba(34, 211, 238, 0.06), transparent 55%),
            var(--rm-canvas);
        color: var(--rm-text);
        font-family: var(--rm-font);
        font-feature-settings: "cv02", "cv03", "cv04", "ss01";
        -webkit-font-smoothing: antialiased;
    }}

    .block-container {{
        padding-top: 2.25rem;
        padding-bottom: 4rem;
        max-width: 1480px;
    }}

    /* Streamlit's own header floats over the content and serves no purpose
       here — the console has its own command bar. */
    header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
    #MainMenu, footer {{ visibility: hidden; }}

    /* ── Typography ────────────────────────────────────────────── */

    h1, h2, h3, h4 {{
        font-family: var(--rm-font);
        letter-spacing: -0.022em;
        color: var(--rm-text);
        font-weight: 620;
    }}
    h1 {{ font-size: {TYPE["headline"]}; }}
    h2 {{ font-size: 1.375rem; }}
    h3 {{ font-size: {TYPE["title"]}; }}

    .stApp, p, li, label, .stMarkdown {{ font-size: {TYPE["body"]}; line-height: 1.55; }}

    /* Every figure in this product is compared against another figure, and
       proportional digits make columns of numbers impossible to scan. */
    [data-testid="stMetricValue"], .rm-num, td, th {{
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum";
    }}

    code, pre, .rm-mono {{ font-family: var(--rm-mono); font-size: {TYPE["small"]}; }}

    /* Captions carry every qualification the API attaches. Muting them into
       invisibility is the standard way a console deletes its own caveats. */
    [data-testid="stCaptionContainer"], .stCaption, .stCaption p {{
        color: var(--rm-muted) !important;
        font-size: {TYPE["small"]};
        line-height: 1.5;
    }}

    /* ── Motion ────────────────────────────────────────────────── */

    @keyframes rm-rise {{
        from {{ opacity: 0; transform: translateY(4px); }}
        to   {{ opacity: 1; transform: none; }}
    }}
    @keyframes rm-shimmer {{
        from {{ background-position: -420px 0; }}
        to   {{ background-position: 420px 0; }}
    }}
    @keyframes rm-pulse {{
        0%, 100% {{ opacity: 1; }}
        50%      {{ opacity: 0.45; }}
    }}

    .rm-panel, .rm-card, [data-testid="stVerticalBlockBorderWrapper"] {{
        animation: rm-rise var(--rm-motion) both;
    }}

    /* Motion is a courtesy, not a requirement. */
    @media (prefers-reduced-motion: reduce) {{
        *, *::before, *::after {{
            animation-duration: 0.001ms !important;
            transition-duration: 0.001ms !important;
        }}
    }}

    /* ── Surfaces ──────────────────────────────────────────────── */

    .rm-panel {{
        background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012));
        border: 1px solid var(--rm-line);
        border-radius: var(--rm-radius-lg);
        padding: 1.15rem 1.25rem;
        box-shadow: 0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 32px -20px rgba(0,0,0,0.9);
    }}

    /* Glass is reserved for surfaces that sit *above* content — the command
       bar and the investigation rail. Blurring a chart costs legibility. */
    .rm-glass {{
        background: rgba(20, 26, 36, 0.72);
        backdrop-filter: blur(18px) saturate(140%);
        -webkit-backdrop-filter: blur(18px) saturate(140%);
        border: 1px solid var(--rm-line-strong);
        border-radius: var(--rm-radius-lg);
    }}

    [data-testid="stVerticalBlockBorderWrapper"] {{
        background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.008));
        border: 1px solid var(--rm-line) !important;
        border-radius: var(--rm-radius-lg);
        transition: border-color var(--rm-motion), transform var(--rm-motion);
    }}
    [data-testid="stVerticalBlockBorderWrapper"]:hover {{
        border-color: var(--rm-line-strong) !important;
    }}

    /* ── Metrics ───────────────────────────────────────────────── */

    [data-testid="stMetric"] {{
        background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01));
        border: 1px solid var(--rm-line);
        border-radius: var(--rm-radius);
        padding: 0.85rem 1rem;
    }}
    [data-testid="stMetricLabel"] p {{
        color: var(--rm-muted);
        font-size: {TYPE["micro"]};
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-weight: 600;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.6rem;
        letter-spacing: -0.02em;
        font-weight: 600;
    }}

    /* ── Sidebar ───────────────────────────────────────────────── */

    [data-testid="stSidebar"] {{
        background: linear-gradient(180deg, #0B0E15 0%, #090C12 100%);
        border-right: 1px solid var(--rm-line);
    }}
    [data-testid="stSidebar"] .block-container {{ padding-top: 1.25rem; }}

    [data-testid="stSidebarNav"] {{ display: none; }}

    /* ── Controls ──────────────────────────────────────────────── */

    .stButton > button, .stDownloadButton > button {{
        border-radius: 10px;
        border: 1px solid var(--rm-line-strong);
        background: rgba(255,255,255,0.04);
        color: var(--rm-text);
        font-weight: 550;
        font-size: {TYPE["small"]};
        transition: background var(--rm-motion), border-color var(--rm-motion),
                    transform var(--rm-motion);
    }}
    .stButton > button:hover, .stDownloadButton > button:hover {{
        background: rgba(255,255,255,0.08);
        border-color: rgba(148,163,184,0.4);
        transform: translateY(-1px);
    }}
    .stButton > button[kind="primary"] {{
        background: linear-gradient(180deg, var(--rm-accent), #4F46E5);
        border-color: transparent;
        color: #fff;
    }}
    .stButton > button:focus-visible, a:focus-visible, input:focus-visible {{
        outline: 2px solid var(--rm-accent);
        outline-offset: 2px;
    }}

    [data-baseweb="input"], [data-baseweb="select"] > div, .stTextArea textarea {{
        background: rgba(255,255,255,0.03) !important;
        border-radius: 10px !important;
        border-color: var(--rm-line) !important;
    }}

    [data-baseweb="tab-list"] {{
        gap: 0.25rem;
        border-bottom: 1px solid var(--rm-line);
    }}
    [data-baseweb="tab"] {{
        font-size: {TYPE["small"]};
        font-weight: 560;
        letter-spacing: 0.01em;
        color: var(--rm-muted);
    }}
    [data-baseweb="tab"][aria-selected="true"] {{ color: var(--rm-text); }}

    /* ── Data ──────────────────────────────────────────────────── */

    [data-testid="stDataFrame"] {{
        border: 1px solid var(--rm-line);
        border-radius: var(--rm-radius);
        overflow: hidden;
    }}
    [data-testid="stDataFrame"] > div {{ overflow-x: auto; }}

    hr {{ border-color: var(--rm-line); margin: 1.25rem 0; }}

    ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
    ::-webkit-scrollbar-thumb {{
        background: rgba(148,163,184,0.22);
        border-radius: 999px;
        border: 2px solid transparent;
        background-clip: content-box;
    }}
    ::-webkit-scrollbar-track {{ background: transparent; }}

    /* ── Component classes ─────────────────────────────────────── */

    .rm-eyebrow {{
        font-size: {TYPE["micro"]};
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--rm-faint);
        font-weight: 650;
    }}
    .rm-display {{
        font-size: {TYPE["display"]};
        font-weight: 640;
        letter-spacing: -0.035em;
        line-height: 1.05;
        font-variant-numeric: tabular-nums;
    }}
    .rm-chip {{
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.14rem 0.55rem;
        border-radius: {RADIUS["pill"]};
        font-size: {TYPE["micro"]};
        font-weight: 600;
        letter-spacing: 0.01em;
        border: 1px solid currentColor;
        white-space: nowrap;
    }}
    .rm-dot {{
        width: 7px; height: 7px; border-radius: 50%;
        display: inline-block; flex: none;
    }}
    .rm-live {{ animation: rm-pulse 2.4s ease-in-out infinite; }}

    .rm-skeleton {{
        border-radius: var(--rm-radius);
        background: linear-gradient(90deg,
            rgba(255,255,255,0.04) 25%,
            rgba(255,255,255,0.09) 37%,
            rgba(255,255,255,0.04) 63%);
        background-size: 840px 100%;
        animation: rm-shimmer 1.4s ease-in-out infinite;
    }}

    .rm-rail {{
        border-left: 2px solid var(--rm-line);
        padding-left: 1rem;
        margin-left: 0.35rem;
    }}
    .rm-node {{ position: relative; padding-bottom: 1.1rem; }}
    .rm-node::before {{
        content: "";
        position: absolute;
        left: -1.42rem;
        top: 0.34rem;
        width: 9px; height: 9px;
        border-radius: 50%;
        background: var(--rm-canvas);
        border: 2px solid currentColor;
    }}

    .rm-meter {{
        height: 5px;
        border-radius: 999px;
        background: rgba(148,163,184,0.16);
        overflow: hidden;
    }}
    .rm-meter > span {{ display: block; height: 100%; border-radius: 999px; }}

    .rm-kbd {{
        font-family: var(--rm-mono);
        font-size: 0.68rem;
        padding: 0.1rem 0.36rem;
        border-radius: 5px;
        border: 1px solid var(--rm-line-strong);
        background: rgba(255,255,255,0.04);
        color: var(--rm-muted);
    }}

    /* ── Responsive ────────────────────────────────────────────── */

    /* Streamlit columns divide rather than wrap: below this width it keeps
       shrinking a four-across metric row until a currency figure clips, and a
       clipped number is a wrong number. */
    @media (max-width: 1000px) {{
        [data-testid="stHorizontalBlock"] {{ flex-direction: column; gap: 0.6rem; }}
        [data-testid="stHorizontalBlock"] > div {{
            width: 100% !important;
            min-width: 100% !important;
        }}
        .rm-display {{ font-size: 2rem; }}
    }}
    @media (max-width: 640px) {{
        .block-container {{ padding: 1rem 0.85rem 3rem; }}
        [data-testid="stMetricValue"] {{ font-size: 1.3rem; }}
        .rm-hide-sm {{ display: none; }}
    }}
    </style>
    """


def inject() -> None:
    """Apply the design system. Called once per page, first."""
    st.markdown(stylesheet(), unsafe_allow_html=True)


def configure(title: str, *, icon: str = "◈") -> None:
    """Page config plus the design system, in the required order."""
    st.set_page_config(
        page_title=f"{title} · RetailMind",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject()


def html(markup: str) -> None:
    """Render a design-system fragment.

    Centralised for two reasons. Every raw-HTML call site is greppable — these
    are the only places the console emits markup it composed itself. And each
    line is stripped of leading whitespace first, which is not cosmetic:
    Streamlit runs this through a Markdown parser, and Markdown treats any line
    indented four spaces as a **code block**. Fragments written inside indented
    f-strings therefore render as literal source unless flattened here, which
    is exactly the kind of bug that survives review and dies in a screenshot.
    """
    st.markdown(
        "\n".join(line.strip() for line in markup.splitlines()),
        unsafe_allow_html=True,
    )


def escape(value: Any) -> str:
    """Escape a value for interpolation into a fragment.

    API responses carry free text — SKU names, store labels, analyst prose,
    supplier names. None of it is trusted markup, and a supplier called
    ``<script>`` must render as a supplier called ``<script>``.
    """
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
