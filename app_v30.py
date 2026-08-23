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
import time
import io
import secrets
from contextlib import contextmanager
import bcrypt
import storage
import scoring
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
# Persistence lives in storage.py, which picks its backend from
# DATABASE_URL: Postgres when one is configured, JSON files under user_data/
# otherwise. Keeping it in a separate module means it imports no Streamlit and
# can be tested directly. Username validation and path confinement moved there
# too, so there is exactly one implementation of each rather than a copy here
# that can drift.

# Per-account throttle on failed sign-ins. Held in memory, so it resets when
# the server restarts — but the point is to make online password guessing
# slow rather than to survive forever, and an in-memory counter costs
# nothing and needs no database.
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300


def is_valid_username(name: str) -> bool:
    """Letters, digits, dot, underscore, hyphen; 3-32 chars; no leading dot.

    Not cosmetic: the JSON backend interpolates usernames into filenames, so
    without a whitelist a name like "../../etc/cron.d/x" would direct a write
    outside the data directory. Defined once in storage.py and delegated to
    here so the two can never drift apart.
    """
    return bool(storage.USERNAME_PATTERN.match(name or ""))


@st.cache_resource(show_spinner=False)
def get_store():
    """
    The persistence backend, opened once per server process.

    Cached because Streamlit re-executes this script on every interaction —
    building a fresh Postgres connection pool per click would exhaust a
    free-tier connection limit within seconds. See storage.py for how the
    backend is chosen.
    """
    store = storage.get_storage()
    # First run against a fresh database: lift anything sitting in local JSON
    # into Postgres so an existing deployment's accounts survive the move.
    # Idempotent — accounts already present are skipped.
    if isinstance(store, storage.PostgresStorage):
        try:
            summary = storage.migrate_json_to_postgres(store)
            if summary["users"]:
                print(f"[tickveil] migrated {summary['users']} account(s) "
                      f"and {summary['documents']} document(s) into Postgres")
        except Exception as exc:
            print(f"[tickveil] JSON->Postgres migration skipped: {exc}")
    return store


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (hashed or "").encode("utf-8"))
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def dummy_password_hash() -> str:
    """
    A valid bcrypt hash of a random string, checked against whenever someone
    signs in with a username that doesn't exist. Its only job is to make the
    unknown-username path cost the same wall-clock time as the wrong-password
    path, so response timing can't be used to enumerate who has an account.

    Cached deliberately. Streamlit re-executes this entire script on every
    widget interaction, and generating a bcrypt hash costs ~270ms at the
    default work factor — computing it at module scope would have added that
    delay to every click in the app, not just to sign-in.
    """
    return hash_password(secrets.token_urlsafe(32))


def password_problem(password: str) -> str | None:
    """
    Returns a plain-English reason the password is too weak, or None.

    Length is the requirement that actually matters against offline
    cracking, so the floor is 10 rather than 8. The other two rules reject
    the two patterns that show up most in real breach corpora — a single
    repeated character, and an unbroken keyboard run — without imposing the
    symbol-and-digit theatre that pushes people toward "Password1!".
    """
    if len(password) < 10:
        return "Use at least 10 characters — length matters more than symbols."
    if len(set(password)) < 4:
        return "That's too few distinct characters to be hard to guess."
    lowered = password.lower()
    for run in ("qwertyuiop", "asdfghjkl", "zxcvbnm", "0123456789", "abcdefghijklmnop"):
        for i in range(len(run) - 5):
            if run[i:i + 6] in lowered:
                return "Avoid straight keyboard or alphabet runs."
    return None


def login_lockout_remaining(username_attempted: str) -> int:
    """Seconds still to wait before this account will accept another attempt."""
    record = st.session_state.get("login_failures", {}).get(username_attempted)
    if not record or record["count"] < MAX_LOGIN_ATTEMPTS:
        return 0
    elapsed = time.time() - record["last"]
    return max(0, int(LOGIN_LOCKOUT_SECONDS - elapsed))


def note_login_failure(username_attempted: str) -> None:
    failures = st.session_state.setdefault("login_failures", {})
    record = failures.setdefault(username_attempted, {"count": 0, "last": 0.0})
    # A lockout that has already expired starts the count over rather than
    # leaving the account permanently one failure from locking.
    if record["count"] >= MAX_LOGIN_ATTEMPTS and time.time() - record["last"] > LOGIN_LOCKOUT_SECONDS:
        record["count"] = 0
    record["count"] += 1
    record["last"] = time.time()


def clear_login_failures(username_attempted: str) -> None:
    st.session_state.get("login_failures", {}).pop(username_attempted, None)


st.set_page_config(page_title="Tickveil", page_icon="🕯️", layout="wide", initial_sidebar_state="collapsed")

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
   MARKET TAPE — the index strip across the top
   ================================================================== */
.tv-tapebar {
    display: flex;
    gap: 0;
    margin: 0.2rem 0 1.1rem;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm);
    background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012));
    backdrop-filter: blur(14px);
    box-shadow: var(--shadow-sm);
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    animation: reveal-up 0.6s var(--ease) both;
}
.tv-tapebar::-webkit-scrollbar { display: none; }
.tv-tick {
    flex: 0 0 auto;
    min-width: 140px;
    padding: 0.6rem 0.95rem;
    border-right: 1px solid var(--line);
    transition: background 0.4s var(--ease);
}
.tv-tick:last-child { border-right: none; }
.tv-tick:hover { background: rgba(212, 176, 120, 0.055); }
.tv-tick-top { display: flex; align-items: baseline; gap: 0.4rem; }
.tv-tick-name {
    font-family: var(--font-ui);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--text-200);
    white-space: nowrap;
}
/* The plain-language gloss that a real terminal leaves out */
.tv-tick-plain {
    font-family: var(--font-ui);
    font-size: 0.56rem;
    letter-spacing: 0.07em;
    color: var(--text-500);
    white-space: nowrap;
}
.tv-tick-bot { display: flex; align-items: baseline; gap: 0.5rem; margin-top: 0.22rem; white-space: nowrap; }
.tv-tick-val {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--text-100);
}
.tv-tick-chg {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 0.7rem;
    font-weight: 600;
    white-space: nowrap;
}
.tv-tick-chg.pos { color: var(--jade); }
.tv-tick-chg.neg { color: var(--rose); }

/* ==================================================================
   CONTEXT CHIP — "you are acting on AAPL"

   Shown by tabs that follow the global command bar instead of carrying
   their own ticker field. Stating the instrument costs one line and
   removes the whole class of error where you analyse one stock and plan
   a trade on another.
   ================================================================== */
.tv-context {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0.6rem 0.9rem;
    border-radius: 10px;
    border: 1px solid var(--line);
    border-left: 2px solid var(--gold-500);
    background: rgba(212, 176, 120, 0.05);
    font-family: var(--font-ui);
    font-size: 0.82rem;
    color: var(--text-300);
}
.tv-context b {
    font-family: var(--font-mono);
    font-size: 1rem;
    color: var(--gold-300);
    letter-spacing: 0.02em;
}
.tv-context span {
    font-size: 0.6rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--text-500);
}

/* ==================================================================
   EXPLAIN MODE — the plain-English gloss under a dense panel
   ================================================================== */
.tv-explain {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
    margin: 0.45rem 0 0.9rem;
    padding: 0.7rem 0.95rem;
    border-radius: 10px;
    border: 1px solid rgba(95, 207, 155, 0.16);
    border-left: 2px solid rgba(95, 207, 155, 0.55);
    background: rgba(95, 207, 155, 0.045);
    animation: fade-slide 0.45s var(--ease) both;
}
.tv-explain-tag {
    flex: 0 0 auto;
    font-family: var(--font-ui);
    font-size: 0.56rem;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--jade);
    padding-top: 0.16rem;
    white-space: nowrap;
}
.tv-explain-body {
    font-family: var(--font-ui);
    font-size: 0.83rem;
    line-height: 1.62;
    color: var(--text-200);
}

