"""
Persistence for Tickveil — one interface, two backends.

Which backend runs is decided by whether a Postgres URL is configured:

  * DATABASE_URL set  -> Postgres. Survives redeploys. Use this in production.
  * DATABASE_URL unset -> JSON files under user_data/. Zero setup, good for
                          local development.

The split exists because Streamlit Community Cloud's filesystem is ephemeral:
local files are not guaranteed to survive a redeploy. That is tolerable for a
free educational tool and completely unacceptable once someone is paying —
losing a subscriber's trade journal is not a recoverable mistake. Postgres is
the fix, and Supabase and Neon both have free tiers large enough for this.

Nothing here imports Streamlit, so it can be tested without a browser.

Schema
------
users            one row per account, including the billing columns the
                 subscription work will read (plan, plan_expires,
                 stripe_customer_id).
user_documents   one row per (account, document kind). Watchlist, journal,
                 Telegram credentials and digest snapshot are each a JSON
                 blob keyed by the account that owns it. They are opaque to
                 the database — no schema migration is needed to change what
                 a journal entry contains.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

# ----------------------------------------------------------------------
# Paths and validation shared by both backends.
# ----------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data")
LEGACY_USERS_FILE = "bullbear_users.json"

# Usernames are interpolated into filenames by the JSON backend and into
# lookups by both, so the whitelist is enforced here rather than trusted from
# the caller. See test_security.py.
USERNAME_PATTERN = re.compile(r"^(?!\.)[A-Za-z0-9._-]{3,32}$")

# The document kinds a user can own. Anything outside this set is rejected,
# so a caller can never invent a key that escapes the data directory.
DOC_KINDS = {"watchlist", "telegram", "journal", "digest_snapshot"}

# Fields every account carries. Listed once so both backends agree on the
# shape, and so a row written before billing existed still reads back with
# sensible defaults rather than raising KeyError.
USER_DEFAULTS: dict[str, Any] = {
    "name": "",
    "password_hash": "",
    "totp_enabled": False,
    "totp_secret": None,
    "plan": "free",
    "plan_expires": None,
    "stripe_customer_id": None,
}


class StorageError(RuntimeError):
    """Raised when a backend cannot satisfy a request."""


def _validate_username(username: str) -> str:
    if not USERNAME_PATTERN.match(username or ""):
        raise StorageError(f"Invalid username: {username!r}")
    return username


def _validate_kind(kind: str) -> str:
    if kind not in DOC_KINDS:
        raise StorageError(f"Unknown document kind: {kind!r}")
    return kind


def _with_defaults(record: dict | None) -> dict | None:
    """Fills in any field added after this row was written."""
    if record is None:
        return None
    return {**USER_DEFAULTS, **record}


# ----------------------------------------------------------------------
# JSON backend — the original behaviour, unchanged in effect.
# ----------------------------------------------------------------------
class JSONStorage:
    """Files under user_data/, created 0700, each written 0600."""

    backend = "json"

    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self._ensure_dir()
        self._adopt_legacy_file()

    def _adopt_legacy_file(self) -> None:
        """
        One-time move of an accounts file that predates user_data/.

        Only runs for the default data directory, and only when this
        directory has no accounts file of its own. Both conditions matter:
        the fallback used to happen on every read, which meant any
        JSONStorage whose own file was missing would silently adopt whatever
        bullbear_users.json happened to be sitting in the process's working
        directory — including its password hashes. That broke test isolation,
        and in production it was an account-takeover path, since the working
        directory is not necessarily under the operator's control.
        """
        if os.path.realpath(self.data_dir) != os.path.realpath(DATA_DIR):
            return
        own = self._path(LEGACY_USERS_FILE)
        if os.path.exists(own) or not os.path.exists(LEGACY_USERS_FILE):
            return
        legacy = self._read(LEGACY_USERS_FILE, None)
        if isinstance(legacy, dict) and legacy:
            self._write_private(own, legacy)

    def _ensure_dir(self) -> None:
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)

    def _path(self, filename: str) -> str:
        """Resolves inside data_dir and refuses anything that escapes it."""
        self._ensure_dir()
        resolved = os.path.realpath(os.path.join(self.data_dir, filename))
        root = os.path.realpath(self.data_dir)
        if os.path.commonpath([resolved, root]) != root:
            raise StorageError("Refusing to access a path outside the data directory.")
        return resolved

    @staticmethod
    def _write_private(path: str, payload: Any) -> None:
        """
        Writes JSON readable only by the owning OS account.

        The mode is applied at creation rather than chmod-ed afterwards —
        creating world-readable and tightening later leaves a window in which
        another local account can read the file. That matters here because
        these files hold TOTP secrets and Telegram bot tokens.
        """
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)

    def _read(self, path: str, default: Any) -> Any:
        try:
            with open(path, "r") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    # --- accounts ---
    def _all_users(self) -> dict:
        """Reads only this backend's own accounts file. Never the CWD."""
        users = self._read(self._path(LEGACY_USERS_FILE), {})
        return users if isinstance(users, dict) else {}

    def get_user(self, username: str) -> dict | None:
        return _with_defaults(self._all_users().get(_validate_username(username)))

    def user_exists(self, username: str) -> bool:
        return _validate_username(username) in self._all_users()

    def create_user(self, username: str, record: dict) -> bool:
        _validate_username(username)
        users = self._all_users()
        if username in users:
            return False
        users[username] = {**USER_DEFAULTS, **record}
        self._write_private(self._path(LEGACY_USERS_FILE), users)
        return True

    def update_user(self, username: str, record: dict) -> None:
        _validate_username(username)
        users = self._all_users()
        users[username] = {**USER_DEFAULTS, **users.get(username, {}), **record}
        self._write_private(self._path(LEGACY_USERS_FILE), users)

    # --- per-user documents ---
    def get_doc(self, username: str, kind: str, default: Any = None) -> Any:
        _validate_username(username)
        _validate_kind(kind)
        return self._read(self._path(f"bullbear_{kind}_{username}.json"), default)

    def put_doc(self, username: str, kind: str, payload: Any) -> None:
        _validate_username(username)
        _validate_kind(kind)
        self._write_private(self._path(f"bullbear_{kind}_{username}.json"), payload)


