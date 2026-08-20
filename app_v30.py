"""
Stock Dashboard v3 — price data (with intraday support), technical
indicators, cleaned news summaries with links, and a narrative connecting
the technical picture to recent news.

IMPORTANT: Nothing here predicts future prices or tells you to buy/sell.
The "Indicator Lean" and "narrative" describe the CURRENT picture and the
tension between different signals — they are not forecasts. This is not
financial advice.
"""

import re
import json
import time
import io
from contextlib import contextmanager
import bcrypt
import pyotp
import qrcode
import html as html_lib
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from scipy import stats
import requests
import plotly.graph_objects as go
from datetime import datetime
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ----------------------------------------------------------------------
# 1. PAGE SETUP
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ACCOUNTS & LOGIN
# ----------------------------------------------------------------------
# Passwords are hashed with bcrypt (a mature, stable, well-vetted library)
# — never stored as plain text. This login form is hand-built with plain
# Streamlit widgets rather than a third-party auth-widget library, since
# those libraries' exact function signatures change between versions in
# ways that are hard to debug blind. bcrypt's API, by contrast, has been
# stable for years: hash a password, check a password, that's it.
#
# 2FA uses TOTP (the same kind of 6-digit code as Google Authenticator/
# Authy) rather than SMS, since SMS 2FA needs a paid provider (e.g. Twilio).
#
# CAVEAT: accounts are stored in a local JSON file next to the app. That's
# reliable if you run Tickveil on your own computer. On Streamlit
# Community Cloud's free tier, local files are NOT guaranteed to survive
# every restart/redeploy — fine for testing, but for a public deployment
# you plan to keep long-term, you'd eventually want an external database
# instead (e.g. a free-tier hosted Postgres via Supabase) so accounts
# never get wiped by a redeploy. That's a bigger follow-up step, not done
# here.
#
# LIMITATION: there's no "remember me" cookie here, so closing the tab or
# hard-refreshing will log you out and you'll need to log in again. Adding
# persistent sessions across browser restarts is a reasonable next step
# if this becomes annoying, just kept out for now to keep this version
# simple and reliable.
# ----------------------------------------------------------------------
USERS_FILE = "bullbear_users.json"


def load_users() -> dict:
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_users(users: dict) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


st.set_page_config(page_title="Tickveil", page_icon="🕯️", layout="wide", initial_sidebar_state="expanded")

# ----------------------------------------------------------------------
# 1b. VISUAL SYSTEM — "private wealth terminal"
#
#     Design intent: this should read like a product a private bank pays
#     for, not a student project. Three decisions carry that:
#
#       1. TYPOGRAPHY as a three-voice system. Fraunces (high-contrast
#          optical serif) speaks only for the brand and section titles;
#          Inter carries all interface text; JetBrains Mono carries every
#          number, with tabular figures so digits sit in fixed columns and
#          prices don't jitter as they tick. Mixing a display serif with a
#          neutral grotesque and a true tabular mono is the single biggest
#          "this cost money" signal in a data product.
#
#       2. DEPTH instead of borders. Surfaces are near-black glass lit by
#          two off-screen colour sources (a warm champagne wash top-left,
#          a cool jade wash top-right) plus a film-grain overlay, so the
#          background has texture rather than being flat #000. Card edges
#          are 1px hairlines at ~6% white — visible, never heavy.
#
#       3. MOTION with intent. Everything eases on a cubic-bezier curve
#          (.16,1,.3,1) rather than linear, content reveals in a stagger
#          rather than all at once, live data pulses, buttons catch a
#          light sweep, and score bars grow from zero. All of it is pure
#          CSS — Streamlit strips <script>, and shipping a JS animation
#          library through an iframe would cost more than it returns.
#
#     Accessibility is not sacrificed for any of it: text sits at or above
#     WCAG AA contrast on these surfaces, focus rings are explicit and
#     gold, and the entire motion layer switches off under
#     prefers-reduced-motion.
#
#     The colour/theme basics also live in .streamlit/config.toml (the
#     officially supported way to theme Streamlit). This block layers on
#     everything config.toml cannot reach.
#
#     Note: Streamlit's internal DOM can change between versions. Every
#     rule below is cosmetic and selector-based, so if a future release
#     renames a test id the app still runs — it just loses that one
#     flourish. Where it's cheap, selectors are doubled up against both
#     the current and previous test ids for exactly that reason.
# ----------------------------------------------------------------------
# The block below MUST begin with <style> on its own line. Markdown treats
# <style> as a raw-HTML block that runs until its closing tag, so the blank
# lines separating the sections inside it are safe. Leading it with anything
# else (a <link rel="preconnect">, a comment) downgrades it to an ordinary
# HTML block, which markdown ends at the FIRST blank line — and the rest of
# the stylesheet then renders as visible text on the page.
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300..700&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* ==================================================================
   DESIGN TOKENS
   One place to change the whole product's look. Every rule below
   reads from these — no hard-coded hexes scattered through the sheet.
   ================================================================== */
:root {
    /* Surfaces: near-black, warm-shifted rather than pure grey */
    --ink-950: #05070B;
    --ink-900: #080A10;
    --ink-850: #0B0E15;
    --ink-800: #0E1219;
    --ink-700: #141924;

    /* Champagne — the single accent. Used sparingly, on purpose. */
    --gold-300: #F2E2C1;
    --gold-400: #E4CB9E;
    --gold-500: #D4B078;
    --gold-600: #B08B52;
    --gold-grad: linear-gradient(135deg, #F6E9CE 0%, #E4CB9E 38%, #C39C61 72%, #A37F4A 100%);

    /* Directional colours — muted, not neon. Jade up, rose down. */
    --jade: #5FCF9B;
    --jade-dim: rgba(95, 207, 155, 0.14);
    --rose: #F0616F;
    --rose-dim: rgba(240, 97, 111, 0.14);

    /* Type */
    --text-100: #F4F1EA;
    --text-200: #DAD5CA;
    --text-300: #ABA598;
    --text-500: #7E786C;

    /* Lines */
    --line: rgba(255, 255, 255, 0.065);
    --line-strong: rgba(255, 255, 255, 0.11);
    --line-gold: rgba(212, 176, 120, 0.28);

    /* Shadows: long, soft, low-opacity — expensive light behaves this way */
    --shadow-sm: 0 2px 10px -4px rgba(0, 0, 0, 0.7);
    --shadow-md: 0 18px 44px -24px rgba(0, 0, 0, 0.95);
    --shadow-gold: 0 16px 40px -18px rgba(212, 176, 120, 0.35);

    /* The house easing curve. Everything uses it. */
    --ease: cubic-bezier(0.16, 1, 0.3, 1);

    --radius: 16px;
    --radius-sm: 11px;

    --font-display: 'Fraunces', 'Iowan Old Style', Georgia, serif;
    --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
}

/* ==================================================================
   PAGE CANVAS — layered light, then grain
   ================================================================== */
html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {
    font-family: var(--font-ui);
    color: var(--text-200);
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(1100px 620px at 8% -12%, rgba(212, 176, 120, 0.13), transparent 62%),
        radial-gradient(900px 520px at 96% -6%, rgba(95, 207, 155, 0.075), transparent 60%),
        radial-gradient(1000px 800px at 50% 118%, rgba(212, 176, 120, 0.05), transparent 66%),
        linear-gradient(180deg, var(--ink-900) 0%, var(--ink-950) 55%, #04060A 100%);
    background-attachment: fixed;
    animation: page-in 0.7s var(--ease) both;
}

/* Film grain. Sits above the gradients, below nothing that matters —
   pointer-events:none means it never intercepts a click. At 3.5% it
   reads as texture, not noise. */
[data-testid="stAppViewContainer"]::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    opacity: 0.035;
    mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");
}

[data-testid="stHeader"] {
    background: transparent !important;
    backdrop-filter: blur(10px);
}
[data-testid="stMain"] { position: relative; z-index: 1; }
[data-testid="stMainBlockContainer"], .block-container {
    padding-top: 2.2rem !important;
    max-width: 1360px;
}

::selection { background: rgba(212, 176, 120, 0.28); color: var(--text-100); }

/* Custom scrollbar — a small detail people notice without noticing */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: var(--ink-950); }
::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg, rgba(212, 176, 120, 0.34), rgba(212, 176, 120, 0.16));
    border-radius: 99px;
    border: 3px solid var(--ink-950);
}
::-webkit-scrollbar-thumb:hover { background: rgba(212, 176, 120, 0.5); }

/* Visible, on-brand focus ring — accessibility and polish at once */
*:focus-visible {
    outline: 2px solid var(--gold-500) !important;
    outline-offset: 2px;
    border-radius: 6px;
}

/* ==================================================================
   TYPOGRAPHY
   ================================================================== */
h1, h2, h3, h4 {
    font-family: var(--font-display) !important;
    color: var(--text-100) !important;
    font-weight: 500;
    font-variation-settings: 'opsz' 120, 'SOFT' 0;
    letter-spacing: -0.015em;
}
h1 {
    font-size: 2.1rem !important;
    line-height: 1.1;
    border: none;
    padding-bottom: 0;
}
h2 { font-size: 1.5rem !important; }

/* Section titles (st.subheader) get an editorial treatment: a short gold
   rule above the text rather than a full-width divider under it. */
h3 {
    font-size: 1.22rem !important;
    font-weight: 500;
    margin-top: 2.1rem !important;
    padding-top: 0.95rem;
    position: relative;
    border: none !important;
}
h3::before {
    content: "";
    position: absolute;
    top: 0; left: 0;
    width: 38px; height: 2px;
    border-radius: 2px;
    background: var(--gold-grad);
    box-shadow: 0 0 14px rgba(212, 176, 120, 0.45);
}
h4 { font-size: 1.02rem !important; }

p, li, .stMarkdown, [data-testid="stMarkdownContainer"] {
    font-family: var(--font-ui);
    color: var(--text-200);
    line-height: 1.68;
}
[data-testid="stCaptionContainer"], .stCaption, small {
    color: var(--text-500) !important;
    font-size: 0.79rem !important;
    line-height: 1.6;
}
/* Links carry colour, not permanent underlines — the rule only appears on
   hover, so a page of headlines doesn't read as a wall of underscores. */
a, a:visited, [data-testid="stMarkdownContainer"] a {
    color: var(--gold-400) !important;
    text-decoration: none !important;
    transition: color 0.3s var(--ease);
}
a:hover, [data-testid="stMarkdownContainer"] a:hover {
    color: var(--gold-300) !important;
    text-decoration: underline !important;
    text-underline-offset: 3px;
    text-decoration-thickness: 1px;
    text-decoration-color: var(--line-gold);
}
code, kbd {
    font-family: var(--font-mono) !important;
    background: rgba(212, 176, 120, 0.09) !important;
    color: var(--gold-400) !important;
    border: 1px solid rgba(212, 176, 120, 0.16);
    border-radius: 5px;
    padding: 0.1rem 0.38rem;
    font-size: 0.83em;
}

/* ==================================================================
   MASTHEAD — brand lockup, live status, tagline
   ================================================================== */
.tv-masthead {
    position: relative;
    padding: 1.6rem 0 1.5rem;
    margin-bottom: 0.4rem;
    animation: reveal-up 0.75s var(--ease) both;
}
.tv-masthead::after {
    content: "";
    position: absolute;
    left: 0; right: 0; bottom: 0;
    height: 1px;
    background: linear-gradient(90deg, var(--line-gold) 0%, rgba(255,255,255,0.05) 42%, transparent 88%);
}
.tv-brandrow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1rem;
}
.tv-brand {
    display: inline-flex;
    align-items: baseline;
    gap: 0.62rem;
    text-decoration: none !important;
}
.tv-mark {
    font-family: var(--font-display);
    font-size: 2.65rem;
    font-weight: 600;
    font-variation-settings: 'opsz' 144;
    letter-spacing: -0.03em;
    line-height: 1;
    /* Gradient text with a slow specular sweep travelling across it */
    background: linear-gradient(100deg,
        #C9BFA8 0%, #F6E9CE 22%, #FFFFFF 32%, #E4CB9E 44%, #B08B52 68%, #D9BE8E 100%);
    background-size: 260% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: sheen 9s ease-in-out infinite;
}
.tv-markdot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--gold-500);
    box-shadow: 0 0 12px rgba(212, 176, 120, 0.9);
    align-self: flex-end;
    margin-bottom: 0.55rem;
}
.tv-tagline {
    font-family: var(--font-ui);
    font-size: 0.7rem;
    letter-spacing: 0.34em;
    text-transform: uppercase;
    color: var(--text-500);
    margin-top: 0.55rem;
    font-weight: 500;
}
.tv-statusrow { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.tv-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.36rem 0.8rem;
    border-radius: 99px;
    border: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.035);
    font-family: var(--font-mono);
    font-size: 0.67rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-300);
    backdrop-filter: blur(8px);
    transition: border-color 0.4s var(--ease), color 0.4s var(--ease);
}
.tv-pill:hover { border-color: var(--line-gold); color: var(--text-100); }
.tv-pill.gold { border-color: rgba(212,176,120,0.24); color: var(--gold-400); background: rgba(212,176,120,0.06); }

/* Live dot: solid core + an expanding ring, the way a real status
   indicator behaves. Two elements, no JS. */
.tv-dot {
    position: relative;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--jade);
    box-shadow: 0 0 10px rgba(95, 207, 155, 0.85);
    flex: 0 0 auto;
}
.tv-dot::after {
    content: "";
    position: absolute;
    inset: -4px;
    border-radius: 50%;
    border: 1px solid var(--jade);
    animation: pulse-ring 2.1s var(--ease) infinite;
}

/* ==================================================================
   CARDS — glass, hairline, hover lift

   Targeting note: Streamlit has no stable test id for "this container
   was created with border=True" — older releases exposed
   stVerticalBlockBorderWrapper, current ones put the border on a plain
   stVerticalBlock via a hashed emotion class that changes every build.
   Rather than chase either, the card() helper below drops an invisible
   marker span inside each card and we select the ancestor with :has().
   Both selectors are kept so the styling survives a version move in
   either direction.
   ================================================================== */
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tv-card-mark) {
    border: 1px solid var(--line) !important;
    border-radius: var(--radius) !important;
    padding: 1.15rem 1.25rem !important;
    background: linear-gradient(158deg, rgba(255,255,255,0.042) 0%, rgba(255,255,255,0.012) 46%, rgba(255,255,255,0.004) 100%) !important;
    backdrop-filter: blur(14px);
    box-shadow: var(--shadow-sm);
    position: relative;
    transition: transform 0.55s var(--ease), box-shadow 0.55s var(--ease), border-color 0.55s var(--ease);
}
/* A light hairline across the top edge — reads as a lit bevel */
[data-testid="stVerticalBlockBorderWrapper"]::before,
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tv-card-mark)::before {
    content: "";
    position: absolute;
    top: 0; left: 12%; right: 12%;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.16), transparent);
    pointer-events: none;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover,
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tv-card-mark):hover {
    transform: translateY(-3px);
    border-color: var(--line-gold) !important;
    box-shadow: var(--shadow-md), 0 0 0 1px rgba(212, 176, 120, 0.09);
}
/* The marker itself never renders — :has() is structural, so hiding its
   container doesn't stop the card selector above from matching. */
[data-testid="stElementContainer"]:has(.tv-card-mark) { display: none !important; }

/* ==================================================================
   METRICS — the numbers are the product, so they get the most care
   ================================================================== */
[data-testid="stMetric"] {
    position: relative;
    overflow: hidden;
    padding: 1.05rem 1.15rem 0.95rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--line);
    background: linear-gradient(160deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.014) 100%);
    transition: transform 0.5s var(--ease), border-color 0.5s var(--ease), box-shadow 0.5s var(--ease);
    animation: reveal-up 0.6s var(--ease) both;
}
[data-testid="stMetric"]::after {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(212,176,120,0.55), transparent);
    opacity: 0.65;
    transition: opacity 0.5s var(--ease);
}
[data-testid="stMetric"]:hover {
    transform: translateY(-3px);
    border-color: var(--line-gold);
    box-shadow: var(--shadow-md);
}
[data-testid="stMetric"]:hover::after { opacity: 1; }

/* Streamlit nests metric text inside its own markdown container, so the
   type rules have to reach the <p> — setting them on the wrapper alone
   gets overridden by the inherited paragraph styles. */
[data-testid="stMetricValue"], [data-testid="stMetricValue"] p,
[data-testid="stMetricValue"] div {
    font-family: var(--font-mono) !important;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1, "zero" 1;
    font-size: 1.5rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.025em;
    color: var(--text-100) !important;
    line-height: 1.2 !important;
}
[data-testid="stMetricValue"] { animation: metric-in 0.55s var(--ease) both; }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p,
[data-testid="stMetricLabel"] div {
    font-family: var(--font-ui) !important;
    color: var(--text-500) !important;
    text-transform: uppercase;
    font-size: 0.645rem !important;
    letter-spacing: 0.17em !important;
    font-weight: 600 !important;
    line-height: 1.5 !important;
}
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] div {
    font-family: var(--font-mono) !important;
    font-size: 0.79rem !important;
    font-weight: 500 !important;
}

/* ==================================================================
   TABS — a segmented control, not browser tabs

   Streamlit moved tabs from BaseWeb to react-aria, so the markup differs
   by version: [data-baseweb="tab"] on older builds, [data-testid="stTab"]
   with role="tab" on current ones. Both are addressed here, and both
   default underline indicators are suppressed since the selected pill
   already carries the state.
   ================================================================== */
[data-baseweb="tab-list"], [role="tablist"] {
    gap: 4px !important;
    padding: 5px !important;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.028);
    border: 1px solid var(--line);
    backdrop-filter: blur(12px);
    flex-wrap: wrap;
    animation: reveal-up 0.65s var(--ease) 0.05s both;
}
[data-baseweb="tab"], [data-testid="stTab"], [role="tab"] {
    border-radius: 10px !important;
    padding: 0.52rem 0.92rem !important;
    font-family: var(--font-ui) !important;
    color: var(--text-500) !important;
    background: transparent !important;
    cursor: pointer;
    transition: color 0.4s var(--ease), background 0.4s var(--ease), box-shadow 0.4s var(--ease);
}
[data-baseweb="tab"] p, [data-testid="stTab"] p, [role="tab"] p {
    font-family: var(--font-ui) !important;
    font-size: 0.7rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.13em !important;
    text-transform: uppercase;
    color: inherit !important;
    white-space: nowrap;
}
[data-baseweb="tab"]:hover, [data-testid="stTab"]:hover, [role="tab"]:hover {
    color: var(--text-100) !important;
    background: rgba(255, 255, 255, 0.05) !important;
}
[data-baseweb="tab"][aria-selected="true"],
[data-testid="stTab"][aria-selected="true"],
[role="tab"][aria-selected="true"] {
    color: var(--gold-300) !important;
    background: linear-gradient(150deg, rgba(212,176,120,0.28), rgba(212,176,120,0.08)) !important;
    box-shadow: inset 0 0 0 1px rgba(212,176,120,0.45), 0 8px 22px -10px rgba(212,176,120,0.55);
}
/* Kill every built-in selected-tab indicator across versions */
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"],
.react-aria-SelectionIndicator { display: none !important; }
[data-testid="stTabs"] [data-baseweb="tab-panel"],
[data-testid="stTabPanel"] { animation: fade-slide 0.5s var(--ease) both; }

/* ==================================================================
   BUTTONS — light sweeps across on hover
   ================================================================== */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button,
button[data-testid^="stBaseButton-"] {
    position: relative;
    overflow: hidden;
    font-family: var(--font-ui) !important;
    font-size: 0.735rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    border-radius: 11px !important;
    border: 1px solid rgba(212, 176, 120, 0.3) !important;
    background: rgba(212, 176, 120, 0.055) !important;
    color: var(--gold-400) !important;
    padding: 0.58rem 1.15rem !important;
    transition: transform 0.35s var(--ease), border-color 0.35s var(--ease),
                background 0.35s var(--ease), box-shadow 0.35s var(--ease), color 0.35s var(--ease);
}
/* Button labels live in a nested markdown container that carries its own
   colour and weight. Without inheriting here, a primary button ends up
   with pale gold text on a pale gold fill — invisible. */
button[data-testid^="stBaseButton-"] p,
.stButton > button p, .stDownloadButton > button p,
[data-testid="stFormSubmitButton"] > button p,
button[data-testid^="stBaseButton-"] [data-testid="stMarkdownContainer"] {
    color: inherit !important;
    font-family: var(--font-ui) !important;
    font-size: 0.735rem !important;
    font-weight: inherit !important;
    letter-spacing: 0.13em !important;
    text-transform: uppercase;
}
.stButton > button::after, .stDownloadButton > button::after,
[data-testid="stFormSubmitButton"] > button::after,
button[data-testid^="stBaseButton-"]::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(112deg, transparent 30%, rgba(255,255,255,0.26) 50%, transparent 70%);
    transform: translateX(-130%);
    transition: transform 0.85s var(--ease);
    pointer-events: none;
}
.stButton > button:hover::after, .stDownloadButton > button:hover::after,
[data-testid="stFormSubmitButton"] > button:hover::after,
button[data-testid^="stBaseButton-"]:hover::after { transform: translateX(130%); }

.stButton > button:hover, .stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover,
button[data-testid^="stBaseButton-"]:hover {
    transform: translateY(-2px);
    border-color: rgba(212, 176, 120, 0.6) !important;
    background: rgba(212, 176, 120, 0.12) !important;
    color: var(--gold-300) !important;
    box-shadow: var(--shadow-gold);
}
.stButton > button:active, [data-testid="stFormSubmitButton"] > button:active,
button[data-testid^="stBaseButton-"]:active {
    transform: translateY(0) scale(0.985);
}

/* Primary action: solid champagne, near-black label. The label colour is
   restated on the nested <p> because that's the element that actually
   paints the text. */
button[kind="primary"], button[kind="primaryFormSubmit"],
button[data-testid="stBaseButton-primary"],
button[data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--gold-grad) !important;
    border: none !important;
    box-shadow: 0 12px 34px -14px rgba(212, 176, 120, 0.72);
}
button[kind="primary"], button[kind="primary"] p,
button[kind="primaryFormSubmit"], button[kind="primaryFormSubmit"] p,
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primary"] p,
button[data-testid="stBaseButton-primaryFormSubmit"],
button[data-testid="stBaseButton-primaryFormSubmit"] p {
    color: #0A0C11 !important;
    font-weight: 700 !important;
}
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    background: var(--gold-grad) !important;
    box-shadow: 0 18px 46px -14px rgba(212, 176, 120, 0.9);
    filter: brightness(1.08);
}
button[kind="primary"]:hover p, button[kind="primaryFormSubmit"]:hover p,
button[data-testid="stBaseButton-primary"]:hover p,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover p { color: #0A0C11 !important; }

/* ==================================================================
   INPUTS
   ================================================================== */
/* Field shells. Current Streamlit wraps each control in a *RootElement /
   *Container div that paints the border, older builds used BaseWeb — both
   are listed so the fields look identical either way. */
[data-testid="stTextInputRootElement"],
[data-testid="stTextAreaRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stDateInputField"],
[data-testid="stSelectbox"] > div > div,
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="textarea"] {
    background: rgba(255, 255, 255, 0.035) !important;
    border: 1px solid var(--line) !important;
    border-radius: 10px !important;
    color: var(--text-100) !important;
    transition: border-color 0.35s var(--ease), box-shadow 0.35s var(--ease), background 0.35s var(--ease);
}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within,
[data-testid="stSelectbox"] > div > div:focus-within,
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
[data-baseweb="select"] > div:focus-within {
    border-color: rgba(212, 176, 120, 0.55) !important;
    box-shadow: 0 0 0 3px rgba(212, 176, 120, 0.12) !important;
    background: rgba(255, 255, 255, 0.055) !important;
}
/* The controls themselves stay transparent so only the shell shows a border */
input, textarea, select {
    background: transparent !important;
    color: var(--text-100) !important;
    font-family: var(--font-ui) !important;
}
/* Ticker symbols and other free-typed identifiers read better in mono */
[data-testid="stTextInputField"], [data-testid="stNumberInputField"] {
    font-family: var(--font-mono) !important;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
}
input::placeholder, textarea::placeholder { color: var(--text-500) !important; }

label, [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] p {
    font-family: var(--font-ui) !important;
    font-size: 0.665rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.15em !important;
    text-transform: uppercase;
    color: var(--text-500) !important;
}
/* Dropdown menus render in a portal outside the app container */
[data-baseweb="popover"] li:hover,
[data-testid="portal"] [role="option"]:hover,
[role="option"][data-focused="true"], [role="option"][aria-selected="true"] {
    background: rgba(212, 176, 120, 0.12) !important;
    color: var(--gold-300) !important;
}
[data-testid="portal"] [role="listbox"] {
    background: rgba(11, 14, 21, 0.97) !important;
    border: 1px solid var(--line-gold) !important;
    border-radius: 12px !important;
    backdrop-filter: blur(18px);
    box-shadow: var(--shadow-md);
}

