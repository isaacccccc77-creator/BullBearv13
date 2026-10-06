"""
Pre-launch check.

    python preflight.py

Answers one question: if I point a domain at this right now, what breaks?

It is deliberately runnable against a live deployment as well as locally
(`DATABASE_URL=... python preflight.py`), because the configuration that
matters is the one the running container has, not the one on your laptop.

Exit code 0 means nothing is blocking. Exit code 1 means at least one
BLOCKER — something that loses user data or leaves the app insecure.
Warnings do not fail the run; they are the things to fix next.
"""

from __future__ import annotations

import importlib
import os
import sys

BLOCK, WARN, OK = "BLOCKER", "warn", "ok"
results: list[tuple[str, str, str]] = []


def record(level: str, title: str, detail: str = "") -> None:
    results.append((level, title, detail))


def check_storage() -> None:
    """
    The highest-stakes setting there is.

    Without DATABASE_URL the app writes JSON files to the container's
    filesystem. Every platform worth deploying on replaces that
    filesystem on redeploy, so the first bugfix push silently deletes
    every account, watchlist and journal entry. There is no error and
    nothing to restore from.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        record(BLOCK, "No DATABASE_URL — accounts will not survive a redeploy",
               "Set it to a managed Postgres. Neon and Supabase have free tiers "
               "that are enough to launch on.")
        return
    try:
        import storage
        pg = storage.PostgresStorage(dsn)
        pg.ensure_schema()
        pg.user_exists("preflight-probe-nobody")
        record(OK, "Postgres reachable and schema present")
    except Exception as e:
        record(BLOCK, "DATABASE_URL is set but the database did not answer",
               f"{type(e).__name__}: {str(e)[:160]}")
        return

    if "sslmode=" not in dsn and not dsn.startswith("postgresql://localhost"):
        record(WARN, "No sslmode in DATABASE_URL",
               "Append ?sslmode=require so the connection cannot silently "
               "fall back to plaintext across the public internet.")


def check_secrets_not_committed() -> None:
    import subprocess
    try:
        tracked = subprocess.run(
            ["git", "ls-files", ".streamlit/secrets.toml", "user_data",
             "bullbear_users.json"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        record(WARN, "Could not ask git what is tracked"); return
    if tracked:
        record(BLOCK, "Secrets or user data are tracked by git",
               f"Tracked: {tracked.splitlines()}. Remove with "
               "`git rm --cached`, then rotate anything that leaked.")
    else:
        record(OK, "No secrets or user data tracked by git")


def check_auth() -> None:
    providers = []
    try:
        import streamlit as st
        auth = st.secrets.get("auth") if hasattr(st, "secrets") else None
        if auth:
            reserved = {"redirect_uri", "cookie_secret"}
            providers = [k for k in auth.keys() if k not in reserved]
            cookie = str(auth.get("cookie_secret", ""))
            redirect = str(auth.get("redirect_uri", ""))
            if len(cookie) < 32:
                record(BLOCK, "auth.cookie_secret is too short or missing",
                       "Use at least 32 random characters: "
                       "python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
            elif "CHANGE-ME" in cookie or cookie.startswith("test"):
                record(BLOCK, "auth.cookie_secret is still the placeholder")
            else:
                record(OK, "auth.cookie_secret looks like real randomness")
            if redirect.startswith("http://") and "localhost" not in redirect:
                record(BLOCK, "auth.redirect_uri is http:// on a non-local host",
                       "An OAuth redirect over plaintext can be intercepted.")
            elif redirect:
                record(OK, f"OAuth redirect: {redirect}")
    except Exception:
        pass
    if providers:
        record(OK, f"Federated sign-in configured: {', '.join(providers)}")
        try:
            importlib.import_module("authlib")
            record(OK, "Authlib installed (required by st.login)")
        except ImportError:
            record(BLOCK, "An [auth] block is configured but Authlib is missing",
                   "pip install 'Authlib>=1.3.2' — sign-in will fail without it.")
    else:
        record(WARN, "No federated sign-in configured",
               "Optional. Password accounts work without it.")


def check_payments() -> None:
    try:
        import support, payments
    except Exception as e:
        record(WARN, f"Could not import payment modules: {e}"); return

    proxy = support.read_config("SUPPORT_PAYNOW_PROXY")
    if proxy:
        kind = (support.read_config("SUPPORT_PAYNOW_TYPE") or "mobile").lower()
        try:
            payments.paynow_payload(
                proxy,
                proxy_type=payments.PROXY_UEN if kind == "uen" else payments.PROXY_MOBILE,
                merchant_name=support.read_config("SUPPORT_PAYNOW_NAME") or "TICKVEIL")
            record(OK, "PayNow payee is valid and a QR can be built")
        except Exception as e:
            record(BLOCK, "PayNow is configured but the payee is unusable",
                   f"{e} — the QR will not render.")
    else:
        record(WARN, "No PayNow payee set", "Optional; the section hides itself.")

    links = support.configured_links()
    record(OK if links else WARN,
           f"{len(links)} donation link(s) configured"
           + ("" if links else " — the card option will not appear"))


def check_dependencies() -> None:
    required = ["streamlit", "pandas", "numpy", "scipy", "plotly", "bcrypt",
                "pyotp", "qrcode", "yfinance", "requests", "vaderSentiment"]
    missing = [m for m in required if not _importable(m)]
    if missing:
        record(BLOCK, f"Missing dependencies: {', '.join(missing)}",
               "pip install -r requirements.txt")
    else:
        record(OK, f"All {len(required)} runtime dependencies import")
    if not _importable("psycopg"):
        record(WARN, "psycopg not installed — the Postgres backend cannot be used")


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def check_app_integrity() -> None:
    """The static guards, run here too so a deploy cannot skip them."""
    import subprocess
    suites = ["test_ordering", "test_layout", "test_theming", "test_payments",
              "test_identity", "test_security", "test_storage", "test_support"]
    failed = []
    for suite in suites:
        try:
            r = subprocess.run([sys.executable, f"{suite}.py"],
                               capture_output=True, timeout=180)
            if r.returncode != 0:
                failed.append(suite)
        except Exception:
            failed.append(suite)
    if failed:
        record(BLOCK, f"Test suites failing: {', '.join(failed)}",
               "Run them individually to see why.")
    else:
        record(OK, f"{len(suites)} test suites pass")


def check_data_source() -> None:
    record(WARN, "Market data comes from yfinance",
           "An unofficial scraper of Yahoo's internal endpoints: it breaks "
           "without notice, rate-limits under real traffic, and redistributing "
           "it on a public site is outside Yahoo's terms. See DEPLOY.md §5.")


def main() -> int:
    print("\n  TICKVEIL PRE-LAUNCH CHECK")
    print("  " + "-" * 58)
    for check in (check_dependencies, check_storage, check_secrets_not_committed,
                  check_auth, check_payments, check_app_integrity,
                  check_data_source):
        try:
            check()
        except Exception as e:
            record(WARN, f"{check.__name__} could not run", f"{type(e).__name__}: {e}")

    icon = {OK: "  ok   ", WARN: "  warn ", BLOCK: " BLOCK "}
    for level in (BLOCK, WARN, OK):
        rows = [r for r in results if r[0] == level]
        if not rows:
            continue
        print()
        for _, title, detail in rows:
            print(f"{icon[level]} {title}")
            if detail:
                for line in _wrap(detail, 66):
                    print(f"         {line}")

    blockers = sum(1 for r in results if r[0] == BLOCK)
    warnings = sum(1 for r in results if r[0] == WARN)
    print("\n  " + "-" * 58)
    if blockers:
        print(f"  {blockers} blocker(s), {warnings} warning(s). "
              "Not ready to point a domain at.")
        return 1
    print(f"  No blockers. {warnings} warning(s) to look at when you can.")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


if __name__ == "__main__":
    sys.exit(main())
