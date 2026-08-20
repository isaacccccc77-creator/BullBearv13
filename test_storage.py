"""
Storage backend tests.

Run with:  python test_storage.py

Runs the *same* assertions against both backends, because the whole point of
storage.py is that the app cannot tell them apart. A behaviour that holds for
JSON files but not for Postgres is a bug that would only appear in production,
which is the worst place to find it.

The Postgres half is skipped unless TEST_DATABASE_URL is set, so the suite
still passes on a machine with no database. To run it locally:

    TEST_DATABASE_URL=postgresql://user@host:5432/dbname python test_storage.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage  # noqa: E402

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def run_contract(store, label):
    """Every behaviour the app relies on, asserted against one backend."""
    print(f"\n--- {label} ({store.backend}) ---")

    expect("unknown account reads as None", store.get_user("nobody_here"), None)
    expect("unknown account does not exist", store.user_exists("nobody_here"), False)

    created = store.create_user("judge", {
        "name": "Isaac", "password_hash": "$2b$12$fakehashvalue",
    })
    expect("create_user returns True for a new name", created, True)
    expect("account now exists", store.user_exists("judge"), True)

    again = store.create_user("judge", {"name": "Impostor", "password_hash": "x"})
    expect("create_user returns False for a taken name", again, False)
    expect("the original record is untouched", store.get_user("judge")["name"], "Isaac")

    record = store.get_user("judge")
    expect("billing fields default for rows written before billing existed",
           (record["plan"], record["plan_expires"], record["stripe_customer_id"]),
           ("free", None, None))

    record["totp_enabled"] = True
    record["totp_secret"] = "SECRET123"
    record["plan"] = "pro"
    store.update_user("judge", record)
    updated = store.get_user("judge")
    expect("update persists 2FA state", (updated["totp_enabled"], updated["totp_secret"]),
           (True, "SECRET123"))
    expect("update persists plan", updated["plan"], "pro")
    expect("update leaves untouched fields alone", updated["name"], "Isaac")

    expect("missing document returns the default",
           store.get_doc("judge", "journal", "fallback"), "fallback")

    entries = [{"ticker": "AAPL", "qty": 10, "note": "unicode ok — £ ¥ 中"}]
    store.put_doc("judge", "journal", entries)
    expect("journal round-trips exactly", store.get_doc("judge", "journal"), entries)

    store.put_doc("judge", "journal", [])
    expect("an empty list round-trips as a list, not as missing",
           store.get_doc("judge", "journal", "MISSING"), [])

    store.put_doc("judge", "watchlist", {"watchlist": "AAPL, MSFT"})
    expect("documents are independent of each other",
           store.get_doc("judge", "watchlist")["watchlist"], "AAPL, MSFT")
    expect("writing one document leaves the others alone",
           store.get_doc("judge", "journal"), [])

    store.put_doc("judge", "watchlist", {"watchlist": "TSLA"})
    expect("overwriting replaces rather than appends",
           store.get_doc("judge", "watchlist")["watchlist"], "TSLA")

    store.create_user("second", {"name": "Other", "password_hash": "y"})
    store.put_doc("second", "watchlist", {"watchlist": "NVDA"})
    expect("one account cannot see another's documents",
           store.get_doc("judge", "watchlist")["watchlist"], "TSLA")

    for bad in ["../../etc/passwd", "a/b", ""]:
        try:
            store.get_user(bad)
            expect(f"rejects username {bad!r}", "NOT REJECTED", "rejected")
        except storage.StorageError:
            expect(f"rejects username {bad!r}", "rejected", "rejected")

    try:
        store.get_doc("judge", "../../etc/passwd")
        expect("rejects an unknown document kind", "NOT REJECTED", "rejected")
    except storage.StorageError:
        expect("rejects an unknown document kind", "rejected", "rejected")


# --- JSON backend -----------------------------------------------------
json_dir = tempfile.mkdtemp(prefix="tickveil-json-")
try:
    run_contract(storage.JSONStorage(json_dir), "JSON backend")

    print("\n--- file permissions ---")
    users_file = os.path.join(json_dir, storage.LEGACY_USERS_FILE)
    expect("account file is owner-only", oct(os.stat(users_file).st_mode & 0o777), "0o600")
    expect("data directory is owner-only", oct(os.stat(json_dir).st_mode & 0o777), "0o700")
finally:
    shutil.rmtree(json_dir, ignore_errors=True)


# --- Postgres backend -------------------------------------------------
DSN = os.environ.get("TEST_DATABASE_URL")
if not DSN:
    print("\n--- Postgres backend: SKIPPED (set TEST_DATABASE_URL to run) ---")
else:
    pg = storage.PostgresStorage(DSN)
    with pg.pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS user_documents, users CASCADE")
    pg.ensure_schema()
    run_contract(pg, "Postgres backend")

    # --- migration ---
    print("\n--- JSON -> Postgres migration ---")
    with pg.pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS user_documents, users CASCADE")
    pg.ensure_schema()

    legacy_dir = tempfile.mkdtemp(prefix="tickveil-legacy-")
    try:
        legacy = storage.JSONStorage(legacy_dir)
        legacy.create_user("olduser", {"name": "Existing", "password_hash": "$2b$12$legacy"})
        legacy.put_doc("olduser", "journal", [{"ticker": "TSLA", "qty": 3}])
        legacy.put_doc("olduser", "watchlist", {"watchlist": "AMD, INTC"})

        summary = storage.migrate_json_to_postgres(pg, legacy)
        expect("migrated the account", summary["users"], 1)
        expect("migrated both documents", summary["documents"], 2)
        expect("account is readable from Postgres", pg.get_user("olduser")["name"], "Existing")
        expect("password hash survived", pg.get_user("olduser")["password_hash"], "$2b$12$legacy")
        expect("journal survived", pg.get_doc("olduser", "journal"), [{"ticker": "TSLA", "qty": 3}])
        expect("watchlist survived", pg.get_doc("olduser", "watchlist")["watchlist"], "AMD, INTC")

        # Running it twice must not duplicate or clobber.
        pg.put_doc("olduser", "watchlist", {"watchlist": "CHANGED IN PROD"})
        second = storage.migrate_json_to_postgres(pg, legacy)
        expect("re-running migrates nothing", second["users"], 0)
        expect("re-running skips the existing account", second["skipped"], 1)
        expect("re-running does not clobber newer data",
               pg.get_doc("olduser", "watchlist")["watchlist"], "CHANGED IN PROD")
    finally:
        shutil.rmtree(legacy_dir, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All storage checks passed.")