/* Sliders in champagne */
[data-testid="stSlider"] [role="slider"] {
    background: var(--gold-500) !important;
    border-color: var(--gold-300) !important;
    box-shadow: 0 0 0 4px rgba(212, 176, 120, 0.16) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[style*="background"] { border-radius: 99px; }
[data-testid="stTickBar"] { background: transparent !important; }

/* ==================================================================
   FORMS, EXPANDERS, ALERTS, TABLES
   ================================================================== */
[data-testid="stForm"] {
    border: 1px solid var(--line) !important;
    border-radius: var(--radius) !important;
    padding: 1.4rem !important;
    background: linear-gradient(158deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012));
    backdrop-filter: blur(14px);
    box-shadow: var(--shadow-sm);
    animation: reveal-up 0.6s var(--ease) both;
}

[data-testid="stExpander"] details, [data-testid="stExpander"] {
    border: 1px solid var(--line) !important;
    border-radius: var(--radius-sm) !important;
    background: rgba(255, 255, 255, 0.024) !important;
    overflow: hidden;
    transition: border-color 0.45s var(--ease), background 0.45s var(--ease);
}
[data-testid="stExpander"]:hover { border-color: var(--line-gold) !important; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p {
    font-family: var(--font-ui) !important;
    font-size: 0.775rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em;
    color: var(--text-300) !important;
    transition: color 0.3s var(--ease);
}
[data-testid="stExpander"] summary:hover,
[data-testid="stExpander"] summary:hover p { color: var(--gold-400) !important; }
/* The chevron is a material-icon ligature in current builds, an <svg> in
   older ones — colour both, and rotate the ligature when open. */
[data-testid="stExpander"] svg { fill: var(--gold-500) !important; color: var(--gold-500) !important; }
[data-testid="stExpander"] [data-testid="stIconMaterial"] {
    color: var(--gold-500) !important;
    transition: transform 0.4s var(--ease);
}
[data-testid="stExpander"] details[open] summary [data-testid="stIconMaterial"] { transform: rotate(90deg); }

/* Alerts: a coloured spine on glass, rather than a saturated block.
   Streamlit signals severity through the inner stAlertContent* test id,
   so the spine colour is set from that rather than from a class we'd
   have to add by hand. */
[data-testid="stAlertContainer"], .stAlert > div {
    border-radius: var(--radius-sm) !important;
    border: 1px solid var(--line) !important;
    border-left: 2px solid var(--gold-500) !important;
    background: rgba(255, 255, 255, 0.032) !important;
    backdrop-filter: blur(10px);
    color: var(--text-200) !important;
    animation: fade-slide 0.5s var(--ease) both;
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: var(--jade) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"])   { border-left-color: var(--rose) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { border-left-color: var(--gold-500) !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"])    { border-left-color: rgba(255,255,255,0.22) !important; }
[data-testid="stAlert"] p, [data-testid="stAlertContainer"] p {
    color: var(--text-200) !important;
    font-size: 0.855rem !important;
}

/* Dataframes: monospaced figures, dark chrome */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--line) !important;
    border-radius: var(--radius-sm) !important;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.018);
}
[data-testid="stDataFrame"] [role="gridcell"], [data-testid="stDataFrame"] [role="columnheader"] {
    font-family: var(--font-mono) !important;
    font-size: 0.79rem !important;
    font-variant-numeric: tabular-nums;
}

hr, [data-testid="stDivider"] hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, transparent, var(--line-strong) 22%, var(--line-strong) 78%, transparent) !important;
    margin: 1.9rem 0 !important;
}

/* Charts sit on glass like everything else */
[data-testid="stPlotlyChart"] {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: linear-gradient(160deg, rgba(255,255,255,0.032), rgba(255,255,255,0.008));
    padding: 0.55rem;
    box-shadow: var(--shadow-sm);
    animation: reveal-up 0.7s var(--ease) both;
    transition: border-color 0.5s var(--ease), box-shadow 0.5s var(--ease);
}
[data-testid="stPlotlyChart"]:hover { border-color: var(--line-gold); box-shadow: var(--shadow-md); }

/* Spinner + progress in champagne */
[data-testid="stSpinner"] svg { stroke: var(--gold-500) !important; }
[data-testid="stSpinner"] p { color: var(--text-500) !important; font-size: 0.78rem !important; letter-spacing: 0.08em; }
/* Checked checkboxes and toggles pick up the accent. The :has() form
   catches current react-aria markup where the state lives on a visually
   hidden <input>; the older attribute forms are kept alongside it. */
[data-testid="stToggle"] [role="checkbox"][aria-checked="true"],
[data-testid="stCheckbox"] [data-baseweb="checkbox"] span[aria-checked="true"],
[data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type,
[data-testid="stToggle"] label:has(input:checked) > div:first-of-type {
    background: var(--gold-500) !important;
    border-color: var(--gold-500) !important;
    box-shadow: 0 0 14px rgba(212, 176, 120, 0.45);
}
[data-testid="stCheckbox"] label:has(input:focus-visible) > div:first-of-type {
    box-shadow: 0 0 0 3px rgba(212, 176, 120, 0.22);
}

/* Plotly's modebar is developer chrome; keep it out of the way until the
   pointer is actually over a chart. */
.modebar { opacity: 0 !important; transition: opacity 0.35s var(--ease); }
[data-testid="stPlotlyChart"]:hover .modebar { opacity: 0.55 !important; }
.modebar-btn svg { fill: var(--text-300) !important; }

/* ==================================================================
   SIDEBAR
   ================================================================== */
[data-testid="stSidebar"] {
    background: linear-gradient(178deg, #0A0D13 0%, #070910 100%) !important;
    border-right: 1px solid var(--line);
}
[data-testid="stSidebar"]::after {
    content: "";
    position: absolute;
    top: 0; right: 0; bottom: 0;
    width: 1px;
    background: linear-gradient(180deg, var(--line-gold), transparent 55%);
}
[data-testid="stSidebar"] * { color: var(--text-200); }
[data-testid="stSidebar"] [data-testid="stMetricValue"] { color: var(--text-100) !important; }

/* ==================================================================
   QUOTE TAPE — the dense terminal header line
   ================================================================== */
.tv-tape {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0 1.5rem;
    padding: 0.95rem 1.25rem;
    margin: 0.5rem 0 1.15rem;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: linear-gradient(120deg, rgba(212,176,120,0.075) 0%, rgba(255,255,255,0.028) 34%, rgba(255,255,255,0.01) 100%);
    backdrop-filter: blur(16px);
    box-shadow: var(--shadow-sm);
    position: relative;
    overflow: hidden;
    animation: reveal-up 0.65s var(--ease) both;
}
/* A slow highlight travelling left to right, like a live tape */
.tv-tape::after {
    content: "";
    position: absolute;
    top: 0; left: -60%;
    width: 60%; height: 100%;
    background: linear-gradient(96deg, transparent, rgba(212,176,120,0.09), transparent);
    animation: tape-sweep 7s var(--ease) infinite;
    pointer-events: none;
}
.tv-tape-sym {
    font-family: var(--font-display);
    font-size: 1.62rem;
    font-weight: 600;
    font-variation-settings: 'opsz' 144;
    letter-spacing: -0.02em;
    color: var(--text-100);
    line-height: 1.1;
    margin-right: 0.15rem;
}
.tv-tape-last {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 1.62rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    color: var(--text-100);
    line-height: 1.1;
}
.tv-tape-chg {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.94rem;
    font-weight: 600;
    padding: 0.24rem 0.6rem;
    border-radius: 8px;
    line-height: 1.3;
}
.tv-tape-chg.pos { color: var(--jade); background: var(--jade-dim); box-shadow: inset 0 0 0 1px rgba(95,207,155,0.24); }
.tv-tape-chg.neg { color: var(--rose); background: var(--rose-dim); box-shadow: inset 0 0 0 1px rgba(240,97,111,0.24); }
.tv-tape-sep {
    width: 1px; height: 30px;
    background: linear-gradient(180deg, transparent, var(--line-strong), transparent);
    margin: 0 0.25rem;
}
.tv-tape-item { display: flex; flex-direction: column; gap: 0.16rem; padding: 0.1rem 0; }
.tv-tape-k {
    font-family: var(--font-ui);
    font-size: 0.59rem;
    letter-spacing: 0.19em;
    text-transform: uppercase;
    color: var(--text-500);
    font-weight: 600;
}
.tv-tape-v {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.86rem;
    font-weight: 500;
    color: var(--text-100);
    white-space: nowrap;
}

/* ==================================================================
   VERDICT CARD — the headline read, given real presence
   ================================================================== */
.tv-verdict {
    position: relative;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 1.2rem;
    padding: 1.5rem 1.7rem;
    margin: 0.7rem 0 1rem;
    border-radius: var(--radius);
    border: 1px solid var(--line);
    background: linear-gradient(140deg, rgba(255,255,255,0.05), rgba(255,255,255,0.012));
    backdrop-filter: blur(16px);
    box-shadow: var(--shadow-sm);
    animation: reveal-up 0.7s var(--ease) both;
}
/* The tone colour enters as a soft glow bleeding in from the left edge,
   not as a coloured background — restraint reads as expensive. */
.tv-verdict::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 3px;
    background: var(--tone, var(--gold-500));
    box-shadow: 0 0 26px 2px var(--tone, var(--gold-500));
}
.tv-verdict::after {
    content: "";
    position: absolute;
    left: -10%; top: -60%;
    width: 45%; height: 220%;
    background: radial-gradient(closest-side, var(--tone-dim, rgba(212,176,120,0.16)), transparent 72%);
    pointer-events: none;
}
.tv-verdict-l { position: relative; z-index: 1; }
.tv-verdict-eyebrow {
    font-family: var(--font-ui);
    font-size: 0.6rem;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: var(--text-500);
    font-weight: 600;
    margin-bottom: 0.42rem;
}
.tv-verdict-val {
    font-family: var(--font-display);
    font-size: 2rem;
    font-weight: 600;
    font-variation-settings: 'opsz' 144;
    letter-spacing: -0.025em;
    line-height: 1.06;
    color: var(--tone-text, var(--text-100));
}
.tv-verdict-note {
    font-family: var(--font-ui);
    font-size: 0.8rem;
    color: var(--text-300);
    margin-top: 0.4rem;
    max-width: 46ch;
    line-height: 1.6;
}
.tv-verdict-r {
    position: relative;
    z-index: 1;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 2.5rem;
    font-weight: 700;
    letter-spacing: -0.045em;
    color: var(--tone-text, var(--gold-400));
    text-align: right;
    line-height: 1;
}
.tv-verdict-rsub {
    font-family: var(--font-ui);
    font-size: 0.6rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--text-500);
    text-align: right;
    margin-top: 0.36rem;
    font-weight: 600;
}

/* ==================================================================
   FACTOR BARS — grow from zero on every render
   ================================================================== */
.tv-bars { display: flex; flex-direction: column; gap: 0.9rem; margin: 0.4rem 0 0.2rem; }
.tv-bar-row { display: flex; flex-direction: column; gap: 0.36rem; }
.tv-bar-top { display: flex; justify-content: space-between; align-items: baseline; }
.tv-bar-name {
    font-family: var(--font-ui);
    font-size: 0.65rem;
    letter-spacing: 0.19em;
    text-transform: uppercase;
    color: var(--text-300);
    font-weight: 600;
}
.tv-bar-num {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--text-100);
}
.tv-bar-track {
    height: 5px;
    border-radius: 99px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
}
.tv-bar-fill {
    height: 100%;
    border-radius: 99px;
    background: var(--gold-grad);
    box-shadow: 0 0 14px rgba(212, 176, 120, 0.55);
    transform-origin: left center;
    animation: bar-grow 1.1s var(--ease) both;
}

/* ==================================================================
   FOOTER
   ================================================================== */
.tv-foot {
    margin-top: 3.4rem;
    padding-top: 1.5rem;
    position: relative;
    text-align: center;
}
.tv-foot::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--line-gold) 48%, transparent);
}
.tv-foot-mark {
    font-family: var(--font-display);
    font-size: 1.05rem;
    font-weight: 600;
    font-variation-settings: 'opsz' 144;
    letter-spacing: 0.02em;
    background: var(--gold-grad);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
}
.tv-foot-txt {
    font-family: var(--font-ui);
    font-size: 0.72rem;
    color: var(--text-500);
    letter-spacing: 0.04em;
    line-height: 1.9;
    margin-top: 0.35rem;
}

/* ==================================================================
   AUTH SCREEN
   ================================================================== */