# ----------------------------------------------------------------------
# Postgres backend.
# ----------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username           TEXT PRIMARY KEY,
    name               TEXT NOT NULL DEFAULT '',
    password_hash      TEXT NOT NULL,
    totp_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    totp_secret        TEXT,
    plan               TEXT NOT NULL DEFAULT 'free',
    plan_expires       TIMESTAMPTZ,
    stripe_customer_id TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_documents (
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (username, kind)
);

CREATE INDEX IF NOT EXISTS idx_users_stripe ON users(stripe_customer_id);
"""


class PostgresStorage:
    """
    Postgres via psycopg 3, using a small connection pool.

    Every method opens a connection from the pool and returns it immediately;
    Streamlit re-runs the whole script on each interaction, so holding a
    connection across a render would exhaust the pool within a few clicks.
    """

    backend = "postgres"

    def __init__(self, dsn: str):
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on install
            raise StorageError(
                "DATABASE_URL is set but psycopg is not installed. "
                "Run: pip install 'psycopg[binary]' psycopg-pool"
            ) from exc

        self.dsn = dsn
        # min_size=0 so a cold app doesn't hold connections it isn't using —
        # free-tier Postgres plans cap connections aggressively.
        self.pool = ConnectionPool(dsn, min_size=0, max_size=5, open=True,
                                   kwargs={"autocommit": True})
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    # --- accounts ---
    _USER_COLUMNS = ("name, password_hash, totp_enabled, totp_secret, "
                     "plan, plan_expires, stripe_customer_id")

    def get_user(self, username: str) -> dict | None:
        _validate_username(username)
        with self.pool.connection() as conn:
            row = conn.execute(
                f"SELECT {self._USER_COLUMNS} FROM users WHERE username = %s",
                (username,),
            ).fetchone()
        if row is None:
            return None
        record = dict(zip(
            ("name", "password_hash", "totp_enabled", "totp_secret",
             "plan", "plan_expires", "stripe_customer_id"), row))
        # Callers serialise this into session state, so hand back a string
        # rather than a datetime.
        if isinstance(record.get("plan_expires"), datetime):
            record["plan_expires"] = record["plan_expires"].isoformat()
        return _with_defaults(record)

    def user_exists(self, username: str) -> bool:
        _validate_username(username)
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM users WHERE username = %s", (username,)
            ).fetchone() is not None

    def create_user(self, username: str, record: dict) -> bool:
        _validate_username(username)
        merged = {**USER_DEFAULTS, **record}
        with self.pool.connection() as conn:
            # ON CONFLICT DO NOTHING makes "is this name taken" atomic —
            # a check-then-insert races two simultaneous registrations.
            result = conn.execute(
                """INSERT INTO users
                     (username, name, password_hash, totp_enabled, totp_secret,
                      plan, plan_expires, stripe_customer_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (username) DO NOTHING""",
                (username, merged["name"], merged["password_hash"],
                 merged["totp_enabled"], merged["totp_secret"], merged["plan"],
                 merged["plan_expires"], merged["stripe_customer_id"]),
            )
            return result.rowcount == 1

    def update_user(self, username: str, record: dict) -> None:
        _validate_username(username)
        current = self.get_user(username) or {}
        merged = {**USER_DEFAULTS, **current, **record}
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE users SET name = %s, password_hash = %s,
                          totp_enabled = %s, totp_secret = %s, plan = %s,
                          plan_expires = %s, stripe_customer_id = %s
                   WHERE username = %s""",
                (merged["name"], merged["password_hash"], merged["totp_enabled"],
                 merged["totp_secret"], merged["plan"], merged["plan_expires"],
                 merged["stripe_customer_id"], username),
            )

    # --- per-user documents ---
    def get_doc(self, username: str, kind: str, default: Any = None) -> Any:
        _validate_username(username)
        _validate_kind(kind)
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM user_documents WHERE username = %s AND kind = %s",
                (username, kind),
            ).fetchone()
        return default if row is None else row[0]

    def put_doc(self, username: str, kind: str, payload: Any) -> None:
        _validate_username(username)
        _validate_kind(kind)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO user_documents (username, kind, payload, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (username, kind)
                   DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()""",
                (username, kind, json.dumps(payload)),
            )


# ----------------------------------------------------------------------
# Backend selection and one-time migration.
# ----------------------------------------------------------------------
def database_url() -> str | None:
    """
    Reads DATABASE_URL from the environment, falling back to Streamlit
    secrets so it can be set in the Streamlit Cloud UI without env plumbing.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url.strip() or None
    try:
        import streamlit as st
        return (st.secrets.get("DATABASE_URL") or "").strip() or None
    except Exception:
        return None


def get_storage():
    """Returns the configured backend. Postgres when a URL is set, else JSON."""
    url = database_url()
    return PostgresStorage(url) if url else JSONStorage()


def migrate_json_to_postgres(pg: PostgresStorage, json_store: JSONStorage | None = None) -> dict:
    """
    Copies any local JSON data into Postgres, once.

    Skips accounts that already exist in the database, so running it twice is
    harmless and it can be left wired into startup. Returns a summary so the
    caller can report what moved.
    """
    json_store = json_store or JSONStorage()
    moved_users, moved_docs, skipped = 0, 0, 0

    for username, record in json_store._all_users().items():
        if not USERNAME_PATTERN.match(username):
            skipped += 1
            continue
        if pg.user_exists(username):
            skipped += 1
            continue
        pg.create_user(username, record)
        moved_users += 1
        for kind in DOC_KINDS:
            payload = json_store.get_doc(username, kind, None)
            if payload is not None:
                pg.put_doc(username, kind, payload)
                moved_docs += 1

    return {"users": moved_users, "documents": moved_docs, "skipped": skipped,
            "at": datetime.now(timezone.utc).isoformat()}
