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
import bcrypt
import pyotp
import qrcode
import html as html_lib
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
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
# reliable if you run BullBear on your own computer. On Streamlit
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


st.set_page_config(page_title="BullBear", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

users = load_users()

# Only show the login/register screen if not already authenticated —
# avoids flashing it after a successful login on rerun.
if not st.session_state.get("authenticated"):
    st.title("BullBear")
    st.caption("Sign in to your account, or create a new one below.")

    login_tab, register_tab = st.tabs(["Log in", "Create account"])

    with login_tab:
        with st.form("login_form"):
            login_username = st.text_input("Username")
            login_password = st.text_input("Password", type="password")
            login_submitted = st.form_submit_button("Log in")

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
            reg_submitted = st.form_submit_button("Create account")

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
                st.success("Account created — go to the 'Log in' tab to sign in.")

    st.stop()

# --- Password auth succeeded. Now handle optional 2FA. ---
username = st.session_state["username"]
user_record = users[username]
totp_enabled = user_record.get("totp_enabled", False)

if totp_enabled and not st.session_state.get(f"totp_verified_{username}", False):
    st.title("BullBear")
    st.subheader("Two-factor verification")
    code = st.text_input("Enter the 6-digit code from your authenticator app", key="totp_code_input")
    if st.button("Verify code"):
        totp = pyotp.TOTP(user_record["totp_secret"])
        if totp.verify(code):
            st.session_state[f"totp_verified_{username}"] = True
            st.rerun()
        else:
            st.error("Incorrect code — try again.")
    st.stop()

# ----------------------------------------------------------------------
# 1b. VISUAL STYLE — elegant private-bank look: deep charcoal-navy, muted
#     gold accent, serif headers + monospace numbers (professional data
#     feel without the harsh black/neon-orange contrast). Includes a
#     mobile breakpoint so headers/metrics don't overflow small screens.
# ----------------------------------------------------------------------
# The color/theme basics also live in .streamlit/config.toml (the
# officially supported way to theme Streamlit). This block layers on
# fonts and panel styling that config.toml can't control on its own.
# Note: Streamlit's internal HTML structure can change between versions —
# if a future Streamlit update changes these class names, this styling
# may need small selector updates, but the app will still work either way.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@0,500;0,600;1,500&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}
h1, h2, h3 {
    font-family: 'Fraunces', serif !important;
    color: #E7DFC6 !important;
    letter-spacing: 0.3px;
    font-weight: 600;
}
h1 {
    border-bottom: 1px solid #C9A96166;
    padding-bottom: 0.4rem;
    font-size: 1.7rem !important;
}
h2, h3 {
    border-bottom: 1px solid #2A2E38;
    padding-bottom: 0.25rem;
    font-size: 1.1rem !important;
}
/* Metric numbers: soft gold, monospace, tabular — professional data feel */
[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace !important;
    color: #C9A961 !important;
    font-weight: 600;
}
[data-testid="stMetricLabel"] {
    font-family: 'Inter', sans-serif !important;
    color: #9198A6 !important;
    text-transform: uppercase;
    font-size: 0.72rem !important;
    letter-spacing: 0.4px;
}
/* Panels/cards: soft rounded corners, gentle border — elegant not stark */
[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #262B36 !important;
    border-radius: 8px !important;
    background-color: #191D26 !important;
}
/* Sidebar: slightly darker, soft gold divider */
[data-testid="stSidebar"] {
    background-color: #12151C !important;
    border-right: 1px solid #C9A96133;
}
/* Buttons: soft gold outline, rounded */
.stButton > button {
    font-family: 'Inter', sans-serif !important;
    border: 1px solid #C9A961 !important;
    border-radius: 6px !important;
    color: #C9A961 !important;
    background-color: transparent !important;
}
.stButton > button:hover {
    background-color: #C9A961 !important;
    color: #14171F !important;
}
[data-testid="stCaptionContainer"] {
    color: #8A90A0 !important;
}

/* --- Mobile: shrink headers/metrics so they don't overflow a phone screen --- */
@media (max-width: 640px) {
    h1 { font-size: 1.25rem !important; letter-spacing: 0.1px; }
    h2, h3 { font-size: 0.95rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.1rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.65rem !important; }
}
</style>
""", unsafe_allow_html=True)

st.title("BullBear")
st.caption(
    "Educational tool only. Shows public price data, indicators, news, and "
    "a statistical price range based on past volatility. Nothing here is "
    "a prediction or a buy/sell recommendation."
)


with st.expander("📖 New to these terms? Quick glossary"):
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
st.sidebar.write(f"👤 **{st.session_state.get('name')}**")
if st.sidebar.button("Log out"):
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


tab_analysis, tab_fundamentals, tab_tradesetup, tab_journal, tab_multiasset, tab_calendar, tab_watchlist, tab_news, tab_settings = st.tabs(
    ["📈 Analysis", "📊 Fundamentals & Risk", "🎯 Trade Setup", "📓 Trade Journal", "🌐 Multi-Asset", "🗓️ Calendar", "📋 Watchlist", "🗞️ Market News", "⚙️ Settings"]
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
    market_choice = st.selectbox(
        "Market",
        options=list(MARKET_SUFFIX_MAP.keys()),
        help="Pick a market and just type the base ticker/code — the right suffix is added automatically. E.g. Hong Kong: '0700' becomes '0700.HK'.",
    )
    raw_ticker_input = st.text_input("Stock ticker or code (e.g. AAPL, 0700, 005930, D05)", "AAPL").upper().strip()
    _suffix = MARKET_SUFFIX_MAP[market_choice]
    if _suffix and "." not in raw_ticker_input:
        ticker = raw_ticker_input + _suffix
    else:
        ticker = raw_ticker_input

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

    period_choice = st.selectbox("Chart timeframe", options=CHART_OPTIONS, index=4)  # default "6mo"
    horizon_days = st.slider("Price range horizon (trading days ahead)", 1, 30, 5)
    run_button = st.button("Run analysis", type="primary")

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
    st.markdown("#### 🔴 Live price ticker")
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
        if st.button("📨 Send last tilt to Telegram"):
            if "last_lean" not in st.session_state:
                st.warning("Run an analysis first (click 'Run analysis' above) — then this button sends that result.")
            elif not telegram_bot_token or not telegram_chat_id:
                st.error("Enter both a bot token and chat ID above first.")
            else:
                message = (
                    f"BullBear update for {st.session_state['last_ticker']}: {st.session_state['last_lean']}. "
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
            uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="BullBear")
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
        "post_price": info.get("postMarketPrice"),
        "post_change": info.get("postMarketChange"),
        "pre_price": info.get("preMarketPrice"),
        "pre_change": info.get("preMarketChange"),
        "currency": info.get("currency") or "USD",
    }


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
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "dividend_yield": info.get("dividendYield"),
        "trailing_eps": info.get("trailingEps"),
        "beta": info.get("beta"),
        "target_mean_price": info.get("targetMeanPrice"),
        "target_high_price": info.get("targetHighPrice"),
        "target_low_price": info.get("targetLowPrice"),
        "recommendation_key": info.get("recommendationKey"),
        "num_analyst_opinions": info.get("numberOfAnalystOpinions"),
        "currency": info.get("currency") or "USD",
    }


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
# TRADE STRUCTURE CALCULATOR — uses ATR (Average True Range), a
# well-documented, publicly-known volatility measure that traders and
# institutions commonly reference for stop placement and position sizing.
# This is NOT a proprietary hedge fund algorithm (those aren't public —
# any tool claiming to replicate one would just be making something up).
# It does NOT predict whether a trade will be profitable. It only helps
# structure risk levels consistently BEFORE entering a hypothetical
# trade, using math you can verify yourself.
# ----------------------------------------------------------------------
def compute_atr(daily_df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range: a standard volatility measure — the average of
    the last N days' 'true range' (the largest of: high-low, high-prev
    close, low-prev close). Higher ATR means the stock typically swings
    more per day in absolute price terms.
    """
    high = daily_df["High"]
    low = daily_df["Low"]
    prev_close = daily_df["Close"].shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def compute_trade_structure(daily_df: pd.DataFrame, atr_multiplier: float,
                             risk_reward_ratio: float, direction: str) -> dict:
    """
    direction: 'long' or 'short'.
    Entry zone: a small band around the current price (not a precise
    point — real fills happen across a range).
    Stop loss: current price minus/plus (ATR × multiplier) — a standard
    volatility-adjusted stop distance. Bigger multiplier = wider stop =
    fewer premature stop-outs, but more risk per share if wrong.
    Take profit: set from YOUR chosen risk:reward ratio, applied to the
    entry-to-stop distance. There's no universally 'correct' ratio.
    """
    atr_series = compute_atr(daily_df)
    latest_atr = atr_series.iloc[-1]
    last_price = daily_df["Close"].iloc[-1]

    if pd.isna(latest_atr):
        return None

    if direction == "long":
        entry_low = last_price - 0.5 * latest_atr
        entry_high = last_price + 0.25 * latest_atr
        stop_loss = last_price - atr_multiplier * latest_atr
        risk_per_share = last_price - stop_loss
        take_profit = last_price + risk_per_share * risk_reward_ratio
    else:  # short
        entry_low = last_price - 0.25 * latest_atr
        entry_high = last_price + 0.5 * latest_atr
        stop_loss = last_price + atr_multiplier * latest_atr
        risk_per_share = stop_loss - last_price
        take_profit = last_price - risk_per_share * risk_reward_ratio

    return {
        "last_price": last_price,
        "atr": latest_atr,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_per_share": risk_per_share,
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
def historical_signal_check(daily_df: pd.DataFrame, forward_days: int = 5):
    """
    Retrospectively checks whether the Indicator Lean's underlying score
    has lined up with this ONE stock's own subsequent price moves over
    its own past year. This is a single-asset, single-window check with
    no transaction costs and no out-of-sample data — a clean-looking
    correlation here is easy to get from noise alone and does NOT mean
    the same pattern will hold going forward.
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

    if len(scores) < 20:
        return None

    return float(np.corrcoef(scores, forward_returns)[0, 1])


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
# 14. WATCHLIST SCAN — ranks multiple tickers by the same transparent
#     heuristics used above (indicator lean + headline tone). This is a
#     ranked scan of well-known signals, not a "best stocks" verdict.
# ----------------------------------------------------------------------
def scan_watchlist(tickers: list[str]) -> pd.DataFrame:
    rows = []
    progress = st.progress(0.0, text="Starting scan...")

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

            sector = get_fundamentals(t).get("sector") or "Unknown"

            rows.append({
                "Ticker": t,
                "Last price": round(float(latest["Close"]), 2),
                "Sector": sector,
                "Indicator lean": lean,
                "Headlines +/-": f"{n_pos}/{n_neg}",
                "Combined tilt score": combined_score,
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
    st.subheader("Trade structure calculator")
    st.warning(
        "⚠️ This is a risk-management calculator using ATR (Average True Range), a "
        "standard, publicly-documented volatility measure — NOT a proprietary hedge "
        "fund algorithm (those aren't public). It does not predict whether a trade "
        "will be profitable, does not tell you whether to enter a trade at all, and "
        "the multiplier/ratio you pick below are your own choices, not 'correct' "
        "universal answers. Use it only to structure risk consistently if you've "
        "already decided you want exposure to this stock."
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

    ts_col3, ts_col4 = st.columns(2)
    with ts_col3:
        atr_multiplier = st.slider(
            "Stop-loss distance (× ATR)", 1.0, 4.0, 2.0, step=0.25,
            help="How many multiples of the stock's average daily range to place the stop. Wider (higher number) = fewer premature stop-outs but more risk per share if wrong. 1.5-2.5x is a commonly referenced range, not a rule.",
        )
    with ts_col4:
        risk_reward_ratio = st.slider(
            "Target risk:reward ratio", 1.0, 5.0, 2.0, step=0.5,
            help="Take-profit distance as a multiple of your risk (entry-to-stop distance). E.g. 2.0 means targeting twice the profit of what you're risking.",
        )

    if st.button("Calculate trade structure"):
        try:
            with st.spinner(f"Loading {ts_ticker} data..."):
                ts_daily_df = load_daily_data(ts_ticker)

            structure = compute_trade_structure(ts_daily_df, atr_multiplier, risk_reward_ratio, direction)

            if structure is None:
                st.error("Not enough price history to compute ATR for this ticker yet.")
            else:
                ts_currency = get_fundamentals(ts_ticker).get("currency", "USD")

                st.markdown(f"### {ts_ticker} — {ts_direction}")
                scol1, scol2 = st.columns(2)
                scol1.metric("Current price", money(structure["last_price"], ts_currency))
                scol2.metric("ATR (14-day)", money(structure["atr"], ts_currency),
                             help="Average daily price range over the last 14 days — the volatility measure driving the levels below.")

                # Delta shows each level's distance from the current price (with
                # direction) so it's unambiguous which side of the price it sits
                # on — without this, a short's stop-loss (above price) reading as
                # a bigger number than its take-profit (below price) looks like a
                # bug even though it's correct for that direction.
                stop_delta_pct = (structure["stop_loss"] - structure["last_price"]) / structure["last_price"] * 100
                tp_delta_pct = (structure["take_profit"] - structure["last_price"]) / structure["last_price"] * 100

                scol3, scol4, scol5 = st.columns(3)
                scol3.metric("Entry zone", f"{money(min(structure['entry_low'], structure['entry_high']), ts_currency)} – {money(max(structure['entry_low'], structure['entry_high']), ts_currency)}",
                             help="A band around the current price, not an exact point — real fills happen across a range.")
                scol4.metric("Stop loss", money(structure["stop_loss"], ts_currency),
                             delta=f"{stop_delta_pct:+.1f}% vs current price", delta_color="off",
                             help=f"Current price {'minus' if direction == 'long' else 'plus'} {atr_multiplier}× ATR — {'below' if direction == 'long' else 'above'} the current price for a {direction} trade.")
                scol5.metric("Take profit", money(structure["take_profit"], ts_currency),
                             delta=f"{tp_delta_pct:+.1f}% vs current price", delta_color="off",
                             help=f"Set from your chosen {risk_reward_ratio}:1 risk-reward ratio — {'above' if direction == 'long' else 'below'} the current price for a {direction} trade, not a prediction of where price will go.")

                if direction == "short":
                    st.caption(
                        f"↳ This is a **short**, so the stop loss ({money(structure['stop_loss'], ts_currency)}) sits "
                        f"*above* the current price and the take profit ({money(structure['take_profit'], ts_currency)}) sits "
                        "*below* it — the take-profit number being smaller than the stop-loss number is expected here, "
                        "not a bug. It'd be the other way around for a long."
                    )

                st.caption(
                    f"Risk per share: {money(structure['risk_per_share'], ts_currency)}. "
                    f"Reward per share (if target hit): {money(structure['risk_per_share'] * risk_reward_ratio, ts_currency)}."
                )
                st.info(
                    "Remember: hitting your stop loss is a normal, expected part of using any "
                    "risk framework, not a sign the framework failed. No stop distance or "
                    "risk-reward ratio can guarantee the trade works out — they only define "
                    "what you're risking and targeting *if* it doesn't."
                )

                if st.button("📓 Log this setup to Trade Journal"):
                    st.session_state["journal_prefill"] = {
                        "ticker": ts_ticker,
                        "direction": direction,
                        "entry_price": round(structure["last_price"], 4),
                        "stop_loss": round(structure["stop_loss"], 4),
                        "take_profit": round(structure["take_profit"], 4),
                    }
                    st.success("Staged — open the 📓 Trade Journal tab to finish adding it (shares, date, notes).")
        except Exception as e:
            if _is_rate_limit_error(e):
                st.error("⏳ Yahoo Finance is rate-limiting requests right now — try again in a minute.")
            else:
                st.error(f"Something went wrong: {e}")


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

    with st.expander("➕ Add a trade", expanded=bool(prefill)):
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
            with st.container(border=True):
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
        avg_r = np.mean([r["R-multiple"] for r in closed_rows if r["R-multiple"] is not None]) if closed_rows else None
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
            fig.add_trace(go.Candlestick(
                x=ma_daily_df.index,
                open=ma_daily_df["Open"], high=ma_daily_df["High"],
                low=ma_daily_df["Low"], close=ma_daily_df["Close"],
                name=ma_ticker,
            ))
            fig.add_trace(go.Scatter(x=ma_daily_df.index, y=ma_daily_df["SMA20"], name="20-day avg", line=dict(width=1.5)))
            fig.add_trace(go.Scatter(x=ma_daily_df.index, y=ma_daily_df["SMA50"], name="50-day avg", line=dict(width=1.5)))
            fig.update_layout(template="plotly_dark", height=450, margin=dict(l=20, r=20, t=20, b=20),
                               xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

            ma_lean, ma_reasons, _ma_score = compute_indicator_lean(ma_latest)
            st.metric("Indicator lean", ma_lean,
                      help="Same transparent indicator count used for stocks — not a validated strategy, and less battle-tested for non-equity assets like these.")
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
            "Official source": "federalreserve.gov/monetarypolicy/fomccalendars.htm",
        },
        {
            "Event": "US Non-Farm Payrolls (jobs report)",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Typically 1st Friday of the month (occasionally 2nd)",
            "Official source": "bls.gov/schedule/news_release/empsit.htm",
        },
        {
            "Event": "US CPI (inflation)",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Typically 2nd week of the month — exact day varies",
            "Official source": "bls.gov/schedule/news_release/cpi.htm",
        },
        {
            "Event": "US GDP (advance estimate)",
            "Frequency": "Quarterly",
            "Remaining 2026 dates": "Late Oct 2026 (Q3), late Jan 2027 (Q4)",
            "Official source": "bea.gov/news/schedule",
        },
        {
            "Event": "US Retail Sales",
            "Frequency": "Monthly",
            "Remaining 2026 dates": "Mid-month — exact day varies",
            "Official source": "census.gov/retail/marts/www/marts_current.pdf",
        },
        {
            "Event": "FOMC meeting minutes",
            "Frequency": "8×/year, ~3 weeks after each meeting",
            "Remaining 2026 dates": "~3 weeks after each FOMC date above",
            "Official source": "federalreserve.gov/monetarypolicy/fomccalendars.htm",
        },
    ])
    st.dataframe(MACRO_EVENTS, hide_index=True, use_container_width=True)
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

        latest_chart = chart_df.iloc[-1]
        latest_daily = daily_df.iloc[-1]
        currency = rt["currency"]

        # Prefer the real-time regular price if Yahoo has it; otherwise
        # fall back to the last bar we already downloaded.
        display_price = rt["regular_price"] if rt["regular_price"] else latest_chart["Close"]

        with tab_analysis:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Last price", money(display_price, currency),
                        help="The most recent regular-session price where available, otherwise the last downloaded bar.")
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
                    f"🌙 After-hours: {money(rt['post_price'], currency)} "
                    f"({'+' if (rt['post_change'] or 0) >= 0 else ''}{rt['post_change']:.2f})"
                )
            elif rt["market_state"] == "PRE" and rt["pre_price"]:
                st.caption(
                    f"🌅 Pre-market: {money(rt['pre_price'], currency)} "
                    f"({'+' if (rt['pre_change'] or 0) >= 0 else ''}{rt['pre_change']:.2f})"
                )

            # --- Price chart (candlestick, matching real trading-platform style) ---
            st.subheader(f"{ticker} price chart ({period_choice})")
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=chart_df.index,
                open=chart_df["Open"], high=chart_df["High"],
                low=chart_df["Low"], close=chart_df["Close"],
                name="Price",
            ))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["SMA20"], name=f"20-{unit_label} avg", line=dict(width=1.5)))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["SMA50"], name=f"50-{unit_label} avg", line=dict(width=1.5)))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["BB_upper"], name="Upper band",
                                      line=dict(dash="dot", width=1), opacity=0.4))
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["BB_lower"], name="Lower band",
                                      line=dict(dash="dot", width=1), opacity=0.4))
            fig.update_layout(template="plotly_dark", height=500, margin=dict(l=20, r=20, t=20, b=20),
                               xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

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
            st.metric("Current tilt (based on daily data)", lean,
                      help="A simple count of how many well-known indicators (moving averages, MACD, RSI) point bullish vs. bearish right now. A transparency tool, not a validated strategy.")
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
                for item in news_items:
                    with st.container(border=True):
                        sentiment = tag_sentiment(item["title"] + " " + item.get("description", ""))
                        badge = {"positive": "🟢 positive tone", "negative": "🔴 negative tone", "neutral": "⚪ neutral tone"}[sentiment]
                        st.markdown(f"**[{item['title']}]({item['link']})** — {badge}")
                        if item["published"]:
                            st.caption(item["published"])
                        if item["description"]:
                            st.caption(item["description"])

            # --- Narrative connecting technicals + news (free, rule-based) ---
            st.subheader("What's driving the current picture")
            st.write(generate_narrative(ticker, lean, reasons, news_items))
            if st.toggle("🧒 Explain like I'm 5"):
                st.info(explain_like_im_5(ticker, lean, news_items))


        with tab_fundamentals:
            st.caption("Fundamentals, analyst estimates, earnings, and risk metrics for the ticker selected in the Analysis tab.")

            # --- Fundamentals + Analyst view + Earnings/Dividends + Risk profile ---
            with st.spinner("Loading fundamentals..."):
                fundamentals = get_fundamentals(ticker)
                ed_data = get_earnings_and_dividends(ticker)
                risk = compute_risk_metrics(daily_df)

            st.subheader("Fundamentals")
            fcol1, fcol2, fcol3, fcol4 = st.columns(4)
            fcol1.metric("Sector", fundamentals["sector"] or "N/A")
            fcol2.metric("Market cap", format_market_cap(fundamentals["market_cap"], currency))
            fcol3.metric("P/E (trailing)", f"{fundamentals['trailing_pe']:.1f}" if fundamentals["trailing_pe"] else "N/A",
                         help="Price divided by trailing 12-month earnings per share. Higher generally means the market is pricing in more future growth (or the stock is more expensive relative to current profit) — context-dependent, not good/bad on its own.")
            div_yield = fundamentals["dividend_yield"]
            # yfinance has changed whether this field is a fraction (0.02) or
            # already a percent (2.0) across versions — this handles either.
            div_yield_pct = div_yield * 100 if (div_yield and div_yield < 1) else div_yield
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
                    st.markdown(f"**Target range**  \n{target_range_text}")
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

            # --- Historical signal check (fixed backtest, honestly caveated) ---
            st.subheader("Historical signal check")
            corr = historical_signal_check(daily_df)
            if corr is not None:
                st.metric("Correlation: signal vs. 5-day forward return (past year)", f"{corr:.2f}")
            else:
                st.write("Not enough history to compute this check.")
            st.warning(
                "This checks whether the Indicator Lean's score has lined up with "
                "*this one stock's own* subsequent price moves, over its own past "
                "year only. A clean-looking number here is easy to get from noise "
                "alone with a single asset and window — it is not validation of a "
                "trading strategy, and does not mean the same pattern continues."
            )
            if telegram_enabled:
                st.caption("💡 Use the '📨 Send last tilt to Telegram' button in the Settings tab to send this result.")

            st.caption(f"Data as of {datetime.now().strftime('%Y-%m-%d %H:%M')}. Not financial advice.")

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
        st.write("👈 Enter a ticker above and click **Run analysis** to get started.")
    with tab_fundamentals:
        st.write("👈 Enter a ticker above and click **Run analysis** to get started.")


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
        st.write("👈 Edit the watchlist above and click **Scan watchlist** to compare tickers.")