.tv-auth {
    text-align: center;
    padding: 2.6rem 0 1.4rem;
    animation: reveal-up 0.85s var(--ease) both;
}
.tv-auth-mark {
    font-family: var(--font-display);
    font-size: 4rem;
    font-weight: 600;
    font-variation-settings: 'opsz' 144;
    letter-spacing: -0.035em;
    line-height: 1;
    background: linear-gradient(100deg, #C9BFA8 0%, #F6E9CE 20%, #FFFFFF 30%, #E4CB9E 45%, #A37F4A 72%, #D9BE8E 100%);
    background-size: 260% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: sheen 8s ease-in-out infinite;
}
.tv-auth-rule {
    width: 54px; height: 2px;
    margin: 1.15rem auto 1.05rem;
    border-radius: 2px;
    background: var(--gold-grad);
    box-shadow: 0 0 18px rgba(212, 176, 120, 0.6);
    animation: rule-in 1s var(--ease) 0.2s both;
}
.tv-auth-sub {
    font-family: var(--font-ui);
    font-size: 0.68rem;
    letter-spacing: 0.36em;
    text-transform: uppercase;
    color: var(--text-500);
    font-weight: 500;
}
.tv-auth-lede {
    font-family: var(--font-ui);
    font-size: 0.9rem;
    color: var(--text-300);
    max-width: 42ch;
    margin: 1.1rem auto 0;
    line-height: 1.7;
}

/* ==================================================================
   MOTION
   ================================================================== */
@keyframes page-in    { from { opacity: 0; } to { opacity: 1; } }
@keyframes reveal-up  { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
@keyframes fade-slide { from { opacity: 0; transform: translateY(8px); }  to { opacity: 1; transform: translateY(0); } }
@keyframes metric-in  { from { opacity: 0; transform: translateY(7px); filter: blur(4px); }
                        to   { opacity: 1; transform: translateY(0); filter: blur(0); } }
@keyframes bar-grow   { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes rule-in    { from { transform: scaleX(0); opacity: 0; } to { transform: scaleX(1); opacity: 1; } }
@keyframes sheen      { 0%, 100% { background-position: 130% 50%; } 50% { background-position: -30% 50%; } }
@keyframes tape-sweep { 0% { left: -60%; } 55%, 100% { left: 130%; } }
@keyframes pulse-ring {
    0%   { transform: scale(0.75); opacity: 0.9; }
    75%  { transform: scale(2.1);  opacity: 0; }
    100% { transform: scale(2.1);  opacity: 0; }
}

/* Staggered entrance: each top-level block arrives a beat after the one
   above it, so the page assembles rather than flashing into place. */
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(1)  { animation: fade-slide 0.55s var(--ease) 0.02s both; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(2)  { animation: fade-slide 0.55s var(--ease) 0.06s both; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(3)  { animation: fade-slide 0.55s var(--ease) 0.10s both; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(4)  { animation: fade-slide 0.55s var(--ease) 0.14s both; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(5)  { animation: fade-slide 0.55s var(--ease) 0.18s both; }
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] > div:nth-child(6)  { animation: fade-slide 0.55s var(--ease) 0.22s both; }

/* Columns stagger left-to-right so metric rows deal out like cards */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1) [data-testid="stMetric"] { animation-delay: 0.04s; }
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"] { animation-delay: 0.11s; }
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"] { animation-delay: 0.18s; }
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"] { animation-delay: 0.25s; }

/* Motion is a garnish, never a requirement. If the OS says reduce it,
   every animation and transition above switches off. */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation: none !important;
        transition: none !important;
        scroll-behavior: auto !important;
    }
}

/* ==================================================================
   RESPONSIVE — the layout has to survive a phone
   ================================================================== */
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"], .block-container { padding-top: 1.2rem !important; }
    .tv-mark { font-size: 1.95rem; }
    .tv-auth-mark { font-size: 2.7rem; }
    .tv-tagline { font-size: 0.6rem; letter-spacing: 0.24em; }
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.2rem !important; }
    h3 { font-size: 1.02rem !important; margin-top: 1.5rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
    [data-testid="stMetric"] { padding: 0.8rem 0.85rem; }
    .tv-tape { gap: 0 0.95rem; padding: 0.8rem 0.9rem; }
    .tv-tape-sym, .tv-tape-last { font-size: 1.18rem; }
    .tv-tape-sep { display: none; }
    .tv-verdict { padding: 1.15rem 1.2rem; }
    .tv-verdict-val { font-size: 1.5rem; }
    .tv-verdict-r { font-size: 1.85rem; }
    [data-baseweb="tab"] { padding: 0.42rem 0.66rem !important; font-size: 0.62rem !important; }
    [data-baseweb="tab"] p { font-size: 0.62rem !important; }
}
</style>
""", unsafe_allow_html=True)


@contextmanager
def card():
    """
    A bordered container that the stylesheet can reliably find.

    Streamlit gives bordered containers a hashed emotion class rather than
    a stable test id, and the id it used to expose was dropped in a later
    release — so styling them directly means re-checking the DOM on every
    Streamlit upgrade. Instead each card emits an invisible marker span,
    and the CSS selects the container via :has(). The marker's own element
    container is hidden, so this costs nothing visually.

    Used exactly like st.container(border=True):

        with card():
            st.write("...")
    """
    with st.container(border=True) as c:
        st.markdown('<span class="tv-card-mark"></span>', unsafe_allow_html=True)
        yield c


users = load_users()

# Only show the login/register screen if not already authenticated —
# avoids flashing it after a successful login on rerun.
if not st.session_state.get("authenticated"):
    # The sign-in screen is the first thing anyone sees, so it carries the
    # brand on its own: an oversized wordmark with a slow specular sweep,
    # a champagne rule, and the forms held in a narrow centre column
    # (roughly a 1:2:1 split) so they read as a considered panel rather
    # than a full-width sprawl of inputs.
    st.markdown(
        """
<div class="tv-auth">
  <div class="tv-auth-mark">Tickveil</div>
  <div class="tv-auth-rule"></div>
  <div class="tv-auth-sub">Market Intelligence Terminal</div>
  <div class="tv-auth-lede">
    Institutional-grade technical, fundamental and factor analysis —
    every number shown with the method that produced it.
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    _gutter_l, _auth_col, _gutter_r = st.columns([1, 2, 1])
    with _auth_col:
        login_tab, register_tab = st.tabs(["Sign in", "Create account"])

        with login_tab:
            with st.form("login_form"):
                login_username = st.text_input("Username")
                login_password = st.text_input("Password", type="password")
                login_submitted = st.form_submit_button(
                    "Enter terminal", type="primary", use_container_width=True
                )

            if login_submitted:
                user_record = users.get(login_username)
                if user_record and check_password(login_password, user_record["password_hash"]):
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = login_username
                    st.session_state["name"] = user_record.get("name", login_username)
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

        with register_tab:
            with st.form("register_form"):
                reg_name = st.text_input("Your name")
                reg_username = st.text_input("Choose a username")
                reg_password = st.text_input("Choose a password", type="password")
                reg_password_confirm = st.text_input("Confirm password", type="password")
                reg_submitted = st.form_submit_button(
                    "Create account", type="primary", use_container_width=True
                )

            if reg_submitted:
                if not reg_name or not reg_username or not reg_password:
                    st.error("Please fill in all fields.")
                elif reg_username in users:
                    st.error("That username is already taken.")
                elif reg_password != reg_password_confirm:
                    st.error("Passwords don't match.")
                elif len(reg_password) < 8:
                    st.error("Password should be at least 8 characters.")
                else:
                    users[reg_username] = {
                        "name": reg_name,
                        "password_hash": hash_password(reg_password),
                        "totp_enabled": False,
                        "totp_secret": None,
                    }
                    save_users(users)
                    st.success("Account created — head to the 'Sign in' tab.")

        st.markdown(
            '<div style="text-align:center;margin-top:1.6rem;font-family:Inter,sans-serif;'
            'font-size:0.66rem;letter-spacing:0.2em;text-transform:uppercase;color:#7E786C;">'
            'Passwords hashed with bcrypt · Optional TOTP two-factor</div>',
            unsafe_allow_html=True,
        )

    st.stop()

# --- Password auth succeeded. Now handle optional 2FA. ---
username = st.session_state["username"]
user_record = users[username]
totp_enabled = user_record.get("totp_enabled", False)

if totp_enabled and not st.session_state.get(f"totp_verified_{username}", False):
    st.markdown(
        """
<div class="tv-auth">
  <div class="tv-auth-mark">Tickveil</div>
  <div class="tv-auth-rule"></div>
  <div class="tv-auth-sub">Two-factor verification</div>
  <div class="tv-auth-lede">
    Enter the six-digit code from your authenticator app to unlock this session.
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    _g1, _totp_col, _g2 = st.columns([1, 2, 1])
    with _totp_col:
        with card():
            code = st.text_input("Authentication code", key="totp_code_input")
            if st.button("Verify code", type="primary", use_container_width=True):
                totp = pyotp.TOTP(user_record["totp_secret"])
                if totp.verify(code):
                    st.session_state[f"totp_verified_{username}"] = True
                    st.rerun()
                else:
                    st.error("Incorrect code — try again.")
    st.stop()



# ----------------------------------------------------------------------
# MASTHEAD — brand lockup on the left, live session status on the right.
#
# The wordmark is an anchor back to #tv-top: Tickveil is a single-page
# app, so "click the logo to go home" means "return to the top of this
# page" rather than navigating anywhere.
#
# The status pills are honest about what they show. Yahoo's free feed is
# delayed, so the pill says DELAYED FEED rather than implying a live
# exchange connection — an expensive-looking product that lies about its
# data source is just a nicer-looking lie.
# ----------------------------------------------------------------------
st.markdown('<div id="tv-top"></div>', unsafe_allow_html=True)
st.markdown(
    f"""
<div class="tv-masthead">
  <div class="tv-brandrow">
    <div>
      <a class="tv-brand" href="#tv-top">
        <span class="tv-mark">Tickveil</span><span class="tv-markdot"></span>
      </a>
      <div class="tv-tagline">Market Intelligence Terminal</div>
    </div>
    <div class="tv-statusrow">
      <span class="tv-pill"><span class="tv-dot"></span>Delayed feed</span>
      <span class="tv-pill">{datetime.now().strftime('%d %b %Y · %H:%M')}</span>
      <span class="tv-pill gold">{html_lib.escape(str(st.session_state.get('name', 'Account')))}</span>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)
st.caption(
    "Educational tool only. Shows public price data, indicators, news, and "
    "a statistical price range based on past volatility. Nothing here is "
    "a prediction or a buy/sell recommendation."
)


with st.expander("New to these terms? A short glossary"):
    st.markdown("""
- **SMA (Simple Moving Average)** — the average closing price over the last N days. Smooths out daily noise so the underlying trend is easier to see. A "20-day average" is just yesterday's price blended with the past month.
- **RSI (Relative Strength Index)** — a 0–100 score for how fast and how far a price has moved recently. Traditionally, above 70 is called "overbought" (may be due to cool off), below 30 "oversold" (may be due to bounce). These are rules of thumb, not guarantees.
- **MACD** — short for Moving Average Convergence Divergence. It compares a faster and slower moving average to gauge whether upward or downward momentum is building or fading.
- **Bollinger Bands** — a band drawn above and below the moving average, sized by how much the price has recently fluctuated. Price hugging the outer band suggests an unusually strong move relative to its own recent history.
- **Standard deviation ("1st"/"2nd deviation")** — a statistics term for how spread out past price moves have been. If daily moves were random, 1 standard deviation ("68% range") covers roughly 68% of past outcomes, and 2 standard deviations ("95% range") covers about 95%. Wider historical swings (higher volatility) mean a wider range here — it describes the past, not a forecast of the future.
- **Indicator Lean** — a simple count of how many well-known indicators (above) currently point up vs. down. It's shown for transparency, not because it's a validated trading strategy.
""")


# ----------------------------------------------------------------------
# 2. SIDEBAR — USER INPUTS
# ----------------------------------------------------------------------

# Compact identity + logout — full account settings (password, 2FA) live in the Settings tab.
st.sidebar.markdown(
    f"""
<div style="padding:0.2rem 0 1rem;">
  <div style="font-family:Inter,sans-serif;font-size:0.58rem;letter-spacing:0.26em;
              text-transform:uppercase;color:#7E786C;font-weight:600;">Signed in as</div>
  <div style="font-family:Fraunces,Georgia,serif;font-size:1.3rem;font-weight:600;
              letter-spacing:-0.02em;color:#F4F1EA;margin-top:0.28rem;">
    {html_lib.escape(str(st.session_state.get('name', '')))}
  </div>
</div>
""",
    unsafe_allow_html=True,
)
if st.sidebar.button("Log out", use_container_width=True):
    for _key in ["authenticated", "username", "name", f"totp_verified_{username}"]:
        st.session_state.pop(_key, None)
    st.rerun()

# ----------------------------------------------------------------------
# WATCHLIST PERSISTENCE — saves your watchlist to a small local file, one
# per user account, so it's still there next time you log in. Lives next
# to the app itself; nothing is sent anywhere.
# ----------------------------------------------------------------------
WATCHLIST_FILE = f"bullbear_watchlist_{username}.json"
DEFAULT_WATCHLIST = "AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, XOM, JNJ"


def load_saved_watchlist() -> str:
    try:
        with open(WATCHLIST_FILE, "r") as f:
            return json.load(f).get("watchlist", DEFAULT_WATCHLIST)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_WATCHLIST


def save_watchlist(watchlist_text: str) -> None:
    try:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump({"watchlist": watchlist_text}, f)
    except OSError:
        pass  # non-critical — worst case, it just doesn't persist this time


TELEGRAM_FILE = f"bullbear_telegram_{username}.json"


def load_saved_telegram() -> dict:
    try:
        with open(TELEGRAM_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"bot_token": "", "chat_id": ""}


def save_telegram(bot_token: str, chat_id: str) -> None:
    try:
        with open(TELEGRAM_FILE, "w") as f:
            json.dump({"bot_token": bot_token, "chat_id": chat_id}, f)
    except OSError:
        pass  # non-critical — worst case, it just doesn't persist this time


JOURNAL_FILE = f"bullbear_journal_{username}.json"


def load_journal() -> list[dict]:
    try:
        with open(JOURNAL_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_journal(entries: list[dict]) -> None:
    try:
        with open(JOURNAL_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except OSError:
        pass  # non-critical — worst case, it just doesn't persist this time


DIGEST_SNAPSHOT_FILE = f"bullbear_digest_snapshot_{username}.json"


def load_digest_snapshot() -> dict:
    try:
        with open(DIGEST_SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_digest_snapshot(snapshot: dict) -> None:
    try:
        with open(DIGEST_SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except OSError:
        pass  # non-critical — worst case, it just doesn't persist this time


# Tab labels are plain words, no emoji. The CSS renders them as an
# uppercase letterspaced segmented control — emoji in a navigation rail is
# the fastest way to make a finance product look like a hobby project, and
# the icons weren't carrying meaning the words didn't already carry.
tab_analysis, tab_fundamentals, tab_factors, tab_tradesetup, tab_journal, tab_digest, tab_multiasset, tab_calendar, tab_watchlist, tab_news, tab_settings = st.tabs(
    ["Analysis", "Fundamentals", "Factor Score", "Trade Setup", "Journal",
     "Daily Digest", "Multi-Asset", "Calendar", "Watchlist", "Market News", "Settings"]
)

with tab_analysis:
    # International markets: yfinance reaches these via ticker suffixes.
    # We auto-append the right one so you can just type the base symbol.
    MARKET_SUFFIX_MAP = {
        "US (default)": "",
        "Hong Kong (HKEX)": ".HK",
        "South Korea — KOSPI": ".KS",
        "South Korea — KOSDAQ": ".KQ",
        "Singapore (SGX)": ".SI",
    }
    CHART_OPTIONS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]
    # Maps each display option to a (yfinance period, yfinance interval) pair.
    # 1d/5d use intraday bars WITH pre/post-market data included (prepost=True)
    # so the chart doesn't just flatline at the last regular-session close —
    # it reflects overnight/extended-hours trading where available.
    CHART_PERIOD_MAP = {
        "1d": ("1d", "5m"),
        "5d": ("5d", "15m"),
        "1mo": ("1mo", "1d"),
        "3mo": ("3mo", "1d"),
        "6mo": ("6mo", "1d"),
        "1y": ("1y", "1d"),
        "2y": ("2y", "1d"),
        "5y": ("5y", "1d"),
    }

    # Command-bar style entry: type a ticker and press Enter (or click Run) —
    # everything needed to run the analysis lives in one form, so adjusting
    # sliders/dropdowns doesn't trigger a rerun until you're ready to go.
    with st.form("command_bar"):
        cb_col1, cb_col2 = st.columns([1, 2])
        with cb_col1:
            market_choice = st.selectbox(
                "Market",
                options=list(MARKET_SUFFIX_MAP.keys()),
                help="Pick a market and just type the base ticker/code — the right suffix is added automatically. E.g. Hong Kong: '0700' becomes '0700.HK'.",
            )
        with cb_col2:
            raw_ticker_input = st.text_input(
                "Stock ticker or code (e.g. AAPL, 0700, 005930, D05)", "AAPL",
                help="Press Enter to run — no need to click the button.",
            ).upper().strip()
        cb_col3, cb_col4 = st.columns(2)
        with cb_col3:
            period_choice = st.selectbox("Chart timeframe", options=CHART_OPTIONS, index=4)  # default "6mo"
        with cb_col4:
            horizon_days = st.slider("Price range horizon (trading days ahead)", 1, 30, 5)
        run_button = st.form_submit_button("Run analysis", type="primary", use_container_width=True)

    _suffix = MARKET_SUFFIX_MAP[market_choice]
    if _suffix and "." not in raw_ticker_input:
        ticker = raw_ticker_input + _suffix
    else:
        ticker = raw_ticker_input

    # Streamlit reruns the whole script on ANY widget interaction anywhere in
    # the app (switching a Trade Setup slider, editing the watchlist, toggling
    # a setting) — not just when this button is clicked. Without remembering
    # the last confirmed run, those unrelated reruns would wipe the Analysis/
    # Fundamentals tabs and replace them with the "enter a ticker" placeholder,
    # even though you already ran an analysis. Snapshotting the inputs here
    # keeps the results on screen until you explicitly click Run analysis again.
    if run_button:
        st.session_state["analysis_run"] = {
            "ticker": ticker,
            "period_choice": period_choice,
            "horizon_days": horizon_days,
        }

    st.divider()
    st.markdown("#### Live price ticker")
    live_mode = st.checkbox(
        "Auto-refresh this ticker's price",
        key="live_mode_toggle",
        help="Polls Yahoo Finance on a timer instead of only refreshing when you click a button. "
             "Still the same delayed Yahoo data as the rest of the app (not true real-time streaming) "
             "— this just checks it more often. More frequent polling means more requests to Yahoo's "
             "free API, which increases the chance of hitting its rate limit.",
    )
    live_interval = st.select_slider(
        "Refresh every", options=[15, 30, 60], value=30, key="live_interval_slider",
        format_func=lambda s: f"{s}s",
    ) if live_mode else None

with tab_watchlist:
    if "watchlist_text" not in st.session_state:
        st.session_state.watchlist_text = load_saved_watchlist()

    watchlist_input = st.text_area(
        "Tickers to scan (comma-separated)",
        key="watchlist_text",
        help="Edit this to any tickers you want compared. Saved automatically so it's here next time you open the app.",
    )
    scan_button = st.button("Scan watchlist")
    if scan_button:
        save_watchlist(st.session_state.watchlist_text)

with tab_settings:
    st.subheader("Telegram alerts")
    if "telegram_creds_loaded" not in st.session_state:
        _saved_tg = load_saved_telegram()
        st.session_state.telegram_bot_token = _saved_tg["bot_token"]
        st.session_state.telegram_chat_id = _saved_tg["chat_id"]
        st.session_state.telegram_creds_loaded = True

    telegram_enabled = st.checkbox(
        "Enable",
        help="Sends a neutral tilt update to a Telegram bot you control — not automatic 24/7 monitoring (see note below).",
    )

    if telegram_enabled:
        with st.expander("First time? 3-step setup (2 min)"):
            st.markdown(
                "1. In **Telegram**, message **@BotFather** → send `/newbot` → follow "
                "prompts → copy the **token** it gives you.\n"
                "2. Message your new bot anything (e.g. \"hi\").\n"
                "3. Visit `api.telegram.org/bot<TOKEN>/getUpdates` in a browser "
                "(replace `<TOKEN>` — no brackets) → find `\"chat\":{\"id\":...}` → "
                "that number is your **Chat ID**."
            )

        telegram_bot_token = st.text_input("Bot token", key="telegram_bot_token", type="password")
        telegram_chat_id = st.text_input("Chat ID", key="telegram_chat_id", type="password")
        if telegram_bot_token or telegram_chat_id:
            save_telegram(telegram_bot_token, telegram_chat_id)

        st.caption(
            "Only sends when you tap the button below — Streamlit can't run in "
            "the background and monitor unattended (that would need a separate "
            "always-on script)."
        )

        # This button sends whatever ticker/tilt was last computed by Run analysis.
        if st.button("Send last tilt to Telegram"):
            if "last_lean" not in st.session_state:
                st.warning("Run an analysis first (click 'Run analysis' above) — then this button sends that result.")
            elif not telegram_bot_token or not telegram_chat_id:
                st.error("Enter both a bot token and chat ID above first.")
            else:
                message = (
                    f"Tickveil update for {st.session_state['last_ticker']}: {st.session_state['last_lean']}. "
                    "Descriptive technical reading, not a buy/sell recommendation."
                )
                sent, info = send_telegram_message(telegram_bot_token, telegram_chat_id, message)
                if sent:
                    st.success("Sent.")
                else:
                    st.error(f"Couldn't send: {info}")
    else:
        telegram_bot_token, telegram_chat_id = "", ""

    st.divider()
    st.subheader("Account")
    st.markdown("**Change password**")
    with st.form("change_password_form"):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input("New password", type="password")
        new_pw_confirm = st.text_input("Confirm new password", type="password")
        pw_submitted = st.form_submit_button("Update password")

    if pw_submitted:
        if not check_password(current_pw, user_record["password_hash"]):
            st.error("Current password is incorrect.")
        elif new_pw != new_pw_confirm:
            st.error("New passwords don't match.")
        elif len(new_pw) < 8:
            st.error("New password should be at least 8 characters.")
        else:
            user_record["password_hash"] = hash_password(new_pw)
            save_users(users)
            st.success("Password updated.")

    st.divider()
    st.markdown("**Two-factor authentication (2FA)**")
    if totp_enabled:
        st.write("✅ 2FA is enabled on your account.")
        if st.button("Disable 2FA"):
            user_record["totp_enabled"] = False
            user_record["totp_secret"] = None
            save_users(users)
            st.rerun()
    else:
        st.write("2FA is off. Recommended for extra security — free, uses an app like Google Authenticator.")
        if st.button("Set up 2FA"):
            st.session_state["setting_up_2fa"] = True

        if st.session_state.get("setting_up_2fa"):
            if "pending_totp_secret" not in st.session_state:
                st.session_state["pending_totp_secret"] = pyotp.random_base32()

            secret = st.session_state["pending_totp_secret"]
            uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="Tickveil")
            qr_img = qrcode.make(uri)
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            st.image(buf.getvalue(), caption="Scan with Google Authenticator, Authy, etc.")
            st.caption(f"Or enter this code manually: `{secret}`")

            confirm_code = st.text_input("Enter the 6-digit code to confirm setup", key="confirm_2fa_code")
            if st.button("Confirm and enable 2FA"):
                if pyotp.TOTP(secret).verify(confirm_code):
                    user_record["totp_enabled"] = True
                    user_record["totp_secret"] = secret
                    save_users(users)
                    del st.session_state["setting_up_2fa"]
                    del st.session_state["pending_totp_secret"]
                    st.success("2FA enabled!")
                    st.rerun()
                else:
                    st.error("Incorrect code — try again.")




# ----------------------------------------------------------------------
# RATE-LIMIT HANDLING — yfinance scrapes Yahoo's public site rather than
# using an official paid API, and Yahoo rate-limits more aggressively for
# cloud-hosted traffic (many apps sharing the same IP range) than for a
# home connection. This retries briefly before giving up, and longer
# cache times below reduce how often we hit Yahoo in the first place.
# ----------------------------------------------------------------------
def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


def yf_call_with_retry(fn, retries: int = 2, delay_seconds: float = 4.0):
    """Runs a yfinance call, retrying briefly on rate-limit errors before giving up."""
    last_exception = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exception = e
            if _is_rate_limit_error(e) and attempt < retries:
                time.sleep(delay_seconds)
                continue
            raise
    raise last_exception


# ----------------------------------------------------------------------
# 3. FETCH PRICE DATA
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
def _clean_price_data(df: pd.DataFrame, ticker_symbol: str) -> pd.DataFrame:
    """
    Drops rows with a zero, negative, or missing Close price — occasional
    bad data points from Yahoo (trading halts, thin foreign-market
    liquidity, data gaps) that would otherwise produce inf/NaN in any
    downstream calculation that divides by price or takes its log. A
    SINGLE such row can silently turn an entire statistic (e.g. a 246-
    sample percentile) into NaN, which is what caused blank "nan" values
    in the price range and historical outcome sections.
    """
    cleaned = df[df["Close"] > 0].dropna(subset=["Close"]).copy()
    if cleaned.empty:
        raise ValueError(f"No usable price data for ticker '{ticker_symbol}' after removing invalid rows.")
    return cleaned


@st.cache_data(ttl=900)  # was 300s — longer cache means fewer Yahoo requests
def load_chart_data(ticker_symbol: str, yf_period: str, yf_interval: str) -> pd.DataFrame:
    """
    Data for the chart — resolution depends on the timeframe picked.
    prepost=True includes pre-market/after-hours bars for intraday views,
    so the chart doesn't just flatline at the last regular-session close.
    """
    stock = yf.Ticker(ticker_symbol)
    df = yf_call_with_retry(lambda: stock.history(period=yf_period, interval=yf_interval, prepost=True))
    if df.empty:
        raise ValueError(f"No data found for ticker '{ticker_symbol}'.")
    return _clean_price_data(df, ticker_symbol)


@st.cache_data(ttl=900)
def load_daily_data(ticker_symbol: str) -> pd.DataFrame:
    """
    Always-daily data (1 year), used for the Indicator Lean and the
    statistical price range — so those stay stable even when the chart
    above is zoomed into an intraday view.
    """
    stock = yf.Ticker(ticker_symbol)
    df = yf_call_with_retry(lambda: stock.history(period="1y", interval="1d"))
    if df.empty:
        raise ValueError(f"No daily data found for ticker '{ticker_symbol}'.")
    return _clean_price_data(df, ticker_symbol)


@st.cache_data(ttl=120)  # was 60s — still fairly fresh, but fewer calls
def get_realtime_price_info(ticker_symbol: str) -> dict:
    """
    Pulls whatever real-time/extended-hours price fields Yahoo has for this
    ticker right now: regular session price plus pre-market or after-hours
    price if the market is currently closed. Not all tickers/exchanges
    report extended-hours data (this is mainly a US thing) — fields are
    None when unavailable, and the app falls back to the last close.
    """
    stock = yf.Ticker(ticker_symbol)
    info = yf_call_with_retry(lambda: stock.info) or {}
    return {
        "market_state": info.get("marketState"),  # e.g. "REGULAR", "PRE", "POST", "CLOSED"
        "regular_price": info.get("regularMarketPrice"),
        "regular_change": info.get("regularMarketChange"),
        "regular_change_pct": info.get("regularMarketChangePercent"),
        "post_price": info.get("postMarketPrice"),
        "post_change": info.get("postMarketChange"),
        "pre_price": info.get("preMarketPrice"),
        "pre_change": info.get("preMarketChange"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "volume": info.get("volume") or info.get("regularMarketVolume"),
        "avg_volume": info.get("averageVolume"),
        "fifty_two_wk_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_wk_low": info.get("fiftyTwoWeekLow"),
        "currency": info.get("currency") or "USD",
    }


@st.cache_data(ttl=900)
def get_macro_backdrop() -> dict | None:
    """
    A purely descriptive VIX readout — where it sits today relative to its
    own trailing 6-month range. Deliberately NOT folded into any score or
    used as a multiplier on anything: VIX is a market-wide fear/uncertainty
    gauge, not a per-stock signal, and mixing it algebraically into a
    single-stock score is easy to get backwards (see engineering notes).
    Shown as context for the person to weigh themselves.
    """
    try:
        vix_df = load_daily_data("^VIX")
    except Exception:
        return None

    if len(vix_df) < 60:
        return None

    current = vix_df["Close"].iloc[-1]
    six_month = vix_df["Close"].iloc[-126:] if len(vix_df) >= 126 else vix_df["Close"]
    avg = six_month.mean()
    std = six_month.std()

    if std == 0 or pd.isna(std):
        level = "typical"
    elif current > avg + std:
        level = "elevated"
    elif current < avg - std:
        level = "low"
    else:
        level = "typical"

    return {"current": float(current), "six_month_avg": float(avg), "level": level}


def parse_gpr_upload(uploaded_file) -> dict | None:
    """
    Reads a user-uploaded Geopolitical Risk (GPR) index CSV — the
    Caldara & Iacoviello index, freely downloadable (not a live API) at
    matteoiacoviello.com/gpr.htm. Expects columns 'date' and 'gpr' (or
    close variants); this is explicitly optional and manual since no
    live free API exists for this data. Same descriptive-only treatment
    as VIX — no per-stock score, no risk-penalty multiplier.
    """
    try:
        df = pd.read_csv(uploaded_file)
    except Exception:
        return None

    cols_lower = {c.lower(): c for c in df.columns}
    date_col = cols_lower.get("date")
    gpr_col = cols_lower.get("gpr") or cols_lower.get("gpr_daily") or cols_lower.get("value")
    if date_col is None or gpr_col is None:
        return None

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col, gpr_col]).sort_values(date_col)
    if len(df) < 30:
        return None

    current = df[gpr_col].iloc[-1]
    six_month = df[gpr_col].iloc[-126:] if len(df) >= 126 else df[gpr_col]
    avg = six_month.mean()
    std = six_month.std()

    if std == 0 or pd.isna(std):
        level = "typical"
    elif current > avg + std:
        level = "elevated"
    elif current < avg - std:
        level = "low"
    else:
        level = "typical"

    return {"current": float(current), "six_month_avg": float(avg), "level": level,
            "as_of": df[date_col].iloc[-1].strftime("%Y-%m-%d")}


@st.cache_data(ttl=15)  # deliberately short — this is the ONLY thing "live mode" refreshes
def get_live_quote(ticker_symbol: str) -> dict:
    """
    A minimal, fast fetch used only by the live-ticker fragment — separate
    from get_realtime_price_info() so enabling "live mode" doesn't change
    caching/rate-limit behavior for the rest of the app. Still Yahoo data
    (delayed, polling-based) — "live" here means "auto-refreshing," not
    true real-time streaming.
    """
    stock = yf.Ticker(ticker_symbol)
    info = yf_call_with_retry(lambda: stock.info) or {}
    price = info.get("regularMarketPrice") or info.get("postMarketPrice") or info.get("preMarketPrice")
    return {
        "price": price,
        "change": info.get("regularMarketChange"),
        "change_pct": info.get("regularMarketChangePercent"),
        "currency": info.get("currency") or "USD",
        "market_state": info.get("marketState"),
    }


CURRENCY_SYMBOLS = {
    "USD": "$", "HKD": "HK$", "KRW": "₩", "SGD": "S$",
    "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
}


def money(value, currency_code: str = "USD") -> str:
    """Formats a number with the right currency symbol for the ticker's home market."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")
    return f"{symbol}{value:,.2f}"


@st.cache_data(ttl=3600)  # fundamentals barely change hour to hour — cache 1hr
def get_fundamentals(ticker_symbol: str) -> dict:
    """
    Pulls company fundamentals + analyst data from yfinance's info dict.
    Uses .get() defensively throughout since not every field is populated
    for every ticker (e.g. dividendYield is often missing for non-payers,
    and analyst target fields are often missing for non-US tickers).
    """
    stock = yf.Ticker(ticker_symbol)
    info = yf_call_with_retry(lambda: stock.info) or {}

    # Same reasoning as the dividend-yield handling used for display below:
    # "dividendYield" has been ambiguous across yfinance versions (fraction
    # vs. already-a-percent); trailingAnnualDividendYield has reliably
    # stayed a true fraction, so it's the authoritative source here too —
    # this feeds the Value factor's z-scoring, where a 100x-off yield
    # would badly distort the score.
    trailing_annual_yield = info.get("trailingAnnualDividendYield")
    dividend_rate = info.get("dividendRate")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    ambiguous_yield = info.get("dividendYield")
    if trailing_annual_yield is not None:
        dividend_yield_pct = trailing_annual_yield * 100
    elif dividend_rate and current_price:
        dividend_yield_pct = dividend_rate / current_price * 100
    elif ambiguous_yield is not None:
        dividend_yield_pct = ambiguous_yield * 100 if ambiguous_yield < 1 else ambiguous_yield
    else:
        dividend_yield_pct = None

    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "dividend_yield": info.get("dividendYield"),
        "trailing_annual_dividend_yield": info.get("trailingAnnualDividendYield"),
        "dividend_yield_pct": dividend_yield_pct,
        "trailing_eps": info.get("trailingEps"),
        "beta": info.get("beta"),
        "target_mean_price": info.get("targetMeanPrice"),
        "target_high_price": info.get("targetHighPrice"),
        "target_low_price": info.get("targetLowPrice"),
        "recommendation_key": info.get("recommendationKey"),
        "num_analyst_opinions": info.get("numberOfAnalystOpinions"),
        "currency": info.get("currency") or "USD",
        # --- extra fields for the Value factor (see FUNDAMENTAL FACTOR MODEL) ---
        "price_to_book": info.get("priceToBook"),
        "peg_ratio": info.get("pegRatio") or info.get("trailingPegRatio"),
        "ev_to_ebitda": info.get("enterpriseToEbitda"),
        "price_to_sales": info.get("priceToSalesTrailing12Months"),
        # --- extra fields for the Quality factor ---
        "return_on_equity": info.get("returnOnEquity"),
        "profit_margins": info.get("profitMargins"),
        "operating_margins": info.get("operatingMargins"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "free_cashflow": info.get("freeCashflow"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
    }


@st.cache_data(ttl=3600)
def get_peer_fundamentals_df(tickers: list[str]) -> pd.DataFrame:
    """
    Fetches fundamentals for a list of tickers (normally the user's own
    Watchlist) to use as the peer/comparison group for Value factor
    z-scoring. Skips any ticker that fails to load rather than failing
    the whole batch — a partial peer group is still useful.
    """
    rows = []
    for t in tickers:
        try:
            f = dict(get_fundamentals(t))
            f["ticker"] = t
            rows.append(f)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("ticker")


@st.cache_data(ttl=3600)
def get_earnings_and_dividends(ticker_symbol: str) -> dict:
    """
    Next earnings date (if available) and recent dividend history.
    yfinance's exact fields for earnings dates have shifted across
    versions, so this tries a couple of approaches defensively.
    """
    stock = yf.Ticker(ticker_symbol)
    next_earnings = None
    try:
        edates = yf_call_with_retry(lambda: stock.get_earnings_dates(limit=8))
        if edates is not None and not edates.empty:
            future = edates[edates.index > pd.Timestamp.now(tz=edates.index.tz)]
            if not future.empty:
                next_earnings = future.index.min()
            else:
                next_earnings = edates.index.max()  # most recent past one, as fallback
    except Exception:
        pass

    dividends = pd.Series(dtype=float)
    try:
        dividends = yf_call_with_retry(lambda: stock.dividends)
        if dividends is not None and not dividends.empty:
            dividends = dividends.tail(8)  # most recent 8 payments
    except Exception:
        pass

    return {"next_earnings": next_earnings, "dividends": dividends}


def format_market_cap(value, currency_code: str = "USD") -> str:
    """Formats a raw market cap number into X.XT / X.XB / X.XM with the right currency symbol."""
    if not value:
        return "N/A"
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")
    if value >= 1e12:
        return f"{symbol}{value / 1e12:.2f}T"
    elif value >= 1e9:
        return f"{symbol}{value / 1e9:.1f}B"
    elif value >= 1e6:
        return f"{symbol}{value / 1e6:.1f}M"
    return f"{symbol}{value:,.0f}"


def format_compact_number(value) -> str:
    """Formats a raw count (e.g. share volume) into X.XK/X.XM/X.XB — no currency symbol."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    if value >= 1e9:
        return f"{value / 1e9:.2f}B"
    elif value >= 1e6:
        return f"{value / 1e6:.1f}M"
    elif value >= 1e3:
        return f"{value / 1e3:.1f}K"
    return f"{value:,.0f}"


def render_quote_strip(ticker_symbol: str, rt: dict, fundamentals: dict, currency: str, display_price) -> None:
    """
    The quote tape: one dense line carrying last, change, volume, day range,
    52-week range, market cap and P/E — the header a professional terminal
    puts above everything else.

    Presentation notes: the symbol is set in the display serif and the price
    in tabular mono at the same optical size, so the two read as a matched
    pair rather than as a label and a number. The change chip is the only
    saturated colour on the line, which is what makes it the thing your eye
    lands on first. Secondary fields are stacked label-over-value in small
    caps, separated by hairline rules, so the line stays scannable at a
    glance instead of turning into a run-on string.

    Every field degrades to 'N/A' individually if Yahoo doesn't report it for
    this ticker, rather than the whole tape failing.
    """
    change = rt.get("regular_change")
    change_pct = rt.get("regular_change_pct")
    change_class = "pos" if (change or 0) >= 0 else "neg"
    change_str = f"{change:+.2f} ({change_pct:+.2f}%)" if change is not None and change_pct is not None else "N/A"

    day_range = (
        f"{money(rt['day_low'], currency)} – {money(rt['day_high'], currency)}"
        if rt.get("day_low") and rt.get("day_high") else "N/A"
    )
    year_range = (
        f"{money(rt['fifty_two_wk_low'], currency)} – {money(rt['fifty_two_wk_high'], currency)}"
        if rt.get("fifty_two_wk_low") and rt.get("fifty_two_wk_high") else "N/A"
    )
    volume_str = (
        f"{format_compact_number(rt['volume'])} · avg {format_compact_number(rt['avg_volume'])}"
        if rt.get("volume") else "N/A"
    )
    pe_str = f"{fundamentals['trailing_pe']:.1f}" if fundamentals.get("trailing_pe") else "N/A"
    mktcap_str = format_market_cap(fundamentals.get("market_cap"), currency)

    rest_items = '<div class="tv-tape-sep"></div>'.join(
        f'<div class="tv-tape-item"><span class="tv-tape-k">{label}</span>'
        f'<span class="tv-tape-v">{html_lib.escape(str(value))}</span></div>'
        for label, value in [
            ("Volume", volume_str), ("Day range", day_range), ("52-week", year_range),
            ("Market cap", mktcap_str), ("P/E", pe_str),
        ]
    )
    st.markdown(
        f'<div class="tv-tape">'
        f'<span class="tv-tape-sym">{html_lib.escape(ticker_symbol)}</span>'
        f'<span class="tv-tape-last">{html_lib.escape(money(display_price, currency))}</span>'
        f'<span class="tv-tape-chg {change_class}">{html_lib.escape(change_str)}</span>'
        f'<div class="tv-tape-sep"></div>'
        f'{rest_items}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------
# CHART THEME — one function every figure in the app passes through, so
# all of them share a single visual language instead of each carrying its
# own ad-hoc colours.
#
# The choices here are the standard "expensive chart" ones: kill the
# chart junk (no plot border, no vertical grid, no background fill),
# leave only faint horizontal rules for reading values off; move the
# price axis to the right, where every trading platform puts it; set all
# tick labels in tabular mono so digits line up column-wise; and give
# hover tooltips the same dark glass and champagne hairline as the cards
# they sit on. Legends go above the plot as a single row so they never
# cover data.
# ----------------------------------------------------------------------
CHART_GOLD = "#D4B078"
CHART_GOLD_SOFT = "#8C7B5C"  # muted champagne — the slower average sits behind the faster one
CHART_JADE = "#5FCF9B"
CHART_ROSE = "#F0616F"
CHART_TEXT = "#ABA598"
CHART_MUTED = "#7E786C"
CHART_GRID = "rgba(255,255,255,0.055)"


def style_chart(fig: go.Figure, height: int = 500, show_legend: bool = True) -> go.Figure:
    """Applies the house chart theme in place and returns the figure."""
    fig.update_layout(
        template="plotly_dark",
        height=height,
        # The right margin is deliberately generous: the price axis is on the
        # right (trading-platform convention) and Plotly's automargin does not
        # reliably reserve room for it when the chart is re-laid-out to the
        # container width, so the tick labels end up clipped at the edge.
        margin=dict(l=8, r=58, t=34, b=14),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", size=12, color=CHART_TEXT),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="rgba(11,14,21,0.95)",
            bordercolor="rgba(212,176,120,0.35)",
            font=dict(family="JetBrains Mono, monospace", size=11, color="#F4F1EA"),
        ),
        showlegend=show_legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, sans-serif", size=10, color=CHART_MUTED),
        ),
        xaxis=dict(
            showgrid=False, zeroline=False,
            linecolor="rgba(255,255,255,0.09)",
            tickfont=dict(family="JetBrains Mono, monospace", size=10, color=CHART_MUTED),
            rangeslider_visible=False, automargin=True,
            showspikes=True, spikemode="across", spikethickness=1,
            spikecolor="rgba(212,176,120,0.45)", spikedash="dot",
        ),
        yaxis=dict(
            showgrid=True, gridcolor=CHART_GRID, zeroline=False,
            linecolor="rgba(0,0,0,0)", side="right", automargin=True,
            tickfont=dict(family="JetBrains Mono, monospace", size=10, color=CHART_MUTED),
        ),
        # Plotly tweens between states on re-render rather than snapping.
        transition=dict(duration=420, easing="cubic-in-out"),
    )
    return fig


def add_price_glow(fig: go.Figure, df: pd.DataFrame, extra_cols: tuple = ()) -> None:
    """
    Lays a faint champagne wash under the price action and pins the y-axis
    to the data.

    Plotly can't do a true vertical gradient fill, so this fakes the useful
    part of one: a low-opacity area trace behind the candles, filled down
    to the axis floor. It reads as ambient light rather than as a second
    data series, which is why it's excluded from the legend and hover.

    The y-range is NOT optional here. A 'tozeroy' fill makes Plotly
    autoscale the axis down to zero, which squashes a $180 stock's entire
    range into the top sliver of the plot. Setting an explicit range over
    the real high/low (plus any extra series, e.g. Bollinger bands, that
    can sit outside it) both fixes that and lets the fill run off the
    bottom edge — which is what makes it read as a wash instead of a
    triangle.
    """
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Close"],
        mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(212,176,120,0.06)",
        hoverinfo="skip", showlegend=False, name="",
    ))

    cols = ["Low", "High", *extra_cols]
    values = pd.concat([df[c] for c in cols if c in df.columns]).dropna()
    if values.empty:
        return
    lo, hi = float(values.min()), float(values.max())
    pad = (hi - lo) * 0.07 or max(hi * 0.02, 0.01)
    fig.update_yaxes(range=[lo - pad, hi + pad])