/* ==================================================================
   STATUS BAR — pinned terminal readout
   ================================================================== */
.tv-statusbar {
    position: fixed;
    left: 0; right: 0; bottom: 0;
    z-index: 60;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.4rem 1.1rem;
    border-top: 1px solid var(--line-gold);
    background: rgba(5, 7, 11, 0.975);
    backdrop-filter: blur(20px);
    font-family: var(--font-mono);
    font-size: 0.63rem;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--text-500);
}
.tv-sb-item { display: inline-flex; align-items: center; gap: 0.4rem; white-space: nowrap; }
.tv-sb-item b { color: var(--gold-400); font-weight: 600; }
.tv-sb-sep { width: 1px; height: 11px; background: var(--line-strong); }
.tv-sb-spacer { flex: 1 1 auto; }
.tv-sb-time { color: var(--text-300); }
.tv-sb-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--jade);
    box-shadow: 0 0 8px rgba(95, 207, 155, 0.9);
    animation: sb-blink 2.6s var(--ease) infinite;
}
/* Keeps the last of the page clear of the fixed bar */
[data-testid="stMainBlockContainer"], .block-container { padding-bottom: 3.2rem !important; }

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
@keyframes sb-blink { 0%, 62%, 100% { opacity: 1; } 80% { opacity: 0.28; } }

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
   RESPONSIVE — the phone is a first-class target, not a fallback

   Three things break a Streamlit dashboard on a phone, and all three are
   fixed here rather than hidden:

     1. st.columns does not stack. Four metrics in a row become four
        unreadable slivers. Below the breakpoint the horizontal block is
        allowed to wrap and each column takes a minimum width, so a
        four-metric row lands as a tidy 2x2 instead.
     2. Eleven tabs wrap into four stacked rows that push the content off
        screen. The rail becomes a single horizontally-scrolling line with
        scroll snapping and a fade at the right edge to signal there's
        more.
     3. Hover states are the only affordance on some controls, and a
        touchscreen has no hover. Tap targets go to the 44px minimum and
        the hover-lift transforms are dropped, since a card that lifts
        under a finger just looks like a rendering glitch.
   ================================================================== */

/* Tablet: mostly desktop, with a slightly tighter rhythm */
@media (max-width: 1024px) {
    [data-testid="stMainBlockContainer"], .block-container { padding-left: 1.1rem !important; padding-right: 1.1rem !important; }
    .tv-mark { font-size: 2.25rem; }
    .tv-verdict-r { font-size: 2.1rem; }
}