# ----------------------------------------------------------------------
# DISPLAY COMPONENTS — the two pieces of custom chrome the built-in
# Streamlit widgets can't express: a headline verdict panel and an
# animated score bar set.
# ----------------------------------------------------------------------
# Directional tones. Each entry is (spine/glow colour, wash colour, text
# colour) — jade for bullish, rose for bearish, champagne for anything
# that hasn't picked a side.
VERDICT_TONES = {
    "bull":    ("#5FCF9B", "rgba(95,207,155,0.20)",  "#8FE3BC"),
    "bear":    ("#F0616F", "rgba(240,97,111,0.20)",  "#F79AA3"),
    "neutral": ("#D4B078", "rgba(212,176,120,0.18)", "#E4CB9E"),
}


def tone_for_lean(lean_text: str) -> str:
    """Maps an indicator-lean label onto one of the three display tones."""
    low = (lean_text or "").lower()
    if "bull" in low:
        return "bull"
    if "bear" in low:
        return "bear"
    return "neutral"


def render_verdict(eyebrow: str, value: str, tone: str = "neutral",
                   note: str = "", right: str = "", right_sub: str = "") -> None:
    """
    The headline read, given the presence it deserves.

    A verdict is the one thing a reader takes away from a screen, so it
    gets display-serif sizing and its own panel instead of being buried in
    a metric box the same size as everything else. Tone enters as a glowing
    spine on the left edge plus a soft radial wash — the panel itself stays
    neutral glass, because flooding a whole card with green or red is what
    cheap dashboards do.
    """
    spine, wash, text_col = VERDICT_TONES.get(tone, VERDICT_TONES["neutral"])
    right_html = ""
    if right:
        right_html = (
            f'<div><div class="tv-verdict-r">{html_lib.escape(str(right))}</div>'
            f'<div class="tv-verdict-rsub">{html_lib.escape(str(right_sub))}</div></div>'
        )
    note_html = f'<div class="tv-verdict-note">{html_lib.escape(note)}</div>' if note else ""
    st.markdown(
        f'<div class="tv-verdict" style="--tone:{spine};--tone-dim:{wash};--tone-text:{text_col};">'
        f'<div class="tv-verdict-l">'
        f'<div class="tv-verdict-eyebrow">{html_lib.escape(eyebrow)}</div>'
        f'<div class="tv-verdict-val">{html_lib.escape(str(value))}</div>'
        f'{note_html}</div>{right_html}</div>',
        unsafe_allow_html=True,
    )


def render_score_bars(rows: list[tuple[str, float | None]]) -> None:
    """
    Horizontal 0-100 bars that grow from zero on each render.

    Bars beat four side-by-side numbers here because the reader's actual
    question is 'which of these is pulling the score up' — a length
    comparison answers that pre-attentively, a set of digits doesn't.
    Missing sub-scores render as an empty track labelled N/A rather than
    being dropped, so the absence is visible instead of silent.
    """
    parts = []
    for i, (name, score) in enumerate(rows):
        pct = 0 if score is None else max(0.0, min(100.0, float(score)))
        shown = "N/A" if score is None else f"{score:.0f}"
        parts.append(
            f'<div class="tv-bar-row">'
            f'<div class="tv-bar-top"><span class="tv-bar-name">{html_lib.escape(name)}</span>'
            f'<span class="tv-bar-num">{shown}</span></div>'
            f'<div class="tv-bar-track"><div class="tv-bar-fill" '
            f'style="width:{pct:.1f}%;animation-delay:{0.08 * i + 0.1:.2f}s"></div></div>'
            f'</div>'
        )
    st.markdown(f'<div class="tv-bars">{"".join(parts)}</div>', unsafe_allow_html=True)


SECTOR_BENCHMARK_MAP = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}


def compute_risk_metrics(daily_df: pd.DataFrame) -> dict:
    """
    Max drawdown = the largest peak-to-trough decline in the lookback
    window. Annualized volatility scales daily volatility up to a
    yearly figure (roughly 252 trading days) so it's comparable across
    stocks regardless of how long each one's history is.
    Both describe the PAST — not a forecast of future risk.
    """
    close = daily_df["Close"]
    running_max = close.cummax()
    drawdown = close / running_max - 1
    max_drawdown = drawdown.min()

    annualized_vol = daily_df["LogReturn"].std() * np.sqrt(252)

    return {"max_drawdown": max_drawdown, "annualized_vol": annualized_vol}


# ----------------------------------------------------------------------
# GOAL-BASED TRADE PLANNER — you pick a plain target gain % and a max
# loss % you're comfortable with; this reports the resulting price levels
# plus how often THIS STOCK'S OWN PAST actually moved that much within
# that many trading days. It re-uses the exact same historical-percentile
# method as "Historical N-day outcomes" elsewhere in this app (real past
# rolling-window returns, not invented numbers) — this is standard
# empirical backtesting, the same category of method taught in
# quantitative-finance courses generally. It is NOT a specific named
# algorithm from any particular university or institution, and it is NOT
# a prediction — past frequency isn't a guarantee of future frequency,
# and a genuinely new event (earnings, news) can produce a move outside
# anything in the historical sample.
# ----------------------------------------------------------------------
def compute_goal_based_plan(daily_df: pd.DataFrame, target_gain_pct: float,
                             max_loss_pct: float, horizon_days: int, direction: str) -> dict | None:
    """
    direction: 'long' or 'short'. target_gain_pct / max_loss_pct are plain
    positive percentages (e.g. 10 means 10%), applied on the correct side
    of the current price for the chosen direction.
    """
    closes = daily_df["Close"]
    n = len(closes)
    if n < horizon_days + 30:
        return None  # not enough history for a meaningful sample of past windows

    last_price = closes.iloc[-1]
    forward_returns = np.array([
        (closes.iloc[i + horizon_days] - closes.iloc[i]) / closes.iloc[i]
        for i in range(n - horizon_days)
    ])
    forward_returns = forward_returns[np.isfinite(forward_returns)]  # a single inf/NaN would otherwise poison the mean below
    if len(forward_returns) < 20:
        return None

    target_frac = target_gain_pct / 100
    loss_frac = max_loss_pct / 100

    if direction == "long":
        target_price = last_price * (1 + target_frac)
        stop_price = last_price * (1 - loss_frac)
        hit_target_pct = float((forward_returns >= target_frac).mean() * 100)
        hit_stop_pct = float((forward_returns <= -loss_frac).mean() * 100)
    else:  # short
        target_price = last_price * (1 - target_frac)
        stop_price = last_price * (1 + loss_frac)
        hit_target_pct = float((forward_returns <= -target_frac).mean() * 100)
        hit_stop_pct = float((forward_returns >= loss_frac).mean() * 100)

    return {
        "last_price": last_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "hit_target_pct": hit_target_pct,
        "hit_stop_pct": hit_stop_pct,
        "sample_size": len(forward_returns),
    }


def compute_journal_pnl(entry: dict) -> dict:
    """
    Computes P/L and R-multiple for a closed journal entry. R-multiple is
    a standard trading metric: how many multiples of your INTENDED risk
    (entry-to-stop distance) you actually made or lost. An R of +2 means
    you made twice what you were risking; -1 means you took your full
    planned loss (hit your stop); -0.3 means you cut it early for a
    smaller-than-planned loss.
    """
    direction = entry["direction"]
    entry_price = entry["entry_price"]
    stop_loss = entry["stop_loss"]
    exit_price = entry["exit_price"]
    shares = entry["shares"]

    if direction == "long":
        pnl_per_share = exit_price - entry_price
        risk_per_share = entry_price - stop_loss
    else:
        pnl_per_share = entry_price - exit_price
        risk_per_share = stop_loss - entry_price

    pnl_dollars = pnl_per_share * shares
    pnl_pct = (pnl_per_share / entry_price * 100) if entry_price else None
    r_multiple = (pnl_per_share / risk_per_share) if risk_per_share and risk_per_share != 0 else None

    return {"pnl_dollars": pnl_dollars, "pnl_pct": pnl_pct, "r_multiple": r_multiple}


# ----------------------------------------------------------------------
# 4. TECHNICAL INDICATORS (works on any OHLC dataframe, daily or intraday)
# ----------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA20"] = df["Close"].rolling(window=20).mean()
    df["SMA50"] = df["Close"].rolling(window=50).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df["RSI14"] = 100 - (100 / (1 + rs))

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    std20 = df["Close"].rolling(window=20).std()
    df["BB_upper"] = df["SMA20"] + (2 * std20)
    df["BB_lower"] = df["SMA20"] - (2 * std20)

    df["LogReturn"] = np.log(df["Close"] / df["Close"].shift(1))

    return df


# ----------------------------------------------------------------------
# 5. INDICATOR LEAN (transparent heuristic — NOT a recommendation)
# ----------------------------------------------------------------------
def compute_indicator_lean(latest: pd.Series) -> tuple[str, list[str], int]:
    bullish_points = 0
    bearish_points = 0
    reasons = []

    if latest["Close"] > latest["SMA20"]:
        bullish_points += 1
        reasons.append("Price is above its 20-day average (short-term trend is upward).")
    else:
        bearish_points += 1
        reasons.append("Price is below its 20-day average (short-term trend is downward).")

    if latest["SMA20"] > latest["SMA50"]:
        bullish_points += 1
        reasons.append("20-day average is above the 50-day average (recent trend is stronger than the longer-term one).")
    else:
        bearish_points += 1
        reasons.append("20-day average is below the 50-day average (recent trend is weaker than the longer-term one).")

    if latest["MACD"] > latest["MACD_signal"]:
        bullish_points += 1
        reasons.append("MACD is above its signal line (momentum is strengthening).")
    else:
        bearish_points += 1
        reasons.append("MACD is below its signal line (momentum is weakening).")

    if 30 <= latest["RSI14"] <= 70:
        reasons.append(f"RSI ({latest['RSI14']:.0f}) is in a neutral range (neither stretched up nor down).")
    elif latest["RSI14"] > 70:
        bearish_points += 1
        reasons.append(f"RSI ({latest['RSI14']:.0f}) is in the overbought zone (price has risen quickly — may be due to cool off).")
    else:
        bullish_points += 1
        reasons.append(f"RSI ({latest['RSI14']:.0f}) is in the oversold zone (price has fallen quickly — may be due to bounce).")

    indicator_score = bullish_points - bearish_points  # ranges roughly -4 to +4

    if bullish_points > bearish_points:
        lean = f"Bullish tilt ({bullish_points} of 4 indicators positive)"
    elif bearish_points > bullish_points:
        lean = f"Bearish tilt ({bearish_points} of 4 indicators negative)"
    else:
        lean = "Mixed / no clear tilt"

    return lean, reasons, indicator_score


# ----------------------------------------------------------------------
# 6. STATISTICAL PRICE RANGE (volatility-based, NOT a prediction)
# ----------------------------------------------------------------------
def compute_price_range(df: pd.DataFrame, days_ahead: int, confidence: float = 0.68):
    log_returns = df["LogReturn"].replace([np.inf, -np.inf], np.nan)  # a bad price ratio can produce inf, not just NaN
    daily_vol = log_returns.std()
    if pd.isna(daily_vol):
        return None, None
    horizon_vol = daily_vol * np.sqrt(days_ahead)
    last_price = df["Close"].iloc[-1]
    z = 1.0 if confidence == 0.68 else 1.96
    upper = last_price * np.exp(z * horizon_vol)
    lower = last_price * np.exp(-z * horizon_vol)
    return lower, upper


def whats_changed_today(daily_df: pd.DataFrame) -> list[str]:
    """
    Factual, computed comparison of today's bar vs. yesterday's — only
    reports things that actually happened (a threshold crossed, a volume
    spike), never an invented narrative. Empty list if nothing notable.
    """
    if len(daily_df) < 21:
        return []

    today = daily_df.iloc[-1]
    yesterday = daily_df.iloc[-2]
    changes = []

    if pd.notna(today["RSI14"]) and pd.notna(yesterday["RSI14"]):
        if yesterday["RSI14"] >= 30 > today["RSI14"]:
            changes.append(f"RSI crossed below 30 today (now {today['RSI14']:.0f}) — entered the oversold zone.")
        elif yesterday["RSI14"] <= 70 < today["RSI14"]:
            changes.append(f"RSI crossed above 70 today (now {today['RSI14']:.0f}) — entered the overbought zone.")

    if pd.notna(today["SMA20"]) and pd.notna(yesterday["SMA20"]):
        if yesterday["Close"] <= yesterday["SMA20"] < today["Close"]:
            changes.append("Price crossed above its 20-day average today.")
        elif yesterday["Close"] >= yesterday["SMA20"] > today["Close"]:
            changes.append("Price crossed below its 20-day average today.")

    if pd.notna(today["MACD"]) and pd.notna(yesterday["MACD"]):
        if yesterday["MACD"] <= yesterday["MACD_signal"] < today["MACD"]:
            changes.append("MACD crossed above its signal line today.")
        elif yesterday["MACD"] >= yesterday["MACD_signal"] > today["MACD"]:
            changes.append("MACD crossed below its signal line today.")

    avg_volume_20d = daily_df["Volume"].iloc[-21:-1].mean()
    if avg_volume_20d and today["Volume"] > avg_volume_20d * 1.4:
        pct_above = (today["Volume"] / avg_volume_20d - 1) * 100
        changes.append(f"Volume today is {pct_above:.0f}% above its 20-day average — notably heavier trading than usual.")

    return changes


def historical_scenario_ranges(daily_df: pd.DataFrame, days_ahead: int) -> dict | None:
    """
    Instead of inventing bull/base/bear percentage outcomes, this looks at
    this stock's OWN actual historical N-day forward returns over the past
    year and reports real percentiles from that distribution. Still not a
    forecast — it's "here's the spread of what actually happened over
    similar-length windows in the past," which the person can weigh
    themselves rather than being handed a made-up number.
    """
    closes = daily_df["Close"]
    n = len(closes)
    if n < days_ahead + 30:
        return None

    forward_returns = [
        (closes.iloc[i + days_ahead] - closes.iloc[i]) / closes.iloc[i]
        for i in range(n - days_ahead)
    ]
    forward_returns = np.array(forward_returns)
    # A single inf/NaN in this array silently makes np.percentile return
    # NaN for the WHOLE result — filter to only finite values first.
    forward_returns = forward_returns[np.isfinite(forward_returns)]
    if len(forward_returns) < 20:
        return None

    return {
        "worst_10pct": np.percentile(forward_returns, 10) * 100,
        "median": np.percentile(forward_returns, 50) * 100,
        "best_10pct": np.percentile(forward_returns, 90) * 100,
        "sample_size": len(forward_returns),
    }


def compute_signal_agreement(indicator_score: int, news_items: list[dict]) -> str:
    """
    An honest alternative to a fabricated 'confidence %': just reports
    whether the technical read and headline tone happen to point the same
    way right now, or not. Not a probability — a simple factual observation.
    """
    if not news_items:
        return "Not enough headlines to compare against the technical read."

    sentiments = [tag_sentiment(i["title"] + " " + i.get("description", "")) for i in news_items]
    n_pos = sentiments.count("positive")
    n_neg = sentiments.count("negative")
    news_lean = "positive" if n_pos > n_neg else ("negative" if n_neg > n_pos else "mixed")
    tech_lean = "positive" if indicator_score > 0 else ("negative" if indicator_score < 0 else "mixed")

    if tech_lean == "mixed" or news_lean == "mixed":
        return "Signals are mixed — technical and headline tone don't clearly point the same way."
    elif tech_lean == news_lean:
        return "Aligned — both the technical read and headline tone point the same direction right now."
    else:
        return "Conflicting — the technical read and headline tone point in different directions right now."


# ----------------------------------------------------------------------
# 7. TEXT CLEANUP — strips HTML tags/entities out of RSS descriptions
# ----------------------------------------------------------------------
def clean_html(raw_text: str) -> str:
    """Removes HTML tags (e.g. <p>) and decodes entities (e.g. &amp;) from RSS text."""
    if not raw_text:
        return ""
    text = html_lib.unescape(raw_text)
    text = re.sub(r"<[^>]+>", " ", text)   # strip any tag like <p>, </p>, <a href=...>
    text = re.sub(r"\s+", " ", text).strip()  # collapse extra whitespace
    return text


# ----------------------------------------------------------------------
# 8. NEWS: headlines + links + cleaned short summaries
# ----------------------------------------------------------------------
@st.cache_data(ttl=600)  # was 300s
def get_news_items(ticker_symbol: str, max_items: int = 6) -> list[dict]:
    """
    Uses yfinance's own news lookup (tied to the specific ticker) instead
    of a generic RSS feed, since the RSS feed was returning unrelated
    market-wide stories. yfinance has changed its exact news response
    shape across versions, so this defensively checks a few possible
    key layouts rather than assuming one fixed structure.
    """
    stock = yf.Ticker(ticker_symbol)
    try:
        raw_news = yf_call_with_retry(lambda: stock.news) or []
    except Exception:
        raw_news = []

    items = []
    for n in raw_news[:max_items]:
        # Newer yfinance versions nest the actual fields under "content";
        # older versions put them directly on the item.
        content = n.get("content", n) if isinstance(n, dict) else {}

        title = content.get("title") or n.get("title") or ""
        if not title:
            continue  # skip anything we can't even get a headline from

        link = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or n.get("link")
            or ""
        )
        published = content.get("pubDate") or n.get("providerPublishTime") or ""
        description = content.get("summary") or content.get("description") or ""

        items.append({
            "title": clean_html(title),
            "link": link,
            "published": str(published),
            "description": clean_html(description),
        })
    return items