@media (max-width: 768px) {
    /* --- Rhythm --- */
    [data-testid="stMainBlockContainer"], .block-container {
        padding-top: 0.9rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
        padding-bottom: 1.5rem !important;
    }

    /* --- Masthead: brand over status, both left-aligned --- */
    .tv-brandrow { flex-direction: column; align-items: flex-start; gap: 0.7rem; }
    .tv-mark { font-size: 2.1rem; }
    .tv-markdot { width: 6px; height: 6px; margin-bottom: 0.4rem; }
    .tv-tagline { font-size: 0.56rem; letter-spacing: 0.22em; }
    .tv-masthead { padding: 1rem 0 1.1rem; }
    .tv-statusrow { gap: 0.35rem; }
    .tv-pill { font-size: 0.6rem; padding: 0.3rem 0.6rem; }

    /* --- Type --- */
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.2rem !important; }
    h3 { font-size: 1.06rem !important; margin-top: 1.6rem !important; }
    p, li, .stMarkdown { font-size: 0.92rem; }

    /* --- Columns wrap instead of squeezing --- */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.6rem !important;
    }
    [data-testid="stColumn"] {
        min-width: calc(50% - 0.3rem) !important;
        flex: 1 1 calc(50% - 0.3rem) !important;
    }
    /* Anything genuinely narrow (a 1:2:1 gutter) collapses away instead of
       leaving an orphan sliver beside the content it was padding. */
    [data-testid="stColumn"]:empty { display: none !important; }

    /* --- Metrics --- */
    [data-testid="stMetric"] { padding: 0.75rem 0.8rem 0.68rem; }
    [data-testid="stMetricValue"], [data-testid="stMetricValue"] p,
    [data-testid="stMetricValue"] div { font-size: 1.18rem !important; }
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p { font-size: 0.58rem !important; letter-spacing: 0.13em !important; }

    /* --- Tabs: one scrolling line, snapped --- */
    [data-baseweb="tab-list"], [role="tablist"] {
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        scroll-snap-type: x proximity;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
        /* Streamlit overlays its own ‹ › scroll arrows at each end of the
           rail. Without this inset the first and last tabs sit underneath
           them and read as clipped. */
        padding-left: 1.7rem !important;
        padding-right: 1.7rem !important;
        scroll-padding-left: 1.7rem;
        /* Fades the right edge so it's visible that the rail continues */
        -webkit-mask-image: linear-gradient(90deg, #000 90%, transparent 100%);
        mask-image: linear-gradient(90deg, #000 90%, transparent 100%);
    }
    /* Same treatment for the market tape, which also scrolls past the edge */
    .tv-tapebar {
        -webkit-mask-image: linear-gradient(90deg, #000 90%, transparent 100%);
        mask-image: linear-gradient(90deg, #000 90%, transparent 100%);
    }
    [data-baseweb="tab-list"]::-webkit-scrollbar, [role="tablist"]::-webkit-scrollbar { display: none; }
    [data-baseweb="tab"], [data-testid="stTab"], [role="tab"] {
        flex: 0 0 auto !important;
        scroll-snap-align: start;
        padding: 0.55rem 0.75rem !important;
        min-height: 40px;
        display: flex !important;
        align-items: center;
    }
    [data-baseweb="tab"] p, [data-testid="stTab"] p, [role="tab"] p {
        font-size: 0.63rem !important;
        letter-spacing: 0.1em !important;
    }

    /* --- Quote tape: two-column grid, headline row spanning both --- */
    .tv-tape {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.75rem 0.9rem;
        padding: 0.85rem 0.9rem;
        align-items: start;
    }
    .tv-tape-sym { grid-column: 1 / -1; font-size: 1.3rem; }
    .tv-tape-last { font-size: 1.45rem; }
    .tv-tape-chg { justify-self: start; font-size: 0.85rem; }
    .tv-tape-sep { display: none; }
    .tv-tape::after { display: none; }  /* the sweep reads as a glitch on a small grid */

    /* --- Market tape: free horizontal scroll --- */
    .tv-tick { min-width: 118px; padding: 0.55rem 0.8rem; }

    /* --- Verdict: stack, big number to the left under the text --- */
    .tv-verdict {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.85rem;
        padding: 1.1rem 1.1rem;
    }
    .tv-verdict-val { font-size: 1.55rem; }
    .tv-verdict-note { font-size: 0.83rem; max-width: none; }
    .tv-verdict-r { font-size: 2.1rem; text-align: left; }
    .tv-verdict-rsub { text-align: left; }

    /* --- Explain callout stacks its tag above the text --- */
    .tv-explain { flex-direction: column; gap: 0.3rem; padding: 0.65rem 0.8rem; }
    .tv-explain-body { font-size: 0.85rem; }

    /* --- Touch targets --- */
    .stButton > button, .stDownloadButton > button,
    [data-testid="stFormSubmitButton"] > button,
    button[data-testid^="stBaseButton-"] {
        min-height: 44px;
        padding: 0.7rem 1rem !important;
        width: 100%;
    }
    input, textarea, [data-testid="stTextInputField"] { min-height: 44px; font-size: 16px !important; }
    [data-testid="stSlider"] { padding: 0.3rem 0.4rem; }

    /* --- No hover physics on a touchscreen --- */
    [data-testid="stVerticalBlockBorderWrapper"]:hover,
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .tv-card-mark):hover,
    [data-testid="stMetric"]:hover,
    .stButton > button:hover, button[data-testid^="stBaseButton-"]:hover {
        transform: none !important;
    }
    .modebar { display: none !important; }

    /* --- Wide content scrolls inside itself, never the page body --- */
    [data-testid="stDataFrame"], [data-testid="stTable"] { overflow-x: auto !important; }
    [data-testid="stPlotlyChart"] { padding: 0.3rem; }

    /* --- Status bar: in the flow, not pinned over the content --- */
    .tv-statusbar {
        position: static;
        flex-wrap: wrap;
        gap: 0.4rem 0.6rem;
        border-radius: var(--radius-sm);
        border: 1px solid var(--line-gold);
        margin-top: 1.4rem;
        font-size: 0.58rem;
    }
    .tv-sb-spacer { display: none; }

    /* --- Auth screen --- */
    .tv-auth { padding: 1.6rem 0 1rem; }
    .tv-auth-mark { font-size: 2.6rem; }
    .tv-auth-lede { font-size: 0.86rem; }
    .tv-foot { margin-top: 2.2rem; }
}

/* Very narrow phones: give the metrics a full row each rather than
   letting two of them share 160px and wrap their labels to three lines. */
@media (max-width: 420px) {
    [data-testid="stColumn"] { min-width: 100% !important; flex-basis: 100% !important; }
    .tv-tape { grid-template-columns: 1fr; }
    .tv-mark { font-size: 1.85rem; }
    .tv-auth-mark { font-size: 2.15rem; }
    .tv-tape-last { font-size: 1.3rem; }
}

/* Landscape phone: the fixed status bar would eat a third of the height */
@media (max-height: 520px) {
    .tv-statusbar { position: static; }
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


# NOTE ON PLACEMENT: this lives here, above the tab bodies, because
# Streamlit re-executes the whole script top-to-bottom on every
# interaction. The Settings tab calls it, and the Settings tab body runs
# long before the analysis helpers further down are defined — so keeping
# this next to those helpers raised NameError the moment anyone actually
# pressed the send button.
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


store = get_store()

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
                waiting = login_lockout_remaining(login_username)
                if waiting:
                    st.error(
                        f"Too many failed attempts for this account. "
                        f"Try again in {waiting // 60}m {waiting % 60}s."
                    )
                else:
                    user_record = store.get_user(login_username) if is_valid_username(login_username) else None
                    # bcrypt is run even when the username is unknown, against a
                    # throwaway hash. Returning instantly for a bad username and
                    # slowly for a good one leaks which accounts exist, and that
                    # timing gap is measurable over a network.
                    stored_hash = (user_record or {}).get("password_hash") or dummy_password_hash()
                    password_ok = check_password(login_password, stored_hash)

                    if user_record and password_ok:
                        clear_login_failures(login_username)
                        st.session_state["authenticated"] = True
                        st.session_state["username"] = login_username
                        st.session_state["name"] = user_record.get("name", login_username)
                        st.rerun()
                    else:
                        note_login_failure(login_username)
                        left = MAX_LOGIN_ATTEMPTS - st.session_state["login_failures"][login_username]["count"]
                        # One message for both failure modes, so the form never
                        # confirms that a username is registered.
                        st.error(
                            "Incorrect username or password."
                            + (f" {left} attempt(s) left before a temporary lock." if 0 < left <= 2 else "")
                        )

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
                _pw_problem = password_problem(reg_password)
                if not reg_name or not reg_username or not reg_password:
                    st.error("Please fill in all fields.")
                elif not is_valid_username(reg_username):
                    st.error(
                        "Usernames can use letters, numbers, dot, underscore and hyphen, "
                        "must be 3–32 characters, and can't start with a dot."
                    )
                elif store.user_exists(reg_username):
                    st.error("That username is already taken.")
                elif reg_password != reg_password_confirm:
                    st.error("Passwords don't match.")
                elif _pw_problem:
                    st.error(_pw_problem)
                else:
                    created = store.create_user(reg_username, {
                        "name": reg_name,
                        "password_hash": hash_password(reg_password),
                        "totp_enabled": False,
                        "totp_secret": None,
                    })
                    # create_user is atomic, so it also catches the race where
                    # two people claim the same name at the same moment.
                    if created:
                        st.success("Account created — head to the 'Sign in' tab.")
                    else:
                        st.error("That username was just taken — try another.")

        st.markdown(
            '<div style="text-align:center;margin-top:1.6rem;font-family:Inter,sans-serif;'
            'font-size:0.66rem;letter-spacing:0.2em;text-transform:uppercase;color:#7E786C;">'
            'Passwords hashed with bcrypt · Optional TOTP two-factor</div>',
            unsafe_allow_html=True,
        )

    st.stop()

# --- Password auth succeeded. Now handle optional 2FA. ---
username = st.session_state["username"]
user_record = store.get_user(username)
if user_record is None:
    # The account vanished under an authenticated session (deleted, or the
    # storage backend was switched). Drop the session rather than crashing.
    for _k in ["authenticated", "username", "name"]:
        st.session_state.pop(_k, None)
    st.rerun()
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
# MARKET TAPE — the strip of index levels across the top of every trading
# terminal. Here it does one extra job: each instrument carries a short
# plain-English name ("US large caps", "Fear gauge") alongside its symbol,
# so someone who doesn't know what ^GSPC or ^VIX is can still read the row.
#
# Fetched in a single batched download and cached for 5 minutes. If the
# fetch fails the strip renders nothing at all rather than a row of dashes
# — a broken tape is worse than no tape.
# ----------------------------------------------------------------------
TAPE_INSTRUMENTS = [
    ("^GSPC", "S&P 500", "US large caps"),
    ("^IXIC", "Nasdaq", "US tech-heavy"),
    ("^DJI", "Dow Jones", "US blue chips"),
    ("^VIX", "VIX", "Fear gauge"),
    ("^TNX", "US 10Y", "Bond yield"),
    ("GC=F", "Gold", "Safe haven"),
    ("BTC-USD", "Bitcoin", "Crypto"),
]


@st.cache_data(ttl=300, show_spinner=False)
def get_market_tape() -> list[dict]:
    """Last level and session change for each tape instrument."""
    symbols = [sym for sym, _, _ in TAPE_INSTRUMENTS]
    try:
        raw = yf_call_with_retry(
            lambda: yf.download(symbols, period="5d", interval="1d",
                                progress=False, group_by="ticker", auto_adjust=True),
            retries=1, delay_seconds=2.0,
        )
    except Exception:
        return []
    if raw is None or raw.empty:
        return []

    out = []
    for symbol, label, plain in TAPE_INSTRUMENTS:
        try:
            closes = raw[symbol]["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            if prev == 0:
                continue
            out.append({
                "label": label,
                "plain": plain,
                "value": last,
                "change_pct": (last / prev - 1) * 100,
            })
        except Exception:
            continue  # one bad instrument shouldn't empty the whole tape
    return out


def render_market_tape() -> None:
    """Draws the index strip, or nothing if the data didn't arrive."""
    try:
        rows = get_market_tape()
    except Exception:
        rows = []
    if not rows:
        return

    cells = []
    for row in rows:
        direction = "pos" if row["change_pct"] >= 0 else "neg"
        arrow = "▲" if row["change_pct"] >= 0 else "▼"
        # Index levels want thousands separators; a sub-100 level (VIX, the
        # 10-year yield) wants two decimals and no separator.
        value = f"{row['value']:,.2f}" if row["value"] >= 100 else f"{row['value']:.2f}"
        cells.append(
            f'<div class="tv-tick">'
            f'<div class="tv-tick-top">'
            f'<span class="tv-tick-name">{html_lib.escape(row["label"])}</span>'
            f'<span class="tv-tick-plain">{html_lib.escape(row["plain"])}</span>'
            f'</div>'
            f'<div class="tv-tick-bot">'
            f'<span class="tv-tick-val">{value}</span>'
            f'<span class="tv-tick-chg {direction}">{arrow} {abs(row["change_pct"]):.2f}%</span>'
            f'</div></div>'
        )
    st.markdown(f'<div class="tv-tapebar">{"".join(cells)}</div>', unsafe_allow_html=True)


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
render_market_tape()

st.caption(
    "Educational tool only. Shows public price data, indicators, news, and "
    "a statistical price range based on past volatility. Nothing here is "
    "a prediction or a buy/sell recommendation."
)

# ----------------------------------------------------------------------
# EXPLAIN MODE — the one thing a Bloomberg terminal will never do for you.
#
# A professional terminal assumes you already know what a z-score against a
# peer group means. That assumption is exactly what makes it unusable for
# the first year. Explain mode keeps the dense readout intact and adds a
# plain-English sentence under each panel saying what the number actually
# means and what it does NOT mean — so the same screen serves someone
# learning and someone who just wants the figure.
#
# It defaults ON. Someone who finds it noisy turns it off in one click;
# someone who needed it would never have known to turn it on.
# ----------------------------------------------------------------------
# Only the explain toggle sits in the header. Logout lives in Settings
# beside the other account controls: on a phone every header control becomes
# a full-width row, and a full-width LOG OUT button directly under the
# masthead reads as the primary action on the page, which it very much isn't.
_head_pad, _head_toggle = st.columns([5, 2])
with _head_toggle:
    explain_mode = st.toggle(
        "Explain mode",
        value=True,
        key="explain_mode",
        help="Adds a plain-English note under each panel explaining what the "
             "numbers mean and what they don't. Turn it off for a dense, "
             "terminal-style readout.",
    )


def explain(text: str) -> None:
    """
    Renders a plain-English gloss for the panel above, when explain mode is on.

    Deliberately a no-op rather than a collapsed expander when off: an
    expander still occupies a row and breaks the density that makes the
    terse mode worth having.
    """
    if not st.session_state.get("explain_mode", True):
        return
    st.markdown(
        f'<div class="tv-explain"><span class="tv-explain-tag">In plain English</span>'
        f'<span class="tv-explain-body">{html_lib.escape(text)}</span></div>',
        unsafe_allow_html=True,
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

# The sidebar is gone on purpose. It only ever held the signed-in name and a
# logout button, and on a phone Streamlit renders it as an overlay that
# squeezes the real content into a ~150px column until it's dismissed. The
# name already appears in the masthead, and logout moved into the header row
# above, where it costs no layout and needs no drawer to reach.

# ----------------------------------------------------------------------
# PER-USER DOCUMENTS — watchlist, Telegram credentials, trade journal and
# the digest snapshot.
#
# All four go through the storage backend rather than touching the
# filesystem directly, so the same code path works whether this is running
# locally against JSON files or in production against Postgres. Each is a
# JSON blob owned by one account; the database treats them as opaque, so
# changing what a journal entry contains needs no schema migration.
#
# Writes are best-effort: a failed save costs the user that one change, and
# is never worth taking the whole page down for.
# ----------------------------------------------------------------------
DEFAULT_WATCHLIST = "AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, XOM, JNJ"


def load_saved_watchlist() -> str:
    doc = store.get_doc(username, "watchlist", None) or {}
    return doc.get("watchlist", DEFAULT_WATCHLIST)


def save_watchlist(watchlist_text: str) -> None:
    try:
        store.put_doc(username, "watchlist", {"watchlist": watchlist_text})
    except Exception:
        pass


def load_saved_telegram() -> dict:
    return store.get_doc(username, "telegram", None) or {"bot_token": "", "chat_id": ""}


def save_telegram(bot_token: str, chat_id: str) -> None:
    try:
        store.put_doc(username, "telegram", {"bot_token": bot_token, "chat_id": chat_id})
    except Exception:
        pass


def load_journal() -> list[dict]:
    return store.get_doc(username, "journal", None) or []


def save_journal(entries: list[dict]) -> None:
    try:
        store.put_doc(username, "journal", entries)
    except Exception:
        pass


def load_digest_snapshot() -> dict:
    return store.get_doc(username, "digest_snapshot", None) or {}


def save_digest_snapshot(snapshot: dict) -> None:
    try:
        store.put_doc(username, "digest_snapshot", snapshot)
    except Exception:
        pass




# ----------------------------------------------------------------------
# COMMAND BAR — global, and deliberately OUTSIDE the tab set.
#
# It used to live inside the Analysis tab, which broke the app's core mental
# model in two ways. Fundamentals and Factor Score depend on it, so their
# empty state read "enter a ticker in the command bar above" while pointing
# at a control that was on a different tab and therefore not on screen at
# all. And because each tool tab carried its own ticker field, you could
# analyse AAPL and then plan a trade on MSFT with nothing flagging the
# mismatch — in a tool about stop levels and position sizing, that is the
# worst outcome the interface can produce.
#
# A terminal has one command line and many views of the result. This is that
# command line: one instrument, set in one place, visible from every tab.
# ----------------------------------------------------------------------

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

# Everything needed to run an analysis sits in one form, so adjusting a
# dropdown or slider doesn't trigger a rerun until you're ready to go.
with st.form("command_bar"):
    cb_col1, cb_col2, cb_col3, cb_col4 = st.columns([1.1, 2.2, 1, 1.4])
    with cb_col1:
        market_choice = st.selectbox(
            "Market",
            options=list(MARKET_SUFFIX_MAP.keys()),
            help="Pick a market and just type the base ticker/code — the right suffix is added "
                 "automatically. E.g. Hong Kong: '0700' becomes '0700.HK'.",
        )
    with cb_col2:
        raw_ticker_input = st.text_input(
            "Ticker or code", "AAPL",
            help="e.g. AAPL, 0700, 005930, D05. Press Enter to run — no need to click the button.",
        ).upper().strip()
    with cb_col3:
        period_choice = st.selectbox("Timeframe", options=CHART_OPTIONS, index=4)  # default "6mo"
    with cb_col4:
        horizon_days = st.slider("Horizon (days)", 1, 30, 5)
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

# The single source of truth for "which instrument am I looking at". Every
# tab reads this rather than carrying its own ticker field.
current_ticker = (st.session_state.get("analysis_run") or {}).get("ticker", "")


# Tab labels are plain words, no emoji. The CSS renders them as an
# uppercase letterspaced segmented control — emoji in a navigation rail is
# the fastest way to make a finance product look like a hobby project, and
# the icons weren't carrying meaning the words didn't already carry.
# Ordered by kind rather than by when each was built: the three views scoped
# to the command bar's instrument come first, then the tools you act with,
# then reference material and settings. "Market News" is gone as a top-level
# tab — it held a single collapsed accordion on an otherwise empty screen,
# which is two clicks and a whole navigation slot for one list. It now sits
# under the stock-specific news in Analysis, where market context is
# actually being read.
(tab_analysis, tab_fundamentals, tab_factors,
 tab_tradesetup, tab_journal, tab_watchlist, tab_digest,
 tab_multiasset, tab_calendar, tab_settings) = st.tabs(
    ["Analysis", "Fundamentals", "Factor Score",
     "Trade Setup", "Journal", "Watchlist", "Daily Digest",
     "Multi-Asset", "Calendar", "Settings"]
)

with tab_watchlist:
    if "watchlist_text" not in st.session_state:
        st.session_state.watchlist_text = load_saved_watchlist()

    # The help text has always claimed this saves automatically, but the write
    # only ran inside the Scan handler — so editing the list and navigating
    # away silently discarded it. An on_change callback makes the promise true.
    def _persist_watchlist() -> None:
        save_watchlist(st.session_state.watchlist_text)

    watchlist_input = st.text_area(
        "Tickers to scan (comma-separated)",
        key="watchlist_text",
        on_change=_persist_watchlist,
        help="Edit this to any tickers you want compared. Saved automatically so it's here next time you open the app.",
    )
    scan_button = st.button("Scan watchlist", type="primary")
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
    st.caption(f"Signed in as {username}.")
    if st.button("Log out of this session"):
        for _key in ["authenticated", "username", "name", f"totp_verified_{username}"]:
            st.session_state.pop(_key, None)
        st.rerun()

    st.markdown("**Change password**")
    with st.form("change_password_form"):
        current_pw = st.text_input("Current password", type="password")
        new_pw = st.text_input("New password", type="password")
        new_pw_confirm = st.text_input("Confirm new password", type="password")
        pw_submitted = st.form_submit_button("Update password")

    if pw_submitted:
        _new_pw_problem = password_problem(new_pw)
        if not check_password(current_pw, user_record["password_hash"]):
            st.error("Current password is incorrect.")
        elif new_pw != new_pw_confirm:
            st.error("New passwords don't match.")
        elif _new_pw_problem:
            st.error(_new_pw_problem)
        else:
            user_record["password_hash"] = hash_password(new_pw)
            store.update_user(username, user_record)
            st.success("Password updated.")

    st.divider()
    st.markdown("**Two-factor authentication (2FA)**")
    if totp_enabled:
        st.write("✅ 2FA is enabled on your account.")
        if st.button("Disable 2FA"):
            user_record["totp_enabled"] = False
            user_record["totp_secret"] = None
            store.update_user(username, user_record)
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
                    store.update_user(username, user_record)
                    del st.session_state["setting_up_2fa"]
                    del st.session_state["pending_totp_secret"]
                    st.success("2FA enabled!")
                    st.rerun()
                else:
                    st.error("Incorrect code — try again.")




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
    live free API exists for this data. When supplied it is blended 50/50
    with VIX in the composite's macro haircut; without it the haircut uses
    VIX alone.
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

    # The series itself is returned alongside the summary. Previously only the
    # summary came back, so an uploaded GPR file was described in a caption and
    # then had no effect whatsoever on any score — the composite looked for a
    # series that nothing ever set.
    series = df.set_index(date_col)[gpr_col].astype(float).rename("GPR")

    return {"current": float(current), "six_month_avg": float(avg), "level": level,
            "as_of": df[date_col].iloc[-1].strftime("%Y-%m-%d"), "series": series}


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
def safe_link(url: str) -> str:
    """
    Returns the URL only if it's an ordinary web link, otherwise "".

    Headlines and their URLs arrive from an upstream feed and get rendered
    into markdown links. Without this check a "javascript:" or "data:" URL
    from a poisoned feed item would become a clickable script in the page.
    Allowing exactly http and https is the whole fix, and it costs nothing.
    """
    candidate = (url or "").strip()
    if candidate.lower().startswith(("http://", "https://")):
        # Whitespace and control characters can be used to smuggle a second
        # scheme past a naive prefix check, so reject rather than clean them.
        if not re.search(r"[\s<>\"']", candidate):
            return candidate
    return ""


def markdown_safe(text: str) -> str:
    """
    Neutralises markdown control characters in text taken from a feed.

    Interpolating a raw headline into f"[{title}]({link})" lets a title
    containing a bracket close the link early and inject arbitrary markdown
    after it. Escaping the delimiters keeps the headline as text.
    """
    return re.sub(r"([\[\]()*_`~<>|\\])", r"\\\1", text or "")


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
            "link": safe_link(link),
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

    # The macro haircut is a market-wide condition, so VIX is fetched once and
    # applied to every ticker rather than re-downloaded per row.
    try:
        scan_vix = load_daily_data("^VIX")["Close"]
    except Exception:
        scan_vix = None

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

            # Same composite the Factor Score tab computes, so the two views
            # cannot disagree about what a ticker scores.
            tech_series, _ind = scoring.technical_score(df)
            tech_now = float(tech_series.iloc[-1]) if pd.notna(tech_series.iloc[-1]) else None

            if scan_vix is not None:
                haircut = float(scoring.macro_risk_penalty(df.index, scan_vix).iloc[-1])
            else:
                haircut = 0.0

            scan_items = []
            for _n, _tone in zip(news, sentiments):
                _ts = _parse_news_timestamp(_n.get("published", ""))
                _age_h = ((datetime.now() - _ts).total_seconds() / 3600.0) if _ts else 24.0
                scan_items.append({
                    "sentiment": {"positive": 1.0, "negative": -1.0, "neutral": 0.0}[_tone],
                    "age_hours": max(_age_h, 0.0),
                })
            _sent_info = scoring.decayed_sentiment(scan_items)
            _sent_sigma = (scoring.sentiment_to_sigma(_sent_info["score"], _sent_info["effective_n"])
                           if _sent_info else None)

            composite = scoring.combine(tech_now, _sent_sigma, haircut)

            rows.append({
                "Ticker": t,
                "Last price": round(float(latest["Close"]), 2),
                "Sector": sector,
                "Indicator lean": lean,
                "Headlines +/-": f"{n_pos}/{n_neg}",
                "Combined tilt score": combined_score,
                "Composite score": round(composite["final"], 2) if composite["final"] is not None else None,
                "Reading": composite["reading"],
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



with tab_tradesetup:
    st.subheader("Trade goal planner")
    explain(
        "You choose a gain you are aiming for and a loss you could live with. This converts those into "
        "actual price levels, then checks this stock's own past to report how often it historically moved "
        "that far within the window. Past frequency is not a probability for this time."
    )
    # The long-form caveat that used to sit here as a warning block is now on
    # the result, not above the controls. Stacking a four-line explain
    # callout and a six-line amber warning ahead of the first input pushed
    # the actual tool below the fold and trained people to skip both.

    # This tab follows the command bar rather than carrying its own ticker
    # field, so you cannot analyse one stock and silently plan a trade on
    # another.
    if not current_ticker:
        st.info("Enter a ticker in the command bar at the top and run an analysis to plan a trade.")
    else:
        ts_head, ts_dir = st.columns([1, 1])
        with ts_head:
            st.markdown(
                f'<div class="tv-context">Planning for <b>{html_lib.escape(current_ticker)}</b>'
                f'<span>change it in the command bar above</span></div>',
                unsafe_allow_html=True,
            )
        ts_ticker = current_ticker
        with ts_dir:
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

            # The caveat belongs here, attached to the two percentages someone
            # might actually act on — not stacked above the controls where it
            # was read as boilerplate and skipped along with everything else.
            st.warning(
                "These two percentages are how often this stock's own past year actually "
                "moved that far within the window — real historical frequency, not a "
                "confidence score and not a probability for this trade. A new event "
                "(earnings, news) can move it outside anything in that sample. This does "
                "not tell you whether to take the trade."
            )

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
                # Defaults to whatever the command bar has loaded, so logging the
                # trade you just analysed doesn't mean retyping its symbol.
                j_ticker = st.text_input(
                    "Ticker", value=prefill.get("ticker", current_ticker or "")
                ).upper().strip()
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
                    "alongside VIX. When supplied it is blended 50/50 with VIX in the "
                    "macro haircut on the Factor Score tab."
                )
                gpr_file = st.file_uploader("GPR index CSV", type="csv", key=f"gpr_upload_{ticker}")
                if gpr_file:
                    gpr_result = parse_gpr_upload(gpr_file)
                    if gpr_result:
                        # Held in session state so the composite score on the
                        # Factor Score tab can fold it into the macro haircut.
                        st.session_state["gpr_series"] = gpr_result["series"]
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
            explain(
                "Four well-known indicators are checked, and this counts how many currently point up "
                "versus down. It describes where the price sits right now relative to its own recent "
                "history — it is not a forecast, and a strong tilt has no bearing on what happens "
                "tomorrow."
            )
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
            explain(
                "This takes how much the stock has bounced around lately and projects that same amount of "
                "bounce forward. A wider range just means a more volatile stock. It assumes no direction "
                "at all, so it is a measure of typical movement, not a guess at where the price is "
                "heading."
            )
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
            explain(
                "Every window of this length in the past year is measured, then sorted. The median is the "
                "middle outcome; the worst and best 10% show the tails. This is what already happened, "
                "not what will happen — and one genuine surprise can land outside the entire range."
            )
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
            explain(
                "Read this as a prompt to look closer, not as a score. Disagreement is usually the more "
                "interesting case: the chart describes what buyers and sellers have already done, while "
                "the headlines describe something that may not be reflected in the price yet."
            )
            st.write(compute_signal_agreement(_score, get_news_items(ticker)))
            st.caption(
                "This just states whether the technical read and headline tone happen to "
                "point the same way right now — it is NOT a probability or confidence score. "
                "Agreement between two weak signals doesn't make either one strong."
            )

            # --- News ---
            st.subheader("Recent news")
            explain(
                "Tone is tagged by matching finance phrases like 'beats expectations' or 'cuts guidance' "
                "first, and general word tone second. It reads the wording, not the substance — always "
                "open the headline itself before drawing any conclusion from the colour of the chip."
            )
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
                        _t = markdown_safe(item["title"])
                        st.markdown(f"**[{_t}]({item['link']})**" if item["link"] else f"**{_t}**")
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
            st.caption("Fundamentals, analyst estimates, earnings and risk metrics for the instrument loaded in the command bar.")

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
            explain(
                "These are professional analysts' published price targets, averaged. They are opinions "
                "with a mixed track record, frequently revised after the fact, and often clustered "
                "because analysts read each other. Treat the spread between high and low as more "
                "informative than the average."
            )
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
            explain(
                "Max drawdown is the worst peak-to-trough fall in the window: the loss you would have sat "
                "through at the very worst moment. Annualised volatility scales daily swings up to a "
                "yearly figure so two stocks with different histories can be compared fairly."
            )
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
            explain(
                "This asks a narrow question honestly: when this same signal appeared in this stock's "
                "past, what happened next on average? Sample sizes are small and past windows overlap, so "
                "read it as a sanity check on the signal, not as evidence that it works."
            )
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
            # ----------------------------------------------------------
            # COMPOSITE SCORE
            #
            # Replaces the previous Value/Quality/Momentum/Sentiment model.
            # The mathematics lives in scoring.py, which imports no Streamlit
            # so it can be tested directly — see test_scoring.py.
            # ----------------------------------------------------------
            st.subheader(f"Composite score — {ticker}")
            explain(
                "One number built from three things: where the price sits relative to its own recent "
                "history, how recent headlines read, and how nervous the wider market is. The first two "
                "are blended; the third is applied as a confidence haircut rather than a direction. The "
                "score is measured in standard deviations, so zero is this stock's own normal and +1 "
                "means one standard deviation above it."
            )

            with st.expander("How this score works — read this first"):
                st.markdown("""
**The three parts.**

1. **Technical** — four measurements of the price series, each expressed as a z-score against
   this stock's own past six months: the gap between its 50-day and 200-day averages (trend),
   RSI (momentum), position inside its Bollinger band (how stretched it is), and the 20-day
   change in on-balance volume (whether volume is arriving on up days or down days).
2. **Sentiment** — recent headline tone, weighted so a headline's influence halves every 24 hours,
   then shrunk toward zero when there are only a handful of headlines. Three articles are a rumour;
   thirty saying the same thing is a signal.
3. **Macro** — VIX, optionally combined with an uploaded geopolitical-risk index. This is *not*
   a direction. It is a haircut on the magnitude of the whole score, because when the market is
   volatile every signal is less informative, not more bearish.

**Why it is measured in standard deviations.** A weighted sum of z-scores does not itself have a
standard deviation of one — it has √(wᵀΣw), which depends on how correlated the parts happen to be
that month. Left alone, a threshold of "+0.5" silently means 1.7σ when the components are
independent and 1.2σ when they are correlated. The composite is therefore rescaled by its own
rolling deviation, so one unit is one standard deviation for every ticker, always.

**What it will not do.** It will not tell you what happens next. The backtest below is included
specifically so you can see how weak the relationship is, and it uses a bootstrap p-value because
the textbook one is invalid here — the windows overlap and the score is highly persistent, which
makes ordinary regression p-values reject a true null roughly half the time.
""")

            # --- Controls -------------------------------------------------
            fs_c1, fs_c2, fs_c3 = st.columns(3)
            with fs_c1:
                w_tech = st.slider("Technical weight", 0, 100, 55, 5, key="fs_w_tech") / 100
            with fs_c2:
                w_sent = st.slider("Sentiment weight", 0, 100, 45, 5, key="fs_w_sent") / 100
            with fs_c3:
                horizon_bt = st.select_slider(
                    "Backtest horizon (trading days)", options=[5, 10, 21, 42],
                    value=10, key="fs_horizon",
                    help="How far ahead the backtest looks when checking whether this score "
                         "had any relationship with what happened next.",
                )

            auto_beta = st.checkbox(
                "Estimate this stock's volatility sensitivity from its own history",
                value=True, key="fs_auto_beta",
                help="Regresses daily returns on daily changes in VIX to size the macro haircut, "
                     "instead of assuming a fixed value for every stock.",
            )

            fs_cfg = scoring.ScoringConfig(
                w_technical=w_tech if (w_tech + w_sent) > 0 else 0.55,
                w_sentiment=w_sent if (w_tech + w_sent) > 0 else 0.45,
                forward_days=horizon_bt,
            )

            with st.spinner("Scoring..."):
                # 1. Technical series over the stock's daily history.
                tech_series, indicator_frame = scoring.technical_score(daily_df, fs_cfg)

                # 2. Macro haircut from VIX (plus GPR if one was uploaded).
                try:
                    vix_series = load_daily_data("^VIX")["Close"]
                except Exception:
                    vix_series = None

                beta_info = None
                if vix_series is not None and auto_beta:
                    beta_info = scoring.estimate_geo_beta(daily_df["Close"], vix_series, fs_cfg)
                    if beta_info:
                        fs_cfg.geo_beta = beta_info["geo_beta"]

                if vix_series is not None:
                    penalty_series = scoring.macro_risk_penalty(
                        daily_df.index, vix_series,
                        st.session_state.get("gpr_series"), fs_cfg,
                    )
                    latest_penalty = float(penalty_series.iloc[-1])
                else:
                    penalty_series = pd.Series(0.0, index=daily_df.index)
                    latest_penalty = 0.0

                # 3. Sentiment from the headlines already fetched for this ticker.
                #    Only a current reading is possible — there is no archive of
                #    past headlines to rebuild a historical series from, which is
                #    why the backtest below covers the technical component only.
                sentiment_items = []
                for _item in news_items:
                    _tone = tag_sentiment(_item["title"] + " " + _item.get("description", ""))
                    _age = _parse_news_timestamp(_item.get("published", ""))
                    _age_hours = ((datetime.now() - _age).total_seconds() / 3600.0
                                  if _age is not None else 24.0)
                    sentiment_items.append({
                        "sentiment": {"positive": 1.0, "negative": -1.0, "neutral": 0.0}[_tone],
                        "age_hours": max(_age_hours, 0.0),
                    })
                sentiment_info = scoring.decayed_sentiment(sentiment_items, cfg=fs_cfg)
                sentiment_sigma = (
                    scoring.sentiment_to_sigma(sentiment_info["score"], sentiment_info["effective_n"])
                    if sentiment_info else None
                )

                latest_tech = float(tech_series.iloc[-1]) if pd.notna(tech_series.iloc[-1]) else None
                result = scoring.combine(latest_tech, sentiment_sigma, latest_penalty, fs_cfg)

            if result["final"] is None:
                st.error(
                    "Not enough price history to score this ticker. The trend component needs "
                    "roughly a year of daily data before it means anything."
                )
            else:
                render_verdict(
                    "Composite score",
                    result["reading"],
                    tone=result["tone"],
                    note="Measured in standard deviations of this stock's own recent history. "
                         "Zero is its normal; positive means conditions are stronger than usual. "
                         "A description of the present, not a forecast.",
                    right=f"{result['final']:+.2f}",
                    right_sub="std deviations",
                )

                fcol1, fcol2, fcol3, fcol4 = st.columns(4)
                fcol1.metric(
                    "Technical", f"{latest_tech:+.2f}" if latest_tech is not None else "N/A",
                    help="Blend of trend, momentum, band position and volume flow, in standard deviations.",
                )
                fcol2.metric(
                    "Sentiment", f"{sentiment_sigma:+.2f}" if sentiment_sigma is not None else "No headlines",
                    help="Recency-weighted headline tone, shrunk toward zero when few headlines exist.",
                )
                fcol3.metric(
                    "Macro haircut", f"−{latest_penalty * 100:.0f}%",
                    help="How much the market's current nervousness reduces confidence in the reading. "
                         "Applied to magnitude, so it reduces conviction in both directions.",
                )
                fcol4.metric(
                    "Before haircut", f"{result['raw']:+.2f}",
                    help="The blended technical and sentiment score, before the macro haircut.",
                )

                if sentiment_sigma is None:
                    st.caption(
                        "No headlines available, so the sentiment component is dropped and the "
                        "technical weight is renormalised to 100% — rather than multiplying the "
                        "score by 0.55 and quietly pulling it toward neutral."
                    )
                else:
                    _used = result["weights_used"]
                    st.caption(
                        f"Weights actually used: technical {_used.get('technical', 0) * 100:.0f}%, "
                        f"sentiment {_used.get('sentiment', 0) * 100:.0f}% "
                        f"(from {sentiment_info['n_items']} headlines, "
                        f"newest {sentiment_info['newest_age_hours']:.0f}h old)."
                    )

                if beta_info:
                    st.caption(
                        f"Volatility sensitivity estimated from this stock's own history: a 1% rise in "
                        f"VIX moved it {beta_info['slope'] * 100:+.2f}% on average "
                        f"(R² {beta_info['r_squared']:.3f}, n={beta_info['n_obs']}), which sets the "
                        f"maximum haircut to {fs_cfg.geo_beta * 100:.0f}%."
                    )

                # --- Score history ------------------------------------------
                st.subheader("Score history")
                explain(
                    "The same score computed for every day in the past year, so you can see whether "
                    "today's reading is unusual for this stock or simply where it normally sits."
                )
                composite_series = (tech_series * (1 - penalty_series)).dropna()
                if not composite_series.empty:
                    hist_fig = go.Figure()
                    hist_fig.add_trace(go.Scatter(
                        x=composite_series.index, y=composite_series,
                        mode="lines", name="Composite",
                        line=dict(width=1.8, color=CHART_GOLD, shape="spline", smoothing=0.35),
                    ))
                    for level, colour in ((fs_cfg.strong_threshold, CHART_JADE),
                                          (-fs_cfg.strong_threshold, CHART_ROSE)):
                        hist_fig.add_hline(y=level, line=dict(color=colour, width=1, dash="dot"),
                                           opacity=0.5)
                    hist_fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.18)", width=1))
                    st.plotly_chart(style_chart(hist_fig, height=300, show_legend=False),
                                    use_container_width=True)

                    freq = scoring.threshold_frequency(composite_series, fs_cfg)
                    if freq:
                        st.caption(
                            f"Over the last {freq['n_days']} trading days this stock read strongly "
                            f"positive {freq['strong_positive_pct']:.0f}% of the time, strongly negative "
                            f"{freq['strong_negative_pct']:.0f}%, and sat in the middle "
                            f"{freq['neutral_pct']:.0f}%. Realised standard deviation "
                            f"{freq['realised_sd']:.2f} — close to 1.0 means the scale is behaving."
                        )

                # --- Component breakdown -------------------------------------
                st.subheader("What is driving it")
                comp_rows = []
                for key, label in scoring.COMPONENT_LABELS.items():
                    zcol = f"z_{key}"
                    if zcol in indicator_frame.columns and pd.notna(indicator_frame[zcol].iloc[-1]):
                        comp_rows.append((label, float(indicator_frame[zcol].iloc[-1]),
                                          fs_cfg.tech_subweights[key]))
                if comp_rows:
                    render_score_bars([
                        (f"{label} · {weight * 100:.0f}% weight", 50 + 50 * max(-2, min(2, z)) / 2)
                        for label, z, weight in comp_rows
                    ])
                    st.caption(
                        "Bars are centred: the midpoint is this stock's own normal, full right is two "
                        "standard deviations above it. Exact values: "
                        + " · ".join(f"{label.split(' (')[0]} {z:+.2f}" for label, z, _ in comp_rows)
                    )

                corr = scoring.component_correlations(indicator_frame, fs_cfg)
                if not corr.empty:
                    with st.expander("Are these four measurements independent? (they are not)"):
                        st.dataframe(corr.round(2), use_container_width=True)
                        _mom = "Momentum (RSI)"
                        _band = "Band position (stretch)"
                        if _mom in corr.index and _band in corr.columns:
                            st.caption(
                                f"Momentum and band position correlate {corr.loc[_mom, _band]:.2f}. "
                                "They largely measure the same thing — how stretched the price is — so "
                                "their nominal 35% and 10% weights are not the effective ones. This is "
                                "shown rather than hidden because it is the main weakness of any "
                                "hand-weighted technical composite."
                            )

                # --- Backtest -------------------------------------------------
                st.subheader("Does this score actually predict anything?")
                explain(
                    "This is the honest check. It asks whether the score had any relationship with "
                    "what the price did next, using statistics that survive the fact that overlapping "
                    "windows and a slow-moving score break the textbook ones."
                )
                with st.spinner("Backtesting..."):
                    bt = scoring.backtest(composite_series, daily_df["Close"], horizon_bt, fs_cfg)

                if "error" in bt:
                    st.info(bt["error"])
                else:
                    bcol1, bcol2, bcol3 = st.columns(3)
                    bcol1.metric(
                        "Bootstrap p-value",
                        f"{bt['p_bootstrap']:.3f}" if np.isfinite(bt["p_bootstrap"]) else "N/A",
                        help="Probability of seeing a relationship this strong if there were really "
                             "none. Computed by block bootstrap, which survives overlapping windows.",
                    )
                    bcol2.metric(
                        "R²", f"{bt['r_squared']:.3f}",
                        help="Share of the next period's move explained by the score. Near zero is "
                             "the normal result.",
                    )
                    bcol3.metric(
                        "Independent windows", f"{bt['n_independent']}",
                        help="Non-overlapping periods — the real amount of evidence, far smaller "
                             "than the raw row count.",
                    )
                    st.write(scoring.interpret_backtest(bt, fs_cfg))

                    with st.expander("Full backtest detail, including the p-value you should ignore"):
                        st.markdown(f"""
| Statistic | Value | |
| --- | --- | --- |
| Observations (overlapping) | {bt['n_obs']} | inflated — each shares {horizon_bt - 1} days with the next |
| Independent windows | {bt['n_independent']} | the honest sample size |
| Slope | {bt['slope']:+.5f} | return per 1σ of score |
| Correlation | {bt['r_value']:+.3f} | |
| R² | {bt['r_squared']:.4f} | |
| **Bootstrap p-value** | **{bt['p_bootstrap']:.4f}** | **use this one** |
| Non-overlapping p-value | {bt['p_independent']:.4f} | agrees with the bootstrap |
| Naive p-value | {bt['p_naive']:.4f} | *invalid here — see below* |
| Directional hit rate | {bt['hit_rate_pct']:.1f}% | 50% is the coin-flip baseline |
| Top-quartile mean return | {bt['top_quartile_mean_pct']:+.2f}% | |
| Bottom-quartile mean return | {bt['bottom_quartile_mean_pct']:+.2f}% | |
| Spread | {bt['spread_pct']:+.2f}% | |

**Why the naive p-value is listed but not used.** Ordinary regression assumes independent
observations. Here the forward return on consecutive days shares {horizon_bt - 1} of its
{horizon_bt} days, and the score is a 126-day rolling statistic with autocorrelation near 0.99.
Tested against simulated data containing *no* relationship at all, the naive p-value falls below
0.05 roughly **half** the time, versus about 5% for the bootstrap. If the two numbers above differ
a lot, that gap is the size of the error you would have made by trusting the familiar one.
""")

                st.warning(
                    "This score describes conditions that already exist. It is not a forecast, not "
                    "personalised advice, and not a validated strategy. A single stock over a single "
                    "period is one test, not evidence — and the backtest above is included so you can "
                    "see that for yourself rather than take it on trust."
                )
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
        st.info("Enter a ticker in the command bar at the top of the page and select **Run analysis** to begin.")
    with tab_fundamentals:
        st.info("Enter a ticker in the command bar at the top of the page and select **Run analysis** to begin.")
    with tab_factors:
        st.info("Enter a ticker in the command bar at the top of the page and select **Run analysis** to begin.")


with tab_watchlist:
    st.divider()
    st.header("Watchlist scan")
    explain(
        "This runs the same indicator count and headline check across every ticker you list, then ranks "
        "them side by side. It is a way to decide what deserves a closer look first — a ranking of "
        "well-known signals, not a verdict on which stocks are good."
    )
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

                # --- Ranked by the same composite the Factor Score tab computes ---
                st.subheader("Ranked by composite score")
                st.caption(
                    "The same technical-plus-sentiment composite the Factor Score tab computes, with "
                    "the same macro haircut, so the two views cannot disagree about a ticker. Scores "
                    "are in standard deviations of each stock's OWN history — which makes this a "
                    "ranking of how unusual each stock looks against itself, not a comparison of the "
                    "stocks against each other."
                )
                if "Composite score" in results_df.columns and results_df["Composite score"].notna().any():
                    factor_sorted_df = results_df.dropna(subset=["Composite score"]).sort_values(
                        "Composite score", ascending=False
                    ).reset_index(drop=True)
                    st.dataframe(
                        factor_sorted_df[["Ticker", "Last price", "Sector", "Composite score", "Reading", "Indicator lean"]],
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.write("Not enough price history to score the tickers in this watchlist.")

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


with tab_analysis:
    # AUTO-REFRESH — strictly opt-in.
    #
    # This block used to render unconditionally, with the checkbox only
    # controlling the polling interval. So a user who had never asked for a
    # live quote still got a "Live price ticker" panel wedged between the
    # command bar and their results, and when the fetch failed it printed the
    # raw exception — libcurl error text, to an audience that came for a
    # stock chart. Now the panel exists only once you switch it on, and it
    # sits with the price data rather than interrupting the path to it.
    st.divider()
    live_mode = st.checkbox(
        "Auto-refresh this price",
        key="live_mode_toggle",
        help="Polls Yahoo on a timer instead of only refreshing when you click a button. Still the "
             "same delayed Yahoo data as the rest of the app, just checked more often. More frequent "
             "polling means more requests, which increases the chance of hitting Yahoo's rate limit.",
    )
    live_interval = st.select_slider(
        "Refresh every", options=[15, 30, 60], value=30, key="live_interval_slider",
        format_func=lambda s: f"{s}s",
    ) if live_mode else None

    if live_mode:
        @st.fragment(run_every=live_interval)
        def render_live_ticker():
            if not current_ticker:
                st.caption("Run an analysis first — auto-refresh follows the loaded instrument.")
                return
            try:
                quote = get_live_quote(current_ticker)
                if quote["price"] is None:
                    st.caption(f"No live quote available for '{current_ticker}' right now.")
                    return
                delta_str = None
                if quote["change"] is not None and quote["change_pct"] is not None:
                    delta_str = f"{quote['change']:+.2f} ({quote['change_pct']:+.2f}%)"
                st.metric(current_ticker, money(quote["price"], quote["currency"]), delta=delta_str)
                st.caption(
                    f"Market state: {quote['market_state'] or 'unknown'} · "
                    f"Last checked {datetime.now().strftime('%H:%M:%S')} · "
                    "Still Yahoo-delayed data — 'live' means auto-refreshing, not true real-time."
                )
            except Exception as exc:
                # Never surface the raw exception. It is libcurl/urllib internals
                # and tells the reader nothing they can act on.
                if _is_rate_limit_error(exc):
                    st.caption("Rate-limited by Yahoo — will try again on the next refresh.")
                else:
                    st.caption(
                        f"Couldn't refresh just now — trying again in {live_interval}s. "
                        "The figures above are from the last successful load."
                    )

        render_live_ticker()


# General market headlines, folded in beneath the ticker-specific news
# above rather than occupying a top-level tab of their own.
with tab_analysis:
    with st.expander("Wider market news (not specific to this ticker)", expanded=False):
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
                # Falls back to plain bold text when the feed gave us a URL
                # that isn't an ordinary http(s) link.
                _t = markdown_safe(item["title"])
                st.markdown(f"{badge} **[{_t}]({item['link']})**" if item["link"] else f"{badge} **{_t}**")
                if item["description"]:
                    st.caption(item["description"])


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

# ----------------------------------------------------------------------
# STATUS BAR — the readout every real terminal keeps pinned along the
# bottom edge. It reports only things that are actually true: the data
# source and its delay, the ticker currently loaded, whether explain mode
# is on, and the time this page rendered.
#
# It's fixed to the viewport on desktop and static on narrow screens,
# where a permanently pinned bar would eat scarce vertical space and sit
# on top of the content it's meant to annotate.
# ----------------------------------------------------------------------
# "LOADED —" reads like a value that failed to render rather than an empty
# state, so the null case says so in words.
_status_ticker = st.session_state.get("last_ticker") or ""
st.markdown(
    f"""
<div class="tv-statusbar">
  <span class="tv-sb-item"><span class="tv-sb-dot"></span>Yahoo Finance · delayed</span>
  <span class="tv-sb-sep"></span>
  <span class="tv-sb-item">{f"Loaded <b>{html_lib.escape(_status_ticker)}</b>" if _status_ticker else "No instrument loaded"}</span>
  <span class="tv-sb-sep"></span>
  <span class="tv-sb-item">Explain {'ON' if st.session_state.get('explain_mode', True) else 'OFF'}</span>
  <span class="tv-sb-spacer"></span>
  <span class="tv-sb-item tv-sb-time">{datetime.now().strftime('%H:%M:%S')}</span>
</div>
""",
    unsafe_allow_html=True,
)