# ----------------------------------------------------------------------
# 9. NEWS SENTIMENT — finance-specific phrase rules FIRST, VADER as fallback
# ----------------------------------------------------------------------
# Why this exists: general-purpose sentiment tools (including VADER) score
# words like "record" and "profit" as strongly positive, so a headline
# like "SK Hynix record profit misses investors' AI expectations" gets
# tagged positive — even though "misses expectations" is the actual news
# that moves the stock. Earnings/guidance headlines routinely have this
# shape (good absolute number, bad RELATIVE surprise), which is exactly
# the case generic sentiment tools get backwards.
#
# Fix: check for specific, well-known finance-headline phrases first
# (miss/beat expectations, guidance cuts/raises, downgrades, recalls,
# etc.) — these are more reliable here because they're specific jargon
# patterns, not just word-level tone. Only fall back to VADER (general
# tone) when no such phrase is found. This still isn't perfect — no
# rule-based system catches every phrasing — but it fixes this specific,
# common class of error.
FINANCE_NEGATIVE_PATTERNS = [
    r"miss(?:es|ed)?\b.{0,25}\b(expectations|estimates|forecasts?)",
    r"below\b.{0,15}\b(expectations|estimates)",
    r"cuts?\b.{0,15}\b(guidance|forecast|outlook)",
    r"\bprofit warning\b",
    r"\bdowngrad\w*",
    r"\brecall(?:s|ed)?\b",
    r"\blawsuit\b",
    r"\blayoffs?\b",
    r"\binvestigat\w*",
    r"\bfraud\b",
    r"\bplunge\w*",
    r"\bslump\w*",
    r"warns?\b.{0,15}\b(profit|outlook|guidance)",
]
FINANCE_POSITIVE_PATTERNS = [
    r"beat(?:s)?\b.{0,25}\b(expectations|estimates|forecasts?)",
    r"above\b.{0,15}\b(expectations|estimates)",
    r"raise(?:s|d)?\b.{0,15}\b(guidance|forecast|outlook)",
    r"\bupgrad\w*",
    r"\bsurge\w*",
    r"\brally\w*",
    r"record\b.{0,15}\b(profit|revenue|earnings|high)",
]


@st.cache_resource
def get_sentiment_analyzer():
    return SentimentIntensityAnalyzer()


def tag_sentiment(text: str) -> str:
    """
    Returns 'positive', 'negative', or 'neutral'. Checks finance-specific
    phrase patterns first (negative patterns before positive ones, since
    "record profit BUT misses expectations" should read as the miss, not
    the record). Falls back to VADER's general tone score only if no
    specific phrase matches.
    """
    lower = text.lower()

    for pattern in FINANCE_NEGATIVE_PATTERNS:
        if re.search(pattern, lower):
            return "negative"

    for pattern in FINANCE_POSITIVE_PATTERNS:
        if re.search(pattern, lower):
            return "positive"

    analyzer = get_sentiment_analyzer()
    compound = analyzer.polarity_scores(text)["compound"]
    if compound >= 0.05:
        return "positive"
    elif compound <= -0.05:
        return "negative"
    return "neutral"


def compute_decayed_sentiment(news_items: list[dict], decay_lambda: float = 0.15) -> dict | None:
    """
    Weights each headline's sentiment by recency — a headline from today
    counts more than one from last week. Same idea as the time-decay
    weighting in the provided script, adapted for the handful of recent
    headlines this app actually has (not a full historical archive).

    Defensive by design: dates come from yfinance in inconsistent formats
    across versions, so parsing uses utc=True + errors='coerce' rather
    than raw subtraction — the original script's tz-naive/tz-aware
    subtraction would raise a TypeError the moment it hit a real mix of
    timezone-aware and naive dates, which live headline data does produce.
    Any headline whose date can't be parsed gets today's (max) weight
    rather than crashing or silently vanishing.
    """
    if not news_items:
        return None

    now = pd.Timestamp.now(tz="UTC")
    sentiment_value = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}

    weights, values = [], []
    for item in news_items:
        tag = tag_sentiment(item["title"] + " " + item.get("description", ""))
        values.append(sentiment_value[tag])

        parsed = pd.to_datetime(item.get("published"), utc=True, errors="coerce")
        if pd.isna(parsed):
            age_days = 0  # unknown date -> treat as fresh rather than crash or silently drop
        else:
            age_days = max((now - parsed).total_seconds() / 86400, 0)
        weights.append(np.exp(-decay_lambda * age_days))

    weights = np.array(weights)
    values = np.array(values)
    if weights.sum() == 0:
        return None

    weighted_avg = float(np.average(values, weights=weights))
    if weighted_avg > 0.15:
        label = "recent-weighted tone leans positive"
    elif weighted_avg < -0.15:
        label = "recent-weighted tone leans negative"
    else:
        label = "recent-weighted tone is roughly neutral"

    return {"score": weighted_avg, "label": label, "n_items": len(news_items)}


# ----------------------------------------------------------------------
# 10. FREE NARRATIVE — rule-based, no API, connects tilt + headline tone
# ----------------------------------------------------------------------
def generate_narrative(ticker_symbol: str, lean: str, reasons: list[str],
                        news_items: list[dict]) -> str:
    """
    Builds a plain-language paragraph from the indicator lean (grounded in
    real price data) and the tone of recent headlines (a much weaker,
    illustrative signal from simple keyword matching on a handful of
    articles). Deliberately does NOT treat a small headline tally as
    something meaningful enough to weigh in a buy/sell direction — a
    handful of headlines skewing one way is easy to happen by chance and
    says very little on its own.
    """
    sentiments = [tag_sentiment(i["title"] + " " + i.get("description", "")) for i in news_items]
    n_pos = sentiments.count("positive")
    n_neg = sentiments.count("negative")
    total = len(sentiments)

    lines = [
        f"The technical readout for {ticker_symbol} is: **{lean}**, based on where the "
        "price sits relative to its moving averages, RSI, and MACD — this part is "
        "grounded in actual price data.",
    ]

    if total == 0:
        lines.append("No recent headlines were found to add context.")
    else:
        # Only describe headline tone in soft, qualitative terms — no
        # implication that a few articles' wording is itself informative.
        if n_pos == n_neg:
            tone_desc = "an even mix of tone"
        elif abs(n_pos - n_neg) == 1:
            tone_desc = "essentially an even mix of tone (within a single headline of each other)"
        elif n_pos > n_neg:
            tone_desc = "a somewhat more positive-sounding mix of wording"
        else:
            tone_desc = "a somewhat more negative-sounding mix of wording"

        lines.append(
            f"The {total} recent headlines shown above have {tone_desc}, based on simple "
            "keyword matching (words like 'beat' or 'surge' vs. 'miss' or 'downgrade'). "
            "On its own, this is a weak signal — a small sample of headlines skewing one "
            "way is easy to happen by chance, and keyword matching can't tell sarcasm, "
            "negation, or genuine importance apart. It is not, by itself, a reasonable "
            "basis to buy or sell — read the actual headlines above for the substance."
        )

    lines.append(
        "This is a rule-based description of the current picture, not a forecast — "
        "keyword matching is blunt and can misread sarcasm, negation, or nuance, "
        "so treat it as a starting point for your own reading of the news, not a verdict."
    )
    return " ".join(lines)


def explain_like_im_5(ticker_symbol: str, lean: str, news_items: list[dict]) -> str:
    """
    A super-simplified, plain-language restatement of the SAME factual
    lean already computed above — no new claims, just simpler words.
    Deliberately still hedged; simplicity isn't a license to overstate.
    """
    if "Bullish" in lean:
        direction = f"a few signs are pointing up for {ticker_symbol} right now"
    elif "Bearish" in lean:
        direction = f"a few signs are pointing down for {ticker_symbol} right now"
    else:
        direction = f"the signals for {ticker_symbol} are mixed right now — no clear direction"

    return (
        f"In simple terms: {direction}, based on how the price has been moving "
        "compared to its own recent averages. That's just a description of the "
        "recent past, though — nobody, including this app, actually knows what "
        "happens next. Please don't treat this as advice to buy or sell."
    )


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 11. HISTORICAL SIGNAL CHECK (a fixed, honestly-caveated backtest)
# ----------------------------------------------------------------------
def historical_signal_check(daily_df: pd.DataFrame, forward_days: int = 5) -> dict | None:
    """
    Retrospectively checks whether the Indicator Lean's underlying score
    has lined up with this ONE stock's own subsequent price moves over
    its own past year. This is a single-asset, single-window check with
    no transaction costs and no out-of-sample data — a clean-looking
    correlation here is easy to get from noise alone and does NOT mean
    the same pattern will hold going forward.

    Reports a fuller statistical picture (R², p-value, directional hit
    rate, top-vs-bottom-quartile t-test) rather than a bare correlation
    number, so a small/noisy sample is visibly flagged as such (a high
    p-value or wide-but-overlapping quartiles) instead of just showing
    one number that looks more confident than it is.
    """
    scores, forward_returns = [], []
    n = len(daily_df)

    for i in range(60, n - forward_days):
        latest = daily_df.iloc[i]
        if pd.isna(latest[["SMA20", "SMA50", "RSI14", "MACD", "MACD_signal"]]).any():
            continue
        _, _, score = compute_indicator_lean(latest)
        future_ret = (daily_df["Close"].iloc[i + forward_days] - daily_df["Close"].iloc[i]) / daily_df["Close"].iloc[i]
        scores.append(score)
        forward_returns.append(future_ret)

    if len(scores) < 30:
        return None

    scores_arr = np.array(scores, dtype=float)
    returns_arr = np.array(forward_returns, dtype=float)

    slope, intercept, r_value, p_value, std_err = stats.linregress(scores_arr, returns_arr)
    hit_rate = float((np.sign(scores_arr) == np.sign(returns_arr)).mean() * 100)

    # Only run the quartile comparison if there's enough spread in the
    # (fairly coarse, -4..+4 integer) indicator score to form real quartiles.
    quartile_result = None
    if len(np.unique(scores_arr)) >= 3:
        top_cut = np.quantile(scores_arr, 0.75)
        bot_cut = np.quantile(scores_arr, 0.25)
        top_q = returns_arr[scores_arr >= top_cut]
        bot_q = returns_arr[scores_arr <= bot_cut]
        if len(top_q) >= 5 and len(bot_q) >= 5:
            t_stat, t_pvalue = stats.ttest_ind(top_q, bot_q, equal_var=False)
            quartile_result = {
                "top_quartile_mean_pct": float(top_q.mean() * 100),
                "bottom_quartile_mean_pct": float(bot_q.mean() * 100),
                "t_pvalue": float(t_pvalue),
            }

    return {
        "n_obs": len(scores_arr),
        "r_value": float(r_value),
        "r_squared": float(r_value ** 2),
        "p_value": float(p_value),
        "hit_rate_pct": hit_rate,
        "quartile": quartile_result,
    }


# ----------------------------------------------------------------------
# 11B. FUNDAMENTAL FACTOR MODEL — combines four independently well-known,
#      academically documented factor styles (Value, Quality, Momentum,
#      News Sentiment) into one transparent 0-100 score. Every sub-score
#      shows its own math (see the "Factor Score" tab) so nothing here is
#      a black box. This is a systematic SCREENING tool, not personalized
#      investment advice. A high score is not a guarantee of future
#      returns — factor investing describes a statistical tendency across
#      LARGE portfolios over LONG periods, not a guarantee for any single
#      stock at any single moment.
# ----------------------------------------------------------------------
def zscore_to_100(z: float, scale: float = 15.0) -> float:
    """
    Maps a z-score (standard deviations from a peer/benchmark average)
    onto a 0-100 scale centered at 50. With the default scale of 15, one
    standard deviation of "cheapness" or "outperformance" moves the score
    15 points, so it saturates at 0/100 by roughly z = ±3.3 — a genuinely
    rare reading, not a common one.
    """
    if z is None or (isinstance(z, float) and np.isnan(z)):
        return 50.0
    return float(np.clip(50 + scale * z, 0, 100))


# field: (fallback peer-group mean, fallback peer-group std dev, higher_is_cheaper)
# Fallbacks are rough, commonly-cited broad-market reference points, used
# ONLY when there aren't enough peer tickers (<3) for a real cross-
# sectional comparison — clearly labeled as such in the UI, not presented
# as a rigorous sector-specific "fair value."
VALUE_METRIC_ANCHORS = {
    "trailing_pe":        (20.0, 10.0, False),
    "forward_pe":         (18.0, 8.0, False),
    "price_to_book":      (3.5, 2.5, False),
    "peg_ratio":          (2.0, 1.5, False),
    "ev_to_ebitda":       (13.0, 6.0, False),
    "price_to_sales":     (3.0, 3.0, False),
    "dividend_yield_pct": (1.8, 1.5, True),
}
VALUE_METRIC_LABELS = {
    "trailing_pe": "P/E (trailing)",
    "forward_pe": "P/E (forward)",
    "price_to_book": "Price / Book",
    "peg_ratio": "PEG ratio",
    "ev_to_ebitda": "EV / EBITDA",
    "price_to_sales": "Price / Sales",
    "dividend_yield_pct": "Dividend yield %",
}


def compute_value_score(fundamentals: dict, peer_df: pd.DataFrame) -> dict:
    """
    Value Composite Score — the same core idea used by quant "value
    composite" indexes: combine several valuation ratios (P/E, forward
    P/E, P/B, PEG, EV/EBITDA, P/S, dividend yield) rather than trusting
    any single one, since any individual multiple can be misleading for
    a given company (e.g. a low P/E can mean "cheap" OR "the market
    expects earnings to fall").

    For each available metric: z = (this stock's value - comparison
    group average) / comparison group std dev. The comparison group is
    the user's Watchlist (a real cross-sectional peer comparison) when
    at least 3 peers have that metric; otherwise a fixed broad-market
    anchor. Ratios where LOWER is cheaper are sign-flipped so a positive
    z always means "looks cheaper than the comparison group." Dividend
    yield is not flipped (higher yield is treated as cheaper, generally).
    The final score is the plain average of every available per-metric
    score (each mapped 0-100 via zscore_to_100).
    """
    rows = []
    for field, (fallback_mean, fallback_std, higher_is_cheaper) in VALUE_METRIC_ANCHORS.items():
        value = fundamentals.get(field)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue

        peer_values = None
        source = "broad-market anchor (not enough watchlist peers)"
        if peer_df is not None and not peer_df.empty and field in peer_df.columns:
            peer_series = pd.to_numeric(peer_df[field], errors="coerce").dropna()
            if len(peer_series) >= 3:
                peer_values = peer_series
                source = f"{len(peer_series)}-ticker watchlist peer group"

        if peer_values is not None:
            mean, std = float(peer_values.mean()), float(peer_values.std())
        else:
            mean, std = fallback_mean, fallback_std

        if not std or pd.isna(std):
            continue

        z = (value - mean) / std
        if not higher_is_cheaper:
            z = -z  # flip so positive z always means "cheaper"

        rows.append({
            "metric": VALUE_METRIC_LABELS[field],
            "value": value,
            "peer_mean": mean,
            "peer_std": std,
            "z": z,
            "score": zscore_to_100(z),
            "source": source,
        })

    if not rows:
        return {"score": None, "rows": [], "note": "No valuation metrics available for this ticker."}

    return {"score": float(np.mean([r["score"] for r in rows])), "rows": rows, "note": None}


def compute_quality_score(fundamentals: dict) -> dict:
    """
    Quality Composite — inspired by Piotroski's F-Score, a well-documented
    academic method of scoring financial health with simple pass/fail
    accounting checks instead of one ratio. This is a lighter, simplified
    version limited to what yfinance exposes (not full financial-statement
    line items), so treat it as a cousin of the original, not a
    reproduction of it. Score = (checks passed) / (checks with data) × 100
    — a ticker missing some fields is scored on whatever it does have,
    not silently penalized for the rest.
    """
    checks = []
    roe = fundamentals.get("return_on_equity")
    if roe is not None:
        checks.append(("Return on equity is positive", roe > 0))
    profit_margin = fundamentals.get("profit_margins")
    if profit_margin is not None:
        checks.append(("Profit margin is positive", profit_margin > 0))
    op_margin = fundamentals.get("operating_margins")
    if op_margin is not None:
        checks.append(("Operating margin is positive", op_margin > 0))
    debt_equity = fundamentals.get("debt_to_equity")
    if debt_equity is not None:
        checks.append(("Debt/Equity below 100% (conservative leverage)", debt_equity < 100))
    current_ratio = fundamentals.get("current_ratio")
    if current_ratio is not None:
        checks.append(("Current ratio above 1 (covers short-term liabilities)", current_ratio > 1))
    fcf = fundamentals.get("free_cashflow")
    if fcf is not None:
        checks.append(("Free cash flow is positive", fcf > 0))
    rev_growth = fundamentals.get("revenue_growth")
    if rev_growth is not None:
        checks.append(("Revenue grew year-over-year", rev_growth > 0))
    earnings_growth = fundamentals.get("earnings_growth")
    if earnings_growth is not None:
        checks.append(("Earnings grew year-over-year", earnings_growth > 0))

    if not checks:
        return {"score": None, "checks": [], "note": "No quality/financial-health metrics available for this ticker."}

    passed = sum(1 for _, ok in checks if ok)
    return {"score": passed / len(checks) * 100, "checks": checks, "note": None}


def compute_momentum_score(daily_df: pd.DataFrame, benchmark_df: pd.DataFrame | None, indicator_score: int) -> dict:
    """
    Blends two independent, well-documented momentum readings:

    1. "12-1 momentum" — the stock's own return over the past ~12 months,
       EXCLUDING the most recent month. This is the specific definition
       from Jegadeesh & Titman (1993), one of the most replicated
       findings in academic finance. The most recent month is excluded
       on purpose: very short-term moves tend to partially REVERSE
       (mean-revert), while the 2-to-12-month window tends to persist.
       Measured relative to a sector benchmark (or SPY) over the
       identical window, then converted to a z-score-like reading using
       a rule-of-thumb ~15 percentage-point "1 standard deviation" of
       individual-stock dispersion (a reasonable approximation, not a
       measured value from this specific dataset).
    2. This app's existing short-term Indicator Lean score (SMA cross,
       MACD vs. signal, RSI zone) — a faster-moving daily reading.

    The two are averaged when both are available, so this factor blends
    a slow, historically-validated signal with a fast, transparent one.
    """
    technical_score = (indicator_score + 4) / 8 * 100
    result = {
        "stock_12_1": None, "benchmark_12_1": None, "relative_12_1": None,
        "momentum_12_1_score": None, "technical_score": technical_score,
        "score": technical_score, "lookback_days": None,
        "note": "Not enough price history (need at least ~6 months) for 12-1 momentum — using the short-term technical score only.",
    }

    # A plain "1y" daily fetch typically comes back with ~250-252 rows
    # (trading days per calendar year, after holidays) — just short of a
    # rigid 253-day requirement, which would make this silently unavailable
    # almost always. Instead, use as much of the available history as
    # there is, up to ~253 days, and require only ~6 months (130 trading
    # days) as a minimum for the reading to be meaningful at all. The
    # actual window used is reported in "lookback_days" so the UI can be
    # honest about it when it's short of a full 12 months.
    MIN_LOOKBACK = 130
    RECENT_SKIP = 21  # ~1 month, excluded to avoid the short-term reversal effect

    def _12_1_return(closes: pd.Series):
        n = len(closes)
        if n < MIN_LOOKBACK + RECENT_SKIP:
            return None, None
        anchor_idx = max(0, n - 253)
        recent_idx = n - 1 - RECENT_SKIP
        return float(closes.iloc[recent_idx] / closes.iloc[anchor_idx] - 1), recent_idx - anchor_idx

    stock_12_1, stock_lookback = _12_1_return(daily_df["Close"])
    if stock_12_1 is not None:
        result["stock_12_1"] = stock_12_1
        result["lookback_days"] = stock_lookback

        if benchmark_df is not None:
            benchmark_12_1, _ = _12_1_return(benchmark_df["Close"])
            if benchmark_12_1 is not None:
                relative = stock_12_1 - benchmark_12_1
                result["benchmark_12_1"] = benchmark_12_1
                result["relative_12_1"] = relative
                result["momentum_12_1_score"] = zscore_to_100(relative / 0.15)
                result["score"] = (result["momentum_12_1_score"] + technical_score) / 2
                result["note"] = None

    return result


def _parse_news_timestamp(published: str) -> datetime | None:
    """
    yfinance news timestamps show up as either an ISO string
    ('2026-08-08T14:32:00Z') or a raw Unix epoch (as a string, from older
    yfinance versions) — this handles either, returning None if neither
    parses (that headline then just gets a neutral default weight below
    instead of being dropped).
    """
    if not published:
        return None
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        return datetime.utcfromtimestamp(float(published))
    except (ValueError, TypeError):
        return None


def compute_news_sentiment_score(news_items: list[dict], half_life_hours: float = 24.0) -> dict:
    """
    Recency-weighted sentiment — "instant" news (the last few hours)
    counts far more than "latest" news from a few days ago, and week-old
    headlines fade out almost entirely. Each headline's sentiment
    (+1 / 0 / -1, from this app's finance-aware tag_sentiment) is weighted
    by 0.5 ^ (age_in_hours / half_life_hours): a headline exactly one
    half-life old counts for half as much as a brand-new one, two
    half-lives old a quarter, and so on. Headlines with no parseable
    timestamp get a neutral 0.5 weight so they still count a little
    rather than being silently dropped.
    Score = weighted-average sentiment, rescaled from [-1, 1] to [0, 100].
    """
    if not news_items:
        return {"score": 50.0, "rows": [], "note": "No recent headlines — defaulting to a neutral 50."}

    now = datetime.utcnow()
    rows = []
    weighted_sum = 0.0
    weight_total = 0.0

    for item in news_items:
        sentiment = tag_sentiment(item["title"] + " " + item.get("description", ""))
        value = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}[sentiment]

        ts = _parse_news_timestamp(item.get("published", ""))
        if ts is not None:
            age_hours = max((now - ts).total_seconds() / 3600, 0)
            weight = 0.5 ** (age_hours / half_life_hours)
        else:
            age_hours = None
            weight = 0.5

        weighted_sum += value * weight
        weight_total += weight
        rows.append({"title": item["title"], "sentiment": sentiment, "age_hours": age_hours, "weight": weight})

    weighted_avg = weighted_sum / weight_total if weight_total > 0 else 0.0
    return {"score": (weighted_avg + 1) / 2 * 100, "rows": rows, "note": None}


DEFAULT_FACTOR_WEIGHTS = {"value": 0.35, "quality": 0.20, "momentum": 0.30, "sentiment": 0.15}


def compute_factor_composite(value_result: dict, quality_result: dict,
                              momentum_result: dict, sentiment_result: dict,
                              weights: dict) -> dict:
    """
    Weighted average of the four factor scores, using only the factors
    that actually have data — weights are renormalized over whatever's
    available, so a ticker missing e.g. Quality data isn't silently
    scored as if it failed that factor.
    """
    components = {
        "value": value_result["score"],
        "quality": quality_result["score"],
        "momentum": momentum_result["score"],
        "sentiment": sentiment_result["score"],
    }
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return {"score": None, "components": components, "used_weights": {}}

    used_weights = {k: weights.get(k, 0) for k in available}
    weight_sum = sum(used_weights.values())
    if weight_sum > 0:
        used_weights = {k: w / weight_sum for k, w in used_weights.items()}
    else:
        used_weights = {k: 1 / len(available) for k in available}

    total = sum(available[k] * used_weights[k] for k in available)
    return {"score": total, "components": components, "used_weights": used_weights}


def factor_score_verdict(score: float) -> tuple[str, str]:
    """
    Bands the 0-100 composite into a plain-language label. The cut points
    are round, easy-to-read thresholds chosen for readability — not
    statistically fit to any outcome.
    """
    if score >= 70:
        return "Strong on these factors", "🟢"
    elif score >= 55:
        return "Above-average on these factors", "🟡"
    elif score >= 45:
        return "Mixed / neutral on these factors", "⚪"
    elif score >= 30:
        return "Below-average on these factors", "🟠"
    else:
        return "Weak on these factors", "🔴"


# ----------------------------------------------------------------------
# 12. PORTFOLIO ALLOCATION (backward-looking Sharpe optimization)
# ----------------------------------------------------------------------
def optimize_portfolio(tickers: list[str], iterations: int = 3000):
    """
    Randomly samples portfolio weightings and keeps whichever had the best
    historical Sharpe ratio (return divided by volatility, ignoring a
    risk-free rate) over the lookback window. IMPORTANT: this literally
    finds whatever WOULD have worked best in the past — a well-known trap
    among quants (sometimes called Sharpe-ratio overfitting), since the
    mix of stocks that happened to do best historically is not reliably
    the mix that will do best going forward. Shown for research interest,
    not as a recommended allocation.
    """
    price_data = {}
    for t in tickers:
        try:
            price_data[t] = load_daily_data(t)["Close"]
        except Exception:
            continue

    if len(price_data) < 2:
        return None, None

    prices = pd.DataFrame(price_data).dropna()
    returns = prices.pct_change().dropna()
    mean_returns = returns.mean()
    cov = returns.cov()
    valid_tickers = list(prices.columns)

    best_sharpe = -np.inf
    best_weights = None
    for _ in range(iterations):
        w = np.random.random(len(valid_tickers))
        w /= w.sum()
        port_return = np.dot(w, mean_returns)
        port_vol = np.sqrt(np.dot(w.T, np.dot(cov, w)))
        sharpe_ratio = port_return / port_vol if port_vol != 0 else -np.inf
        if sharpe_ratio > best_sharpe:
            best_sharpe = sharpe_ratio
            best_weights = w

    return dict(zip(valid_tickers, best_weights)), best_sharpe


# ----------------------------------------------------------------------
# 13. TELEGRAM NOTIFICATIONS — neutral tilt updates, sent manually.
#     Note: Streamlit only runs when someone has the page open or clicks
#     something. This can NOT silently monitor the market and text you
#     unattended 24/7 — true background monitoring needs separate
#     infrastructure (e.g. a scheduled script running on its own).
# ----------------------------------------------------------------------
def send_telegram_message(bot_token: str, chat_id: str, message: str) -> tuple[bool, str]:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        resp.raise_for_status()
        return True, "Sent."
    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------------------
# DAILY DIGEST — a one-screen "here's what to look at today" summary,
# pulling together watchlist tilt CHANGES (not just current state),
# open Trade Journal positions vs. their stop/target, and upcoming
# earnings this week. Generated on demand (button click), not on a
# schedule — see the note below on why.
# ----------------------------------------------------------------------
def generate_daily_digest(watchlist_tickers: list[str]) -> dict:
    previous_snapshot = load_digest_snapshot()
    new_snapshot = {}
    tilt_changes = []
    tilt_unchanged = []

    for t in watchlist_tickers:
        try:
            df = add_indicators(load_daily_data(t))
            lean, _reasons, _score = compute_indicator_lean(df.iloc[-1])
            new_snapshot[t] = lean
            prev_lean = previous_snapshot.get(t)
            if prev_lean and prev_lean != lean:
                tilt_changes.append({"Ticker": t, "Was": prev_lean, "Now": lean})
            else:
                tilt_unchanged.append({"Ticker": t, "Current tilt": lean})
        except Exception:
            continue

    save_digest_snapshot(new_snapshot)

    # --- Open journal positions vs. current price ---
    open_positions = []
    for entry in load_journal():
        if entry["status"] != "open":
            continue
        try:
            quote = get_live_quote(entry["ticker"])
            current_price = quote["price"]
            if current_price is None:
                continue
            if entry["direction"] == "long":
                pct_to_stop = (current_price - entry["stop_loss"]) / entry["stop_loss"] * 100
                pct_to_target = ((entry["take_profit"] - current_price) / current_price * 100
                                 if entry.get("take_profit") else None)
            else:
                pct_to_stop = (entry["stop_loss"] - current_price) / current_price * 100
                pct_to_target = ((current_price - entry["take_profit"]) / current_price * 100
                                  if entry.get("take_profit") else None)
            open_positions.append({
                "Ticker": entry["ticker"], "Direction": entry["direction"],
                "Current price": current_price, "Entry": entry["entry_price"],
                "% cushion to stop": round(pct_to_stop, 1),
                "% to target": round(pct_to_target, 1) if pct_to_target is not None else None,
            })
        except Exception:
            continue

    # --- Upcoming earnings within 7 days ---
    upcoming_earnings = []
    today = pd.Timestamp.now().normalize()
    for t in watchlist_tickers:
        try:
            ed = get_earnings_and_dividends(t)
            next_date = ed["next_earnings"]
            if next_date is not None:
                days_away = (pd.Timestamp(next_date).tz_localize(None).normalize() - today).days
                if 0 <= days_away <= 7:
                    upcoming_earnings.append({"Ticker": t, "Earnings date": next_date.strftime("%Y-%m-%d"), "In days": days_away})
        except Exception:
            continue

    return {
        "tilt_changes": tilt_changes,
        "tilt_unchanged": tilt_unchanged,
        "open_positions": open_positions,
        "upcoming_earnings": sorted(upcoming_earnings, key=lambda r: r["In days"]),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def format_digest_for_telegram(digest: dict) -> str:
    lines = [f"📋 Tickveil Daily Digest — {digest['generated_at']}", ""]

    if digest["tilt_changes"]:
        lines.append("🔄 Tilt changes since last check:")
        for c in digest["tilt_changes"]:
            lines.append(f"  {c['Ticker']}: {c['Was']} → {c['Now']}")
    else:
        lines.append("🔄 No tilt changes since last check.")

    if digest["upcoming_earnings"]:
        lines.append("")
        lines.append("📅 Earnings this week:")
        for e in digest["upcoming_earnings"]:
            lines.append(f"  {e['Ticker']}: {e['Earnings date']} ({e['In days']}d)")

    if digest["open_positions"]:
        lines.append("")
        lines.append("📓 Open journal positions:")
        for p in digest["open_positions"]:
            target_str = f", {p['% to target']:+.1f}% to target" if p["% to target"] is not None else ""
            lines.append(f"  {p['Ticker']} ({p['Direction']}): {p['% cushion to stop']:+.1f}% cushion to stop{target_str}")

    lines.append("")
    lines.append("Descriptive summary only — not trading advice.")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 14. WATCHLIST SCAN — ranks multiple tickers by the same transparent
#     heuristics used above (indicator lean + headline tone). This is a
#     ranked scan of well-known signals, not a "best stocks" verdict.
# ----------------------------------------------------------------------
def scan_watchlist(tickers: list[str]) -> pd.DataFrame:
    rows = []
    progress = st.progress(0.0, text="Starting scan...")

    # The whole watchlist doubles as its own Value-factor peer group here —
    # a genuine cross-sectional comparison rather than the broad-market
    # fallback anchors used when scoring a single ticker in isolation.
    peer_df = get_peer_fundamentals_df(tickers)

    for idx, t in enumerate(tickers):
        progress.progress((idx) / max(len(tickers), 1), text=f"Scanning {t}...")
        try:
            df = load_daily_data(t)
            df = add_indicators(df)
            latest = df.iloc[-1]
            lean, _reasons, indicator_score = compute_indicator_lean(latest)

            news = get_news_items(t, max_items=5)
            sentiments = [tag_sentiment(i["title"] + " " + i.get("description", "")) for i in news]
            n_pos = sentiments.count("positive")
            n_neg = sentiments.count("negative")

            combined_score = indicator_score + (n_pos - n_neg)

            fundamentals = get_fundamentals(t)
            sector = fundamentals.get("sector") or "Unknown"

            benchmark_ticker = SECTOR_BENCHMARK_MAP.get(sector, "SPY")
            try:
                benchmark_df = load_daily_data(benchmark_ticker)
            except Exception:
                benchmark_df = None

            value_result = compute_value_score(fundamentals, peer_df)
            quality_result = compute_quality_score(fundamentals)
            momentum_result = compute_momentum_score(df, benchmark_df, indicator_score)
            sentiment_result = compute_news_sentiment_score(news)
            composite = compute_factor_composite(
                value_result, quality_result, momentum_result, sentiment_result, DEFAULT_FACTOR_WEIGHTS
            )

            rows.append({
                "Ticker": t,
                "Last price": round(float(latest["Close"]), 2),
                "Sector": sector,
                "Indicator lean": lean,
                "Headlines +/-": f"{n_pos}/{n_neg}",
                "Combined tilt score": combined_score,
                "Factor score": round(composite["score"], 1) if composite["score"] is not None else None,
            })
        except Exception:
            continue  # skip tickers that fail to load (bad symbol, no data, etc.)

    progress.progress(1.0, text="Done.")
    progress.empty()
    return pd.DataFrame(rows)


def sector_concentration(results_df: pd.DataFrame) -> pd.DataFrame:
    """
    Factual sector breakdown of the watchlist — equal-weighted (counts each
    ticker once, not by dollar amount, since we don't know your actual
    position sizes). Flags concentration, doesn't recommend a fix.
    """
    counts = results_df["Sector"].value_counts()
    pct = (counts / counts.sum() * 100).round(1)
    return pd.DataFrame({"Sector": counts.index, "Count": counts.values, "% of watchlist": pct.values})



with tab_analysis:
    @st.fragment(run_every=live_interval)
    def render_live_ticker():
        if not ticker:
            st.caption("Enter a ticker above to see a live-refreshing quote.")
            return
        try:
            quote = get_live_quote(ticker)
            if quote["price"] is None:
                st.caption(f"No live quote available for '{ticker}' right now.")
                return
            delta_str = None
            if quote["change"] is not None and quote["change_pct"] is not None:
                delta_str = f"{quote['change']:+.2f} ({quote['change_pct']:+.2f}%)"
            st.metric(f"{ticker}", money(quote["price"], quote["currency"]), delta=delta_str)
            st.caption(
                f"Market state: {quote['market_state'] or 'unknown'} · "
                f"Last checked {datetime.now().strftime('%H:%M:%S')} · "
                f"Still Yahoo-delayed data — 'live' means auto-refreshing, not true real-time."
            )
        except Exception as e:
            if _is_rate_limit_error(e):
                st.caption("⏳ Rate-limited right now — will retry on the next refresh.")
            else:
                st.caption(f"Live quote unavailable ({e}).")

    render_live_ticker()


with tab_news:
    with st.expander("🗞️ Market news (general, not stock-specific)", expanded=False):
        with st.spinner("Loading market headlines..."):
            try:
                market_news = get_news_items("SPY", max_items=6)
            except Exception:
                market_news = []

        if not market_news:
            st.write("Couldn't load general market news right now.")
        else:
            for item in market_news:
                sentiment = tag_sentiment(item["title"] + " " + item.get("description", ""))
                badge = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}[sentiment]
                st.markdown(f"{badge} **[{item['title']}]({item['link']})**")
                if item["description"]:
                    st.caption(item["description"])


with tab_tradesetup:
    st.subheader("Trade goal planner")
    st.warning(
        "⚠️ You set a plain target gain and a max loss you're comfortable with, and "
        "this shows how often THIS STOCK'S OWN PAST actually moved that much within "
        "your chosen time window — the same real historical-percentile method as "
        "'Historical N-day outcomes' in the Analysis tab, not a fabricated confidence "
        "score, and not any named proprietary or university algorithm. Past frequency "
        "is not a guarantee of future frequency — a genuinely new event (earnings, "
        "news) can produce a move outside anything in the historical sample. This "
        "does not tell you whether to enter a trade at all."
    )

    ts_col1, ts_col2 = st.columns(2)
    with ts_col1:
        ts_market_choice = st.selectbox("Market", options=list(MARKET_SUFFIX_MAP.keys()), key="ts_market")
        ts_raw_ticker = st.text_input("Stock ticker or code", "AAPL", key="ts_ticker").upper().strip()
        _ts_suffix = MARKET_SUFFIX_MAP[ts_market_choice]
        ts_ticker = ts_raw_ticker + _ts_suffix if (_ts_suffix and "." not in ts_raw_ticker) else ts_raw_ticker
    with ts_col2:
        ts_direction = st.radio("Direction", options=["Long (expecting price to rise)", "Short (expecting price to fall)"], key="ts_direction")
        direction = "long" if ts_direction.startswith("Long") else "short"

    ts_col3, ts_col4, ts_col5 = st.columns(3)
    with ts_col3:
        target_gain_pct = st.slider(
            "Target gain (%)", 1, 50, 10, step=1,
            help="How much you want to make, as a plain percentage of today's price.",
        )
    with ts_col4:
        max_loss_pct = st.slider(
            "Max loss you're OK with (%)", 1, 30, 5, step=1,
            help="How much you're willing to lose before you'd walk away, as a plain percentage of today's price.",
        )
    with ts_col5:
        ts_horizon_days = st.slider(
            "Time window (trading days)", 5, 90, 20, step=5,
            help="How long you're giving the trade to work. Roughly 20 trading days ≈ 1 calendar month.",
        )

    if st.button("Check this plan"):
        try:
            with st.spinner(f"Loading {ts_ticker} data..."):
                ts_daily_df = load_daily_data(ts_ticker)

            plan = compute_goal_based_plan(ts_daily_df, target_gain_pct, max_loss_pct, ts_horizon_days, direction)

            if plan is None:
                st.session_state.pop("ts_last_plan", None)
                st.error("Not enough price history for this ticker yet to check this plan.")
            else:
                # Stashed in session_state (rather than only rendering right here)
                # so the results — and the "Log to journal" button below — survive
                # the rerun that clicking that button itself triggers. A button
                # nested inside `if st.button(...):` loses its click on that rerun,
                # since the outer button's True state doesn't persist across reruns.
                st.session_state["ts_last_plan"] = {
                    "plan": plan,
                    "ticker": ts_ticker,
                    "direction": direction,
                    "direction_label": ts_direction,
                    "currency": get_fundamentals(ts_ticker).get("currency", "USD"),
                    "target_gain_pct": target_gain_pct,
                    "max_loss_pct": max_loss_pct,
                    "horizon_days": ts_horizon_days,
                }
        except Exception as e:
            st.session_state.pop("ts_last_plan", None)
            if _is_rate_limit_error(e):
                st.error("⏳ Yahoo Finance is rate-limiting requests right now — try again in a minute.")
            else:
                st.error(f"Something went wrong: {e}")

    _saved_plan = st.session_state.get("ts_last_plan")
    if _saved_plan:
        plan = _saved_plan["plan"]
        p_ticker = _saved_plan["ticker"]
        p_direction = _saved_plan["direction"]
        p_currency = _saved_plan["currency"]
        p_target_pct = _saved_plan["target_gain_pct"]
        p_loss_pct = _saved_plan["max_loss_pct"]
        p_horizon = _saved_plan["horizon_days"]

        st.markdown(f"### {p_ticker} — {_saved_plan['direction_label']}")
        pcol1, pcol2, pcol3 = st.columns(3)
        pcol1.metric("Current price", money(plan["last_price"], p_currency))
        pcol2.metric("Your target", money(plan["target_price"], p_currency),
                     delta=f"{'+' if p_direction == 'long' else '-'}{p_target_pct}%", delta_color="off")
        pcol3.metric("Your max-loss level", money(plan["stop_price"], p_currency),
                     delta=f"{'-' if p_direction == 'long' else '+'}{p_loss_pct}%", delta_color="off")

        rcol1, rcol2 = st.columns(2)
        rcol1.metric(f"Hit target within {p_horizon} days (past year)", f"{plan['hit_target_pct']:.0f}% of the time")
        rcol2.metric(f"Hit max-loss within {p_horizon} days (past year)", f"{plan['hit_stop_pct']:.0f}% of the time")

        st.caption(
            f"Based on {plan['sample_size']} overlapping {p_horizon}-day windows from this stock's own "
            f"past ~year of daily prices. Each % is simply how often the price was at or beyond that level "
            f"exactly {p_horizon} trading days later — not 'at any point during' the window, and not "
            "a forecast. A small sample size makes these percentages noisier — treat a handful of windows "
            "as a rough signal, not a precise number."
        )
        st.info(
            "Remember: a low hit-rate on your target doesn't mean 'don't do it', and a high one doesn't mean "
            "'guaranteed' — it's just how often this specific move size has happened for this stock before, "
            "given no particular reason to expect it works the same way going forward."
        )

        st.divider()
        st.subheader("Position sizing (optional)")
        st.caption(
            "Standard retail risk-management math: given your account size and how much of it you're "
            "willing to risk on this ONE trade, this suggests a share count so a full stop-out costs "
            "roughly that much — not a recommendation on whether to take the trade, and not adjusted "
            "for any other positions you already hold."
        )
        poscol1, poscol2 = st.columns(2)
        with poscol1:
            account_size = st.number_input(
                "Account size", min_value=0.0, value=10000.0, step=500.0, key="ts_account_size",
            )
        with poscol2:
            risk_pct_of_account = st.slider(
                "% of account to risk on this trade", 0.5, 10.0, 1.0, step=0.5, key="ts_risk_pct",
                help="A commonly cited retail guideline is around 1-2% per trade, so a string of losses "
                     "doesn't wipe out the account — a guideline, not a rule; your own risk tolerance may differ.",
            )
        per_share_risk = abs(plan["last_price"] - plan["stop_price"])
        if account_size > 0 and per_share_risk > 0:
            dollars_at_risk = account_size * risk_pct_of_account / 100
            suggested_shares = int(dollars_at_risk / per_share_risk)
            poscol_a, poscol_b, poscol_c = st.columns(3)
            poscol_a.metric("$ at risk if stopped out", money(dollars_at_risk, p_currency))
            poscol_b.metric("Suggested share count", f"{suggested_shares:,}")
            poscol_c.metric("Position value at current price", money(suggested_shares * plan["last_price"], p_currency))
        else:
            st.caption("Enter an account size above to see a suggested position size.")

        if st.button("📓 Log this setup to Trade Journal"):
            st.session_state["journal_prefill"] = {
                "ticker": p_ticker,
                "direction": p_direction,
                "entry_price": round(plan["last_price"], 4),
                "stop_loss": round(plan["stop_price"], 4),
                "take_profit": round(plan["target_price"], 4),
            }
            st.success("Staged — open the 📓 Trade Journal tab to finish adding it (shares, date, notes).")


with tab_journal:
    st.subheader("Trade journal")
    st.caption(
        "A personal record of trades you've actually taken (or plan to) — for your "
        "own honest review, not a performance the app grades you on. Nobody's win "
        "rate is 100%; the point of a journal is seeing your real numbers clearly, "
        "including the losses, so you can learn from them."
    )

    journal_entries = load_journal()
    prefill = st.session_state.get("journal_prefill", {})

    with st.expander("Add a trade", expanded=bool(prefill)):
        with st.form("add_journal_entry"):
            jcol1, jcol2 = st.columns(2)
            with jcol1:
                j_ticker = st.text_input("Ticker", value=prefill.get("ticker", "")).upper().strip()
                j_direction = st.radio("Direction", options=["long", "short"],
                                       index=0 if prefill.get("direction", "long") == "long" else 1,
                                       horizontal=True)
                j_shares = st.number_input("Shares/units", min_value=0.0, step=1.0, value=0.0)
            with jcol2:
                j_entry_price = st.number_input("Entry price", min_value=0.0, format="%.4f",
                                                 value=float(prefill.get("entry_price", 0.0)))
                j_stop_loss = st.number_input("Stop loss", min_value=0.0, format="%.4f",
                                               value=float(prefill.get("stop_loss", 0.0)))
                j_take_profit = st.number_input("Take profit (optional)", min_value=0.0, format="%.4f",
                                                 value=float(prefill.get("take_profit", 0.0)))
            j_date_opened = st.date_input("Date opened", value=datetime.now().date())
            j_notes = st.text_area("Notes (optional)", placeholder="Why you took this trade, what you were watching for, etc.")
            add_submitted = st.form_submit_button("Add to journal")

        if add_submitted:
            if not j_ticker or j_entry_price <= 0 or j_stop_loss <= 0 or j_shares <= 0:
                st.error("Ticker, entry price, stop loss, and shares are all required.")
            else:
                journal_entries.append({
                    "id": f"{j_ticker}_{datetime.now().timestamp()}",
                    "ticker": j_ticker,
                    "direction": j_direction,
                    "shares": j_shares,
                    "entry_price": j_entry_price,
                    "stop_loss": j_stop_loss,
                    "take_profit": j_take_profit if j_take_profit > 0 else None,
                    "date_opened": str(j_date_opened),
                    "notes": j_notes,
                    "status": "open",
                    "exit_price": None,
                    "date_closed": None,
                })
                save_journal(journal_entries)
                st.session_state.pop("journal_prefill", None)
                st.success(f"Added {j_ticker} to your journal.")
                st.rerun()

    open_entries = [e for e in journal_entries if e["status"] == "open"]
    closed_entries = [e for e in journal_entries if e["status"] == "closed"]

    st.divider()
    st.subheader(f"Open positions ({len(open_entries)})")
    if not open_entries:
        st.caption("No open positions logged.")
    else:
        for entry in open_entries:
            with card():
                ecol1, ecol2, ecol3 = st.columns([2, 2, 1])
                ecol1.markdown(f"**{entry['ticker']}** · {entry['direction']} · {entry['shares']:g} shares")
                ecol2.caption(
                    f"Entry {entry['entry_price']:.4f} · Stop {entry['stop_loss']:.4f}"
                    + (f" · Target {entry['take_profit']:.4f}" if entry['take_profit'] else "")
                    + f" · Opened {entry['date_opened']}"
                )
                if entry["notes"]:
                    st.caption(f"📝 {entry['notes']}")

                with ecol3.popover("Close trade"):
                    exit_price = st.number_input("Exit price", min_value=0.0, format="%.4f", key=f"exit_{entry['id']}")
                    exit_date = st.date_input("Date closed", value=datetime.now().date(), key=f"exitdate_{entry['id']}")
                    if st.button("Confirm close", key=f"confirm_close_{entry['id']}"):
                        if exit_price <= 0:
                            st.error("Enter a valid exit price.")
                        else:
                            entry["status"] = "closed"
                            entry["exit_price"] = exit_price
                            entry["date_closed"] = str(exit_date)
                            save_journal(journal_entries)
                            st.rerun()

    st.divider()
    st.subheader(f"Closed trades ({len(closed_entries)})")
    if not closed_entries:
        st.caption("No closed trades yet.")
    else:
        closed_rows = []
        for entry in closed_entries:
            pnl = compute_journal_pnl(entry)
            closed_rows.append({
                "Ticker": entry["ticker"],
                "Direction": entry["direction"],
                "Entry": entry["entry_price"],
                "Exit": entry["exit_price"],
                "P/L $": round(pnl["pnl_dollars"], 2),
                "P/L %": round(pnl["pnl_pct"], 2) if pnl["pnl_pct"] is not None else None,
                "R-multiple": round(pnl["r_multiple"], 2) if pnl["r_multiple"] is not None else None,
                "Opened": entry["date_opened"],
                "Closed": entry["date_closed"],
            })
        closed_df = pd.DataFrame(closed_rows).sort_values("Closed", ascending=False).reset_index(drop=True)
        st.dataframe(closed_df, hide_index=True, use_container_width=True)

        st.divider()
        st.subheader("Your honest numbers")
        wins = [r for r in closed_rows if r["P/L $"] > 0]
        win_rate = len(wins) / len(closed_rows) * 100 if closed_rows else 0
        r_multiples = [r["R-multiple"] for r in closed_rows if r["R-multiple"] is not None]
        avg_r = np.mean(r_multiples) if r_multiples else None  # empty list -> None, not NaN (np.mean([]) warns and returns NaN)
        total_pnl = sum(r["P/L $"] for r in closed_rows)

        scol1, scol2, scol3 = st.columns(3)
        scol1.metric("Win rate", f"{win_rate:.0f}%", help="% of closed trades with positive P/L. Not predictive of your next trade.")
        scol2.metric("Average R-multiple", f"{avg_r:+.2f}" if avg_r is not None else "N/A",
                     help="Average outcome as a multiple of what you were risking per trade. Below the trades editor for exact math.")
        scol3.metric("Total P/L", money(total_pnl, "USD"))
        st.caption(
            "These are YOUR actual historical results, not a prediction of future performance — "
            "a small sample can look better or worse than your real long-run edge purely by chance. "
            "The point of tracking this honestly is noticing patterns (e.g. cutting winners short, "
            "letting losers run) rather than judging any single trade."
        )


with tab_digest:
    st.subheader("Daily digest")
    st.caption(
        "A one-screen 'what changed' summary across your watchlist, journal, and "
        "upcoming earnings — designed to be the one thing you check each morning "
        "instead of opening every tab separately."
    )
    st.info(
        "⚠️ Streamlit only runs when this page is open or you click something — it "
        "can't wake up on its own every morning and text you unattended. Clicking "
        "'Generate digest' below computes it fresh, and 'Send to Telegram' pushes "
        "that result to your phone. For a fully automatic 7am send with no app open, "
        "you'd need a separate always-on scheduled script calling the same underlying "
        "functions — a reasonable follow-up project, just not something a Streamlit "
        "app can do by itself."
    )

    if st.button("🌅 Generate digest", type="primary"):
        watchlist_tickers = [t.strip().upper() for t in load_saved_watchlist().split(",") if t.strip()]
        with st.spinner(f"Checking {len(watchlist_tickers)} tickers, your journal, and upcoming earnings..."):
            st.session_state["last_digest"] = generate_daily_digest(watchlist_tickers)

    digest = st.session_state.get("last_digest")
    if not digest:
        st.info("Select **Generate digest** to build today's summary.")
    else:
        st.caption(f"Generated {digest['generated_at']}")

        st.markdown("#### 🔄 Watchlist tilt changes")
        if digest["tilt_changes"]:
            st.dataframe(pd.DataFrame(digest["tilt_changes"]), hide_index=True, use_container_width=True)
        else:
            st.caption("No tilt changes since your last digest — either nothing's shifted, or this is your first digest.")
        with st.expander(f"Unchanged ({len(digest['tilt_unchanged'])})"):
            if digest["tilt_unchanged"]:
                st.dataframe(pd.DataFrame(digest["tilt_unchanged"]), hide_index=True, use_container_width=True)
            else:
                st.caption("Nothing here.")

        st.markdown("#### 📅 Earnings this week")
        if digest["upcoming_earnings"]:
            st.dataframe(pd.DataFrame(digest["upcoming_earnings"]), hide_index=True, use_container_width=True)
        else:
            st.caption("No watchlist earnings dates within the next 7 days.")

        st.markdown("#### 📓 Open journal positions")
        if digest["open_positions"]:
            st.dataframe(pd.DataFrame(digest["open_positions"]), hide_index=True, use_container_width=True)
            st.caption("'% cushion to stop' is how far current price sits from your stop loss — negative means it's already past it.")
        else:
            st.caption("No open positions logged in your Trade Journal.")

        st.divider()
        if st.button("Send this digest to Telegram"):
            tg = load_saved_telegram()
            if not tg.get("bot_token") or not tg.get("chat_id"):
                st.error("Add your Telegram bot token and chat ID in the ⚙️ Settings tab first.")
            else:
                message = format_digest_for_telegram(digest)
                sent, info = send_telegram_message(tg["bot_token"], tg["chat_id"], message)
                if sent:
                    st.success("Sent.")
                else:
                    st.error(f"Couldn't send: {info}")


with tab_multiasset:
    st.subheader("Multi-asset view")
    st.caption(
        "FX pairs, crypto, and commodities futures — same free Yahoo Finance data "
        "source as the stock tabs, so the same coverage/reliability caveats apply. "
        "Depth and history can be thinner for these than for large-cap stocks."
    )

    MULTI_ASSET_SYMBOLS = {
        "Forex": {
            "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
            "USD/SGD": "USDSGD=X", "USD/KRW": "USDKRW=X", "USD/HKD": "USDHKD=X",
            "AUD/USD": "AUDUSD=X",
        },
        "Crypto": {
            "Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Solana": "SOL-USD",
            "XRP": "XRP-USD", "Dogecoin": "DOGE-USD",
        },
        "Commodities": {
            "Gold": "GC=F", "Silver": "SI=F", "Crude Oil (WTI)": "CL=F",
            "Natural Gas": "NG=F", "Copper": "HG=F", "Corn": "ZC=F",
        },
    }

    ma_col1, ma_col2 = st.columns(2)
    with ma_col1:
        ma_asset_class = st.selectbox("Asset class", options=list(MULTI_ASSET_SYMBOLS.keys()))
    with ma_col2:
        ma_symbol_choice = st.selectbox("Instrument", options=list(MULTI_ASSET_SYMBOLS[ma_asset_class].keys()) + ["Custom symbol..."])

    if ma_symbol_choice == "Custom symbol...":
        ma_ticker = st.text_input(
            "Enter a Yahoo Finance symbol",
            help="Forex: PAIR=X (e.g. NZDUSD=X). Crypto: COIN-USD (e.g. ADA-USD). Futures: CODE=F (e.g. SI=F for silver).",
        ).strip().upper()
    else:
        ma_ticker = MULTI_ASSET_SYMBOLS[ma_asset_class][ma_symbol_choice]

    ma_run = st.button("Load", key="multiasset_run")

    if ma_run and ma_ticker:
        try:
            with st.spinner(f"Loading {ma_ticker}..."):
                ma_daily_df = load_daily_data(ma_ticker)
                ma_daily_df = add_indicators(ma_daily_df)

            ma_latest = ma_daily_df.iloc[-1]

            # FX rates aren't really "money in a currency" the way a stock
            # price is, so they're shown as plain numbers; crypto and
            # commodities futures are conventionally USD-quoted.
            is_fx = ma_asset_class == "Forex"
            price_display = f"{ma_latest['Close']:.4f}" if is_fx else money(ma_latest["Close"], "USD")

            mcol1, mcol2, mcol3 = st.columns(3)
            mcol1.metric(ma_symbol_choice if ma_symbol_choice != "Custom symbol..." else ma_ticker, price_display)
            mcol2.metric("20-day avg", f"{ma_latest['SMA20']:.4f}" if is_fx else money(ma_latest['SMA20'], "USD"))
            mcol3.metric("RSI (14)", f"{ma_latest['RSI14']:.1f}" if pd.notna(ma_latest["RSI14"]) else "N/A")

            fig = go.Figure()
            add_price_glow(fig, ma_daily_df, extra_cols=("SMA20", "SMA50"))
            fig.add_trace(go.Scatter(
                x=ma_daily_df.index, y=ma_daily_df["SMA20"], name="20-day avg",
                line=dict(width=1.6, color=CHART_GOLD, shape="spline", smoothing=0.4),
            ))
            fig.add_trace(go.Scatter(
                x=ma_daily_df.index, y=ma_daily_df["SMA50"], name="50-day avg",
                line=dict(width=1.3, color=CHART_GOLD_SOFT, shape="spline", smoothing=0.4),
            ))
            fig.add_trace(go.Candlestick(
                x=ma_daily_df.index,
                open=ma_daily_df["Open"], high=ma_daily_df["High"],
                low=ma_daily_df["Low"], close=ma_daily_df["Close"],
                name=ma_ticker,
                increasing=dict(line=dict(color=CHART_JADE, width=1), fillcolor=CHART_JADE),
                decreasing=dict(line=dict(color=CHART_ROSE, width=1), fillcolor=CHART_ROSE),
            ))
            st.plotly_chart(style_chart(fig, height=460), use_container_width=True)

            ma_lean, ma_reasons, _ma_score = compute_indicator_lean(ma_latest)
            _ma_head, _, _ma_detail = ma_lean.partition(" (")
            render_verdict(
                "Indicator lean",
                _ma_head,
                tone=tone_for_lean(ma_lean),
                note="Same transparent indicator count used for stocks — not a validated "
                     "strategy, and less battle-tested for non-equity assets like these.",
                right=f"{_ma_score:+d}",
                right_sub="net signals",
            )
            for r in ma_reasons:
                st.write(f"- {r}")

            st.caption(
                "Note: FX, crypto, and commodities behave differently from stocks in "
                "important ways (crypto trades 24/7 with no 'closing price', commodities "
                "futures roll between contract months, FX has no earnings/dividends) — "
                "the same technical indicators are shown here for consistency, but they "
                "were originally designed around equity price behavior."
            )
        except Exception as e:
            if _is_rate_limit_error(e):
                st.error("⏳ Yahoo Finance is rate-limiting requests right now — try again in a minute.")
            else:
                st.error(f"Couldn't load '{ma_ticker}' — check the symbol is a valid Yahoo Finance ticker. ({e})")


with tab_calendar:
    st.subheader("Macro calendar (reference)")
    st.caption(
        "This is a maintained reference table, not a live feed — scraping economic-"
        "calendar sites is fragile and breaks often (and against most sites' terms), "
        "so instead of faking a 'live calendar' that quietly goes stale, this lists "
        "known recurring US release patterns plus the confirmed 2026 FOMC dates. "
        "Always verify the exact date against the official source before relying on it — "
        "release schedules can shift, and this table needs a manual update once a year."
    )

    MACRO_EVENTS = pd.DataFrame([
        {
            "Event": "FOMC meeting (Fed interest rate decision)",
            "Frequency": "8×/year",
            "Remaining 2026 dates": "Sep 15–16, Oct 27–28, Dec 8–9",
            "Official source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        },
        {
            "Event": "US Non-Farm Payrolls (jobs report)",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Typically 1st Friday of the month (occasionally 2nd)",
            "Official source": "https://www.bls.gov/schedule/news_release/empsit.htm",
        },
        {
            "Event": "US CPI (inflation)",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Typically 2nd week of the month — exact day varies",
            "Official source": "https://www.bls.gov/schedule/news_release/cpi.htm",
        },
        {
            "Event": "US GDP (advance estimate)",
            "Frequency": "Quarterly",
            "Remaining 2026 dates": "Late Oct 2026 (Q3), late Jan 2027 (Q4)",
            "Official source": "https://www.bea.gov/news/schedule",
        },
        {
            "Event": "US Retail Sales",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Mid-month — exact day varies",
            "Official source": "https://www.census.gov/retail/marts/www/marts_current.pdf",
        },
        {
            "Event": "FOMC meeting minutes",
            "Frequency": "8×/year, ~3 weeks after each meeting",
            "Remaining 2026 dates": "~3 weeks after each FOMC date above",
            "Official source": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        },
    ])
    st.dataframe(
        MACRO_EVENTS, hide_index=True, use_container_width=True,
        column_config={"Official source": st.column_config.LinkColumn(display_text="Open source ↗")},
    )
    st.caption("Reference compiled August 2026. FOMC dates confirmed against the Federal Reserve's official calendar; other rows are typical patterns, not confirmed exact dates.")

    st.divider()
    st.subheader("Earnings calendar (your watchlist)")
    st.caption("Live — pulled from Yahoo Finance for the tickers in your Watchlist tab.")

    if st.button("Load earnings dates for my watchlist"):
        watchlist_tickers = [t.strip().upper() for t in load_saved_watchlist().split(",") if t.strip()]
        earnings_rows = []
        with st.spinner(f"Checking earnings dates for {len(watchlist_tickers)} tickers..."):
            for t in watchlist_tickers:
                try:
                    ed = get_earnings_and_dividends(t)
                    if ed["next_earnings"] is not None:
                        earnings_rows.append({
                            "Ticker": t,
                            "Next/most recent earnings date": ed["next_earnings"].strftime("%Y-%m-%d"),
                        })
                except Exception:
                    continue

        if not earnings_rows:
            st.write("No earnings date data available for these tickers right now.")
        else:
            earnings_df = pd.DataFrame(earnings_rows).sort_values("Next/most recent earnings date").reset_index(drop=True)
            st.dataframe(earnings_df, hide_index=True, use_container_width=True)
            st.caption(
                "Some dates may be the most recent PAST earnings if Yahoo doesn't yet have the "
                "next confirmed date — check the actual date before relying on it."
            )


# ----------------------------------------------------------------------
# 12. MAIN LOGIC
# ----------------------------------------------------------------------
_analysis_run = st.session_state.get("analysis_run")
if _analysis_run:
    # Use the inputs captured at the moment "Run analysis" was clicked, not
    # whatever the widgets currently show — otherwise an unrelated rerun could
    # re-render using a half-typed ticker the user hasn't confirmed yet.
    ticker = _analysis_run["ticker"]
    period_choice = _analysis_run["period_choice"]
    horizon_days = _analysis_run["horizon_days"]
    try:
        yf_period, yf_interval = CHART_PERIOD_MAP[period_choice]
        unit_label = "day" if yf_interval == "1d" else "bar"

        with st.spinner(f"Loading {ticker} data..."):
            chart_df = load_chart_data(ticker, yf_period, yf_interval)
            chart_df = add_indicators(chart_df)

            daily_df = load_daily_data(ticker)
            daily_df = add_indicators(daily_df)

            rt = get_realtime_price_info(ticker)
            fundamentals = get_fundamentals(ticker)  # reused below in Fundamentals tab too — cached, so no extra request there

        latest_chart = chart_df.iloc[-1]
        latest_daily = daily_df.iloc[-1]
        currency = rt["currency"]

        # Prefer the real-time regular price if Yahoo has it; otherwise
        # fall back to the last bar we already downloaded.
        display_price = rt["regular_price"] if rt["regular_price"] else latest_chart["Close"]

        with tab_analysis:
            render_quote_strip(ticker, rt, fundamentals, currency, display_price)
            st.caption(f"Source: Yahoo Finance (free, delayed data) · Refreshed {datetime.now().strftime('%H:%M:%S')}")

            col2, col3, col4 = st.columns(3)
            col2.metric(f"20-{unit_label} avg", money(latest_chart['SMA20'], currency) if pd.notna(latest_chart['SMA20']) else "N/A",
                        help="Average closing price over the last 20 periods. Smooths out day-to-day noise to show the short-term trend.")
            col3.metric(f"50-{unit_label} avg", money(latest_chart['SMA50'], currency) if pd.notna(latest_chart['SMA50']) else "N/A",
                        help="Average closing price over the last 50 periods — a slower-moving, longer-term trend line.")
            col4.metric("RSI (14)", f"{latest_chart['RSI14']:.1f}" if pd.notna(latest_chart['RSI14']) else "N/A",
                        help="Relative Strength Index: a 0-100 momentum score. Traditionally, >70 is 'overbought', <30 is 'oversold' — rules of thumb, not guarantees.")

            # --- Extended-hours indicator (pre-market / after-hours) ---
            # Mainly available for US tickers — Yahoo doesn't report this for
            # every exchange, so this quietly does nothing if data's missing.
            if rt["market_state"] in ("POST", "PREPRE", "POSTPOST") and rt["post_price"]:
                st.caption(
                    f"After-hours: {money(rt['post_price'], currency)} "
                    f"({'+' if (rt['post_change'] or 0) >= 0 else ''}{rt['post_change']:.2f})"
                )
            elif rt["market_state"] == "PRE" and rt["pre_price"]:
                st.caption(
                    f"Pre-market: {money(rt['pre_price'], currency)} "
                    f"({'+' if (rt['pre_change'] or 0) >= 0 else ''}{rt['pre_change']:.2f})"
                )

            # --- Macro backdrop (VIX) — purely descriptive, not a per-stock signal ---
            macro = get_macro_backdrop()
            if macro:
                level_desc = {
                    "elevated": "above its own 6-month average — markets are pricing in more uncertainty than usual",
                    "low": "below its own 6-month average — markets are pricing in less uncertainty than usual",
                    "typical": "close to its own 6-month average — nothing unusual market-wide right now",
                }[macro["level"]]
                st.caption(f"Market backdrop: VIX at {macro['current']:.1f}, {level_desc}. (Market-wide context, not specific to {ticker}.)")

            with st.expander("Add geopolitical risk context (optional)"):
                st.caption(
                    "There's no free live API for geopolitical risk data, so this isn't automatic. "
                    "The Caldara & Iacoviello Geopolitical Risk (GPR) Index is a free, well-known "
                    "academic dataset you can download yourself from "
                    "[matteoiacoviello.com/gpr.htm](https://www.matteoiacoviello.com/gpr.htm) "
                    "(needs 'date' and 'gpr' columns) — upload it here if you want that context "
                    "alongside VIX. Same rule as everywhere else: descriptive only, never folded "
                    "into any per-stock score."
                )
                gpr_file = st.file_uploader("GPR index CSV", type="csv", key=f"gpr_upload_{ticker}")
                if gpr_file:
                    gpr_result = parse_gpr_upload(gpr_file)
                    if gpr_result:
                        gpr_level_desc = {
                            "elevated": "above its own 6-month average in your file — elevated geopolitical risk per this index",
                            "low": "below its own 6-month average in your file — lower geopolitical risk per this index",
                            "typical": "close to its own 6-month average in your file — nothing unusual per this index",
                        }[gpr_result["level"]]
                        st.caption(f"GPR index (as of {gpr_result['as_of']}): {gpr_result['current']:.1f}, {gpr_level_desc}.")
                    else:
                        st.error("Couldn't parse that file — check it has 'date' and 'gpr' columns.")

            # --- Price chart (candlestick, matching real trading-platform style) ---
            # Draw order matters: the ambient wash goes down first, then the
            # Bollinger envelope as a shaded band (fill='tonexty' between the
            # two dotted lines), then the moving averages, then candles last
            # so price always sits on top of its own context.
            st.subheader(f"{ticker} price chart ({period_choice})")
            fig = go.Figure()
            add_price_glow(fig, chart_df, extra_cols=("BB_upper", "BB_lower"))
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["BB_upper"], name="Upper band",
                line=dict(dash="dot", width=1, color="rgba(212,176,120,0.35)"),
                hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["BB_lower"], name="Lower band",
                line=dict(dash="dot", width=1, color="rgba(212,176,120,0.35)"),
                fill="tonexty", fillcolor="rgba(212,176,120,0.045)", hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["SMA20"], name=f"20-{unit_label} avg",
                line=dict(width=1.6, color=CHART_GOLD, shape="spline", smoothing=0.4),
            ))
            fig.add_trace(go.Scatter(
                x=chart_df.index, y=chart_df["SMA50"], name=f"50-{unit_label} avg",
                line=dict(width=1.3, color=CHART_GOLD_SOFT, shape="spline", smoothing=0.4),
            ))
            fig.add_trace(go.Candlestick(
                x=chart_df.index,
                open=chart_df["Open"], high=chart_df["High"],
                low=chart_df["Low"], close=chart_df["Close"],
                name="Price",
                increasing=dict(line=dict(color=CHART_JADE, width=1), fillcolor=CHART_JADE),
                decreasing=dict(line=dict(color=CHART_ROSE, width=1), fillcolor=CHART_ROSE),
            ))
            st.plotly_chart(style_chart(fig, height=520), use_container_width=True)

            if yf_interval != "1d":
                st.caption(
                    f"Showing intraday {yf_interval} bars. The Indicator Lean and price range "
                    "below always use standard daily data, so they stay stable regardless of "
                    "this chart's zoom level."
                )

            # --- Indicator Lean (always from daily data) ---
            st.subheader("Indicator lean")
            lean, reasons, _score = compute_indicator_lean(latest_daily)
            st.session_state["last_lean"] = lean
            st.session_state["last_ticker"] = ticker
            # `lean` reads e.g. "Bullish tilt (3 of 4 indicators positive)". The
            # headline takes the phrase, the count moves into the supporting
            # line — a two-line verdict at display size is no longer a verdict.
            _lean_head, _, _lean_detail = lean.partition(" (")
            _lean_detail = _lean_detail.rstrip(")")
            render_verdict(
                "Current tilt · daily data",
                _lean_head,
                tone=tone_for_lean(lean),
                note=(f"{_lean_detail[0].upper() + _lean_detail[1:]}. " if _lean_detail else "")
                     + "A count of how many well-known indicators (moving averages, MACD, RSI) "
                       "point bullish vs. bearish right now — a transparency tool, not a "
                       "validated strategy, and never a recommendation.",
                right=f"{_score:+d}",
                right_sub="net signals",
            )
            for r in reasons:
                st.write(f"- {r}")

            # --- What changed today (factual, computed — not a narrative) ---
            changes_today = whats_changed_today(daily_df)
            if changes_today:
                st.markdown("**What changed today:**")
                for c in changes_today:
                    st.write(f"- {c}")
            else:
                st.caption("No notable threshold crossings or volume spikes today.")

            # --- Statistical price range (always from daily data) ---
            st.subheader(f"Statistical price range — next {horizon_days} trading day(s)")
            lower68, upper68 = compute_price_range(daily_df, horizon_days, confidence=0.68)
            lower95, upper95 = compute_price_range(daily_df, horizon_days, confidence=0.95)
            rcol1, rcol2 = st.columns(2)
            rcol1.metric("~68% range (1 std dev)", f"{money(lower68, currency)} – {money(upper68, currency)}",
                         help="1 standard deviation. Based on this stock's own past daily price swings, if those swings behaved like a bell curve, about 68% of past outcomes fell within 1 standard deviation of the average move. Describes typical past volatility, not a forecast.")
            rcol2.metric("~95% range (2 std dev)", f"{money(lower95, currency)} – {money(upper95, currency)}",
                         help="2 standard deviations — a wider band. About 95% of past outcomes fell within this range if moves behaved like a bell curve. Still based on the past, not a prediction.")
            st.warning(
                "Based purely on this stock's own historical volatility, assuming no "
                "directional drift. Not a prediction — real prices can move outside "
                "this range, especially around news events."
            )

            # --- Historical scenario ranges — REAL percentiles from this
            #     stock's own past N-day returns, not invented bull/base/bear
            #     numbers. Answers "what actually happened over similar windows
            #     historically" rather than forecasting what will happen now. ---
            st.subheader(f"Historical {horizon_days}-day outcomes (this stock's own past year)")
            scenario = historical_scenario_ranges(daily_df, horizon_days)
            if scenario:
                scol1, scol2, scol3 = st.columns(3)
                scol1.metric("Worst 10% of past outcomes", f"{scenario['worst_10pct']:+.1f}%")
                scol2.metric("Median past outcome", f"{scenario['median']:+.1f}%")
                scol3.metric("Best 10% of past outcomes", f"{scenario['best_10pct']:+.1f}%")
                st.caption(
                    f"Based on {scenario['sample_size']} overlapping {horizon_days}-day windows from this "
                    "stock's own past year. This is what actually happened historically over "
                    "similar-length periods — not a forecast, and past windows overlap so they "
                    "aren't fully independent samples. A genuinely new event (earnings, news) can "
                    "produce an outcome outside this entire historical range."
                )
            else:
                st.caption("Not enough price history to compute this yet.")

            # --- Signal agreement — honest alternative to a fabricated confidence score ---
            st.subheader("Signal agreement")
            st.write(compute_signal_agreement(_score, get_news_items(ticker)))
            st.caption(
                "This just states whether the technical read and headline tone happen to "
                "point the same way right now — it is NOT a probability or confidence score. "
                "Agreement between two weak signals doesn't make either one strong."
            )

            # --- News ---
            st.subheader("Recent news")
            with st.spinner("Fetching headlines..."):
                news_items = get_news_items(ticker)

            if not news_items:
                st.write("No headlines found for this ticker right now.")
            else:
                # Headline cards: the tone pill sits above the headline as a
                # small caps chip rather than as a coloured emoji trailing the
                # text. Same information, but it scans as a category label and
                # keeps the headline itself the largest thing in the card.
                for item in news_items:
                    with card():
                        sentiment = tag_sentiment(item["title"] + " " + item.get("description", ""))
                        pill_style = {
                            "positive": "color:#5FCF9B;background:rgba(95,207,155,0.12);border-color:rgba(95,207,155,0.28)",
                            "negative": "color:#F0616F;background:rgba(240,97,111,0.12);border-color:rgba(240,97,111,0.28)",
                            "neutral": "color:#ABA598;background:rgba(255,255,255,0.05);border-color:rgba(255,255,255,0.10)",
                        }[sentiment]
                        st.markdown(
                            f'<span style="display:inline-block;padding:0.2rem 0.6rem;border-radius:99px;'
                            f'border:1px solid;font-family:Inter,sans-serif;font-size:0.58rem;font-weight:600;'
                            f'letter-spacing:0.19em;text-transform:uppercase;{pill_style}">{sentiment} tone</span>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(f"**[{item['title']}]({item['link']})**")
                        if item["published"]:
                            st.caption(item["published"])
                        if item["description"]:
                            st.caption(item["description"])

                decayed = compute_decayed_sentiment(news_items)
                if decayed:
                    st.caption(
                        f"{decayed['label'].capitalize()} across these {decayed['n_items']} headlines "
                        f"(recency-weighted score: {decayed['score']:+.2f}, range -1 to +1). "
                        "Newer headlines count more than older ones — still just headline tone, "
                        "not a signal to act on; see the individual headlines above for substance."
                    )

            # --- Narrative connecting technicals + news (free, rule-based) ---
            st.subheader("What's driving the current picture")
            st.write(generate_narrative(ticker, lean, reasons, news_items))
            if st.toggle("Explain this like I'm five"):
                st.info(explain_like_im_5(ticker, lean, news_items))


        with tab_fundamentals:
            st.caption("Fundamentals, analyst estimates, earnings, and risk metrics for the ticker selected in the Analysis tab.")

            # --- Fundamentals + Analyst view + Earnings/Dividends + Risk profile ---
            # fundamentals already fetched above for the quote strip (cached — no extra request here)
            with st.spinner("Loading fundamentals..."):
                ed_data = get_earnings_and_dividends(ticker)
                risk = compute_risk_metrics(daily_df)

            st.subheader("Fundamentals")
            fcol1, fcol2, fcol3, fcol4 = st.columns(4)
            fcol1.metric("Sector", fundamentals["sector"] or "N/A")
            fcol2.metric("Market cap", format_market_cap(fundamentals["market_cap"], currency))
            fcol3.metric("P/E (trailing)", f"{fundamentals['trailing_pe']:.1f}" if fundamentals["trailing_pe"] else "N/A",
                         help="Price divided by trailing 12-month earnings per share. Higher generally means the market is pricing in more future growth (or the stock is more expensive relative to current profit) — context-dependent, not good/bad on its own.")
            # "dividend_yield_pct" is computed once in get_fundamentals (preferring
            # the unambiguous trailingAnnualDividendYield field — see the comment
            # there — since yfinance's plain "dividendYield" has changed units
            # across versions with no reliable way to tell which from the number
            # alone, and silently produced 100x-off yields when guessed wrong).
            div_yield_pct = fundamentals["dividend_yield_pct"]
            fcol4.metric("Dividend yield", f"{div_yield_pct:.2f}%" if div_yield_pct else "None",
                         help="Annual dividend payments as a percentage of the current share price.")

            st.subheader("Analyst view")
            st.caption(
                "These are published estimates from Wall Street analysts covering this "
                "stock — not this app's own calculation, and not a guarantee of future price. "
                "Coverage is often thinner or unavailable for non-US tickers."
            )
            if fundamentals["target_mean_price"]:
                acol1, acol2 = st.columns(2)
                with acol1:
                    st.metric("Analyst avg. target", money(fundamentals['target_mean_price'], currency))
                    st.metric("# of analysts", fundamentals["num_analyst_opinions"] or "N/A")
                with acol2:
                    target_range_text = (
                        f"{money(fundamentals['target_low_price'], currency)} – {money(fundamentals['target_high_price'], currency)}"
                        if fundamentals["target_low_price"] and fundamentals["target_high_price"] else "N/A"
                    )
                    # Two "$"-prefixed amounts on one line reads as a pair of LaTeX math
                    # delimiters to st.markdown's KaTeX rendering, which silently swallows
                    # both dollar signs — escape them so they render as literal text.
                    st.markdown(f"**Target range**  \n{target_range_text}".replace("$", "\\$"))
                    st.markdown(f"**Consensus**  \n{(fundamentals['recommendation_key'] or 'N/A').replace('_', ' ').title()}")
            else:
                st.write("No analyst target data available for this ticker.")

            st.subheader("Earnings & dividends")
            ecol1, ecol2 = st.columns(2)
            with ecol1:
                if ed_data["next_earnings"] is not None:
                    st.metric("Next/most recent earnings date", ed_data["next_earnings"].strftime("%Y-%m-%d"))
                else:
                    st.write("No earnings date data available.")
            with ecol2:
                if not ed_data["dividends"].empty:
                    st.caption("Last 8 dividend payments")
                    st.bar_chart(ed_data["dividends"])
                else:
                    st.write("No dividend history — this stock may not pay dividends.")

            st.subheader("Risk profile")
            rcol_a, rcol_b, rcol_c = st.columns(3)
            rcol_a.metric("Beta", f"{fundamentals['beta']:.2f}" if fundamentals["beta"] else "N/A",
                          help="How much this stock has historically moved relative to the overall market. 1.0 = moves roughly with the market; above 1 = historically more volatile than the market; below 1 = historically less volatile.")
            rcol_b.metric("Max drawdown (1y)", f"{risk['max_drawdown']*100:.1f}%",
                          help="The largest peak-to-trough decline over the past year. Describes the worst historical stretch in this window — not a cap on future losses.")
            rcol_c.metric("Annualized volatility", f"{risk['annualized_vol']*100:.1f}%",
                          help="How much this stock's price has typically swung over a year, scaled up from its daily moves. Higher = historically choppier.")

            sector = fundamentals["sector"]
            benchmark_ticker = SECTOR_BENCHMARK_MAP.get(sector, "SPY")
            benchmark_label = f"{sector} sector (via {benchmark_ticker})" if sector in SECTOR_BENCHMARK_MAP else f"the overall market (via {benchmark_ticker})"
            try:
                benchmark_df = add_indicators(load_daily_data(benchmark_ticker))
                benchmark_vol = benchmark_df["LogReturn"].std() * np.sqrt(252)
                st.caption(
                    f"For comparison, {benchmark_label} has had roughly {benchmark_vol*100:.1f}% "
                    f"annualized volatility over the same period — "
                    + ("higher" if risk["annualized_vol"] > benchmark_vol else "lower")
                    + f" than {ticker}'s {risk['annualized_vol']*100:.1f}%."
                )
            except Exception:
                pass  # benchmark comparison is a nice-to-have, not essential

            # --- Historical signal check (full regression stats, honestly caveated) ---
            st.subheader("Historical signal check")
            bt = historical_signal_check(daily_df)
            if bt is None:
                st.write("Not enough history to compute this check.")
            else:
                hcol1, hcol2, hcol3 = st.columns(3)
                hcol1.metric("R (correlation)", f"{bt['r_value']:+.2f}",
                             help="How closely the indicator score has tracked 5-day forward returns. Near 0 = no relationship; the sign matters more than the exact size at this sample.")
                hcol2.metric("R² (variance explained)", f"{bt['r_squared']:.3f}",
                             help="What fraction of forward-return variation the score explains. Values well under 0.1 are typical and expected for a simple technical score — this is not a flaw, it's what an honest number looks like.")
                hcol3.metric("p-value", f"{bt['p_value']:.3f}",
                             help="Above ~0.05 means the relationship isn't statistically distinguishable from noise at this sample size — common here, and worth taking seriously rather than skipping past.")
                st.caption(f"Directional hit rate: {bt['hit_rate_pct']:.1f}% (score and forward return had the same sign) · n = {bt['n_obs']} overlapping windows")

                if bt["quartile"]:
                    q = bt["quartile"]
                    st.caption(
                        f"Top-quartile-score periods averaged {q['top_quartile_mean_pct']:+.2f}% over the next "
                        f"{5} days; bottom-quartile-score periods averaged {q['bottom_quartile_mean_pct']:+.2f}% "
                        f"(t-test p-value: {q['t_pvalue']:.3f})."
                    )
            st.warning(
                "This checks whether the Indicator Lean's score has lined up with "
                "*this one stock's own* subsequent price moves, over its own past "
                "year only. A clean-looking number here is easy to get from noise "
                "alone with a single asset and window — it is not validation of a "
                "trading strategy, and does not mean the same pattern continues. "
                "A high p-value here isn't a bug to fix — it's the honest, and "
                "typical, result for a simple technical score."
            )
            if telegram_enabled:
                st.caption("Use 'Send last tilt to Telegram' in the Settings tab to send this result.")

            st.caption(f"Data as of {datetime.now().strftime('%Y-%m-%d %H:%M')}. Not financial advice.")

        with tab_factors:
            st.subheader(f"Factor Score — {ticker}")
            st.caption(
                "A single, transparent 0-100 score built from four independently "
                "well-documented factor styles: Value, Quality, Momentum, and News "
                "Sentiment. This is a systematic SCREENING score, not personalized "
                "investment advice — expand the methodology note and each factor "
                "breakdown below to see exactly how it's computed, and its caveats."
            )

            with st.expander("How this score works — read this first"):
                st.markdown("""
**The big idea.** Factor investing is the well-documented finding that certain
measurable stock characteristics — being statistically *cheap* (Value), being
*financially healthy* (Quality), and having *recent positive price trend*
(Momentum) — have, **on average, over long periods, across large numbers of
stocks**, been associated with better subsequent returns than their
opposites. This is decades of published academic research (Fama & French on
value, Jegadeesh & Titman on momentum, Piotroski on quality) — not a
proprietary or made-up model. "On average, over many stocks, over long
periods" is doing a lot of work in that sentence: it does **not** mean any
single stock, right now, is guaranteed to behave a certain way. A fourth
factor, News Sentiment, is layered on top since it's specifically what you
asked for — it is the least academically validated of the four (closer to
short-term noise than a proven factor), which is why it gets the smallest
default weight.

- **Value** — z-scores this stock's P/E, forward P/E, P/B, PEG, EV/EBITDA,
  P/S, and dividend yield against your Watchlist (used as a peer/comparison
  group) or a broad-market fallback if the watchlist has too few tickers. A
  z-score answers "how many standard deviations cheaper or more expensive is
  this stock than its comparison group?" — averaging several ratios instead
  of trusting one avoids a single misleading number driving the whole score.
- **Quality** — counts how many financial-health checks (positive ROE,
  positive margins, conservative debt, positive free cash flow, positive
  growth) this stock passes, inspired by a simplified version of Piotroski's
  F-Score. More checks passed = healthier balance sheet and income statement.
- **Momentum** — blends the stock's own 12-month return (excluding the most
  recent month — the standard academic "12-1 momentum" definition) measured
  relative to its sector benchmark, with this app's existing short-term
  Indicator Lean (moving averages, MACD, RSI).
- **News Sentiment** — recency-weighted tone of recent headlines, using the
  same finance-aware sentiment tagging used elsewhere in this app. A
  headline from an hour ago counts far more than one from a week ago.

**Combining them.** Each factor produces its own 0-100 score, then they're
combined as a weighted average (weights adjustable below — they auto-rescale
to add up to 100%). A factor with no available data for this ticker is left
out entirely and the remaining weights are rescaled — it is never silently
treated as a 0.
                """)

            st.markdown("#### Factor weights")
            wcol1, wcol2, wcol3, wcol4 = st.columns(4)
            w_value = wcol1.slider("Value", 0, 100, int(DEFAULT_FACTOR_WEIGHTS["value"] * 100), key="w_value")
            w_quality = wcol2.slider("Quality", 0, 100, int(DEFAULT_FACTOR_WEIGHTS["quality"] * 100), key="w_quality")
            w_momentum = wcol3.slider("Momentum", 0, 100, int(DEFAULT_FACTOR_WEIGHTS["momentum"] * 100), key="w_momentum")
            w_sentiment = wcol4.slider("Sentiment", 0, 100, int(DEFAULT_FACTOR_WEIGHTS["sentiment"] * 100), key="w_sentiment")
            _raw_weights = {"value": w_value, "quality": w_quality, "momentum": w_momentum, "sentiment": w_sentiment}
            _weight_total = sum(_raw_weights.values())
            factor_weights = ({k: v / _weight_total for k, v in _raw_weights.items()} if _weight_total > 0
                               else {"value": 0.25, "quality": 0.25, "momentum": 0.25, "sentiment": 0.25})
            st.caption("Weights auto-rescale to add up to 100%, whatever you set them to individually.")

            with st.spinner("Computing factor score..."):
                _peer_tickers = [t.strip().upper() for t in st.session_state.get("watchlist_text", "").split(",") if t.strip()]
                peer_df = get_peer_fundamentals_df(_peer_tickers) if _peer_tickers else pd.DataFrame()

                factor_benchmark_ticker = SECTOR_BENCHMARK_MAP.get(fundamentals.get("sector"), "SPY")
                try:
                    factor_benchmark_df = load_daily_data(factor_benchmark_ticker)
                except Exception:
                    factor_benchmark_df = None

                value_result = compute_value_score(fundamentals, peer_df)
                quality_result = compute_quality_score(fundamentals)
                momentum_result = compute_momentum_score(daily_df, factor_benchmark_df, _score)
                sentiment_result = compute_news_sentiment_score(news_items)
                composite = compute_factor_composite(value_result, quality_result, momentum_result, sentiment_result, factor_weights)

            if composite["score"] is None:
                st.error("Not enough data available to compute a factor score for this ticker.")
            else:
                verdict_label, verdict_emoji = factor_score_verdict(composite["score"])

                # The composite gets the verdict panel; the four sub-scores get
                # bars beside the dial. A dial alone tells you where you landed
                # but not what put you there — the bars answer that in the same
                # glance, which is the whole point of showing them together.
                _factor_tone = "bull" if composite["score"] >= 60 else ("bear" if composite["score"] < 40 else "neutral")
                render_verdict(
                    "Composite factor score",
                    verdict_label,
                    tone=_factor_tone,
                    note="Value, Quality, Momentum and News Sentiment, weighted as set above. "
                         "A snapshot of measurable factors — not a forecast.",
                    right=f"{composite['score']:.0f}",
                    right_sub="out of 100",
                )

                gcol, bcol = st.columns([1, 1])
                with gcol:
                    # Dark dial: the coloured bands read as a faint temperature
                    # scale behind the needle rather than competing with it, and
                    # the value is set in the same tabular mono as every other
                    # number in the product.
                    gauge_fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=composite["score"],
                        number={
                            "suffix": "<span style='font-size:0.5em;color:#7E786C'> / 100</span>",
                            "font": {"family": "JetBrains Mono, monospace", "size": 46, "color": "#F4F1EA"},
                        },
                        gauge={
                            "axis": {
                                "range": [0, 100],
                                "tickcolor": "rgba(255,255,255,0.16)",
                                "tickfont": {"family": "JetBrains Mono, monospace", "size": 10, "color": "#7E786C"},
                            },
                            "bar": {"color": CHART_GOLD, "thickness": 0.24},
                            "bgcolor": "rgba(0,0,0,0)",
                            "borderwidth": 0,
                            "steps": [
                                {"range": [0, 30], "color": "rgba(240,97,111,0.20)"},
                                {"range": [30, 45], "color": "rgba(240,97,111,0.10)"},
                                {"range": [45, 55], "color": "rgba(255,255,255,0.05)"},
                                {"range": [55, 70], "color": "rgba(95,207,155,0.10)"},
                                {"range": [70, 100], "color": "rgba(95,207,155,0.20)"},
                            ],
                            "threshold": {
                                "line": {"color": "#F2E2C1", "width": 2},
                                "thickness": 0.82,
                                "value": composite["score"],
                            },
                        },
                    ))
                    gauge_fig.update_layout(
                        height=280, margin=dict(l=24, r=24, t=24, b=8),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter, sans-serif", color=CHART_TEXT),
                        transition=dict(duration=500, easing="cubic-in-out"),
                    )
                    # A dial has nothing to zoom or pan, so its toolbar is
                    # pure clutter — turn it off rather than fade it.
                    st.plotly_chart(gauge_fig, use_container_width=True,
                                    config={"displayModeBar": False})

                with bcol:
                    st.markdown(
                        '<div style="font-family:Inter,sans-serif;font-size:0.6rem;letter-spacing:0.24em;'
                        'text-transform:uppercase;color:#7E786C;font-weight:600;margin:0.6rem 0 0.9rem;">'
                        'Sub-score contribution</div>',
                        unsafe_allow_html=True,
                    )
                    _w = composite["used_weights"]
                    render_score_bars([
                        (f"Value · {_w.get('value', 0) * 100:.0f}% weight", value_result["score"]),
                        (f"Quality · {_w.get('quality', 0) * 100:.0f}% weight", quality_result["score"]),
                        (f"Momentum · {_w.get('momentum', 0) * 100:.0f}% weight", momentum_result["score"]),
                        (f"Sentiment · {_w.get('sentiment', 0) * 100:.0f}% weight", sentiment_result["score"]),
                    ])

                st.warning(
                    "This score describes the CURRENT snapshot of measurable factors — "
                    "it is not a forecast, not personalized advice, and not validated as "
                    "a predictive strategy for this specific stock. Factor investing is a "
                    "statistical tendency across large portfolios over long horizons, not "
                    "a guarantee for any individual stock at any individual time."
                )

                with st.expander(f"Value breakdown — {value_result['score']:.0f}/100" if value_result["score"] is not None else "Value breakdown — N/A"):
                    if value_result["rows"]:
                        vdf = pd.DataFrame(value_result["rows"])[["metric", "value", "peer_mean", "peer_std", "z", "score", "source"]]
                        vdf.columns = ["Metric", "This stock", "Peer/anchor avg", "Peer/anchor std dev", "Z-score", "Score", "Compared against"]
                        st.dataframe(vdf.round(2), hide_index=True, use_container_width=True)
                        st.caption(
                            "Z-score = (this stock's ratio − comparison average) ÷ comparison "
                            "std dev, sign-flipped for P/E-style ratios so positive always means "
                            "'cheaper.' Score = 50 + 15 × Z-score, clipped to [0, 100]."
                        )
                    else:
                        st.write(value_result["note"])
                    if peer_df.empty or len(peer_df) < 3:
                        st.caption(
                            "⚠️ Used broad-market fallback anchors, not a real peer comparison — "
                            "add at least 3 tickers to your Watchlist (ideally in the same sector) "
                            "for a more meaningful Value score."
                        )

                with st.expander(f"Quality breakdown — {quality_result['score']:.0f}/100" if quality_result["score"] is not None else "Quality breakdown — N/A"):
                    if quality_result["checks"]:
                        for label, passed in quality_result["checks"]:
                            st.write(("✅ " if passed else "❌ ") + label)
                        st.caption(f"{sum(1 for _, p in quality_result['checks'] if p)} of {len(quality_result['checks'])} checks passed.")
                    else:
                        st.write(quality_result["note"])

                with st.expander(f"Momentum breakdown — {momentum_result['score']:.0f}/100" if momentum_result["score"] is not None else "Momentum breakdown — N/A"):
                    mcol1, mcol2 = st.columns(2)
                    mcol1.metric("Short-term technical score", f"{momentum_result['technical_score']:.0f}/100",
                                 help="From the Indicator Lean: moving averages, MACD, RSI.")
                    if momentum_result["momentum_12_1_score"] is not None:
                        _lookback_months = momentum_result["lookback_days"] / 21
                        mcol2.metric("12-1 momentum score", f"{momentum_result['momentum_12_1_score']:.0f}/100",
                                     help="This stock's return over the available lookback window (excluding the most recent month) vs. its sector benchmark over the same window.")
                        st.caption(
                            f"This stock: {momentum_result['stock_12_1'] * 100:+.1f}% · "
                            f"Benchmark ({factor_benchmark_ticker}): {momentum_result['benchmark_12_1'] * 100:+.1f}% · "
                            f"Relative: {momentum_result['relative_12_1'] * 100:+.1f} percentage points "
                            f"(measured over ~{_lookback_months:.0f} months of available history"
                            + ("" if _lookback_months >= 11 else ", less than a full 12 — shorter price history was available")
                            + ")."
                        )
                    else:
                        st.write(momentum_result["note"])

                with st.expander(f"Sentiment breakdown — {sentiment_result['score']:.0f}/100" if sentiment_result["score"] is not None else "Sentiment breakdown — N/A"):
                    if sentiment_result["rows"]:
                        sdf = pd.DataFrame(sentiment_result["rows"])
                        sdf["age_hours"] = sdf["age_hours"].apply(lambda x: f"{x:.1f}h ago" if x is not None else "unknown")
                        sdf["weight"] = sdf["weight"].round(2)
                        sdf.columns = ["Headline", "Tone", "Age", "Recency weight"]
                        st.dataframe(sdf, hide_index=True, use_container_width=True)
                        st.caption(
                            "Recency weight = 0.5 ^ (age in hours ÷ 24) — a headline from 24 "
                            "hours ago counts half as much as one from right now; from 48 hours "
                            "ago, a quarter as much."
                        )
                    else:
                        st.write(sentiment_result["note"])

    except Exception as e:
        if _is_rate_limit_error(e):
            st.error(
                "⏳ Yahoo Finance is rate-limiting requests right now. This happens "
                "sometimes on cloud hosting (many apps share the same IP range) and "
                "isn't something wrong with your app — it usually clears up within a "
                "minute or two. Try again shortly."
            )
        else:
            st.error(f"Something went wrong: {e}")

else:
    # Scoped to the two tabs that actually need a run, instead of a bare
    # st.write() outside any tab — which rendered below the tab strip and
    # showed up regardless of which tab was open.
    with tab_analysis:
        st.info("Enter a ticker in the command bar above and select **Run analysis** to begin.")
    with tab_fundamentals:
        st.info("Enter a ticker in the command bar above and select **Run analysis** to begin.")
    with tab_factors:
        st.info("Enter a ticker in the command bar above and select **Run analysis** to begin.")


with tab_watchlist:
    st.divider()
    st.header("Watchlist scan")
    st.caption(
        "Ranks the tickers you list by the same Indicator Lean + headline-tone "
        "heuristics used above — not a 'best stocks to buy' list. A high score "
        "just means more of these simple signals currently point the same way; "
        "it says nothing about future performance."
    )

    if scan_button:
        tickers_to_scan = [t.strip().upper() for t in watchlist_input.split(",") if t.strip()]
        if not tickers_to_scan:
            st.warning("Add at least one ticker to scan.")
        else:
            with st.spinner(f"Scanning {len(tickers_to_scan)} tickers..."):
                results_df = scan_watchlist(tickers_to_scan)

            if results_df.empty:
                st.error("Couldn't fetch data for any of these tickers — check the symbols and try again.")
            else:
                sorted_df = results_df.sort_values("Combined tilt score", ascending=False).reset_index(drop=True)

                col_bull, col_bear = st.columns(2)
                with col_bull:
                    st.subheader("Most bullish-tilted")
                    st.dataframe(sorted_df.head(5), hide_index=True, use_container_width=True)
                with col_bear:
                    st.subheader("Most bearish-tilted")
                    st.dataframe(
                        sorted_df.sort_values("Combined tilt score", ascending=True).head(5).reset_index(drop=True),
                        hide_index=True, use_container_width=True,
                    )

                with st.expander("See full scan results"):
                    st.dataframe(sorted_df, hide_index=True, use_container_width=True)

                st.warning(
                    "'Combined tilt score' just adds the indicator count (moving averages, MACD, RSI) "
                    "to the net headline tone (positive minus negative mentions). It's a simple, transparent "
                    "tally — not a validated ranking, and it can flip quickly as prices and headlines change."
                )

                # --- Ranked by Factor Score (Value + Quality + Momentum + Sentiment) ---
                st.subheader("Ranked by Factor Score")
                st.caption(
                    "Uses the default factor weights (Value 35% / Quality 20% / Momentum 30% / "
                    "Sentiment 15%) — open the 🧮 Factor Score tab on a single ticker to adjust "
                    "weights and see the full math behind each score. Value is z-scored against "
                    "this watchlist itself, so it's a real peer comparison here."
                )
                if "Factor score" in results_df.columns and results_df["Factor score"].notna().any():
                    factor_sorted_df = results_df.dropna(subset=["Factor score"]).sort_values(
                        "Factor score", ascending=False
                    ).reset_index(drop=True)
                    st.dataframe(
                        factor_sorted_df[["Ticker", "Last price", "Sector", "Factor score", "Indicator lean"]],
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.write("Not enough data to compute Factor Scores for this watchlist.")

                # --- Sector concentration (factual — counts tickers, not $ exposure) ---
                st.subheader("Sector concentration in this watchlist")
                sec_df = sector_concentration(results_df)
                st.dataframe(sec_df, hide_index=True, use_container_width=True)
                top_sector_pct = sec_df["% of watchlist"].max() if not sec_df.empty else 0
                if top_sector_pct >= 40:
                    top_sector_name = sec_df.iloc[0]["Sector"]
                    st.warning(
                        f"⚠️ {top_sector_pct:.0f}% of this watchlist is in {top_sector_name} — "
                        "concentrated in one sector means sector-specific news affects a large "
                        "share of it at once. This counts tickers equally, not by dollar amount, "
                        "so it may not match your actual portfolio weighting."
                    )
                else:
                    st.caption("Counts each ticker equally (not by dollar exposure) — not a substitute for your actual position sizes.")

                # --- Portfolio allocation (backward-looking, research interest only) ---
                st.subheader("Historical Sharpe-optimal allocation")
                with st.spinner("Running allocation search..."):
                    weights, best_sharpe = optimize_portfolio(tickers_to_scan)

                if weights is None:
                    st.write("Need at least 2 valid tickers with price history to compute this.")
                else:
                    weights_df = pd.DataFrame(
                        {"Ticker": list(weights.keys()), "Weight %": [w * 100 for w in weights.values()]}
                    ).sort_values("Weight %", ascending=False).reset_index(drop=True)
                    st.dataframe(weights_df, hide_index=True, use_container_width=True)
                    st.metric("Historical Sharpe ratio of this mix", f"{best_sharpe:.2f}")
                    st.warning(
                        "This is the weighting that WOULD have had the best risk-adjusted return "
                        "over the exact past window used here — found by randomly trying thousands "
                        "of combinations. This kind of backward-looking optimization is well known "
                        "(sometimes called 'Sharpe ratio overfitting') to not reliably predict which "
                        "mix will do best going forward. It's shown for research interest, not as a "
                        "recommended portfolio."
                    )
    else:
        st.info("Edit the watchlist above and select **Scan watchlist** to compare tickers.")


# ----------------------------------------------------------------------
# FOOTER — copyright year computed at render time so it never goes stale,
# plus the core disclaimer repeated once more at the very bottom of the
# page. No links to a Privacy Policy / Terms of Service page here since
# those don't exist yet as real documents — better to omit than to link
# somewhere that 404s.
# ----------------------------------------------------------------------
st.markdown(
    f"""
    <div class="tv-foot">
        <div class="tv-foot-mark">Tickveil</div>
        <div class="tv-foot-txt">
            © {datetime.now().year} · Educational tool only — not financial advice.<br>
            Price data via Yahoo Finance (free, delayed). Built solo, in active development.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
