"""
Security regression tests for Tickveil.

Run with:  python test_security.py

These cover the input-handling boundaries where a mistake would be a real
vulnerability rather than a cosmetic bug — the places where untrusted input
(a chosen username, a headline from an upstream feed) reaches a filesystem
path or a rendered link.

The app is a single Streamlit script that renders a page on import, so
importing it here would try to boot a whole app. Instead each function under
test is extracted from the source and executed in an isolated namespace.
That keeps the tests honest — they run the real shipped code, not a copy —
without needing a running server.
"""

import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_v30.py")
SOURCE = open(APP, encoding="utf-8").read()

TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".test_data")

# Username validation and path confinement live in storage.py, which imports
# no Streamlit and so can be imported outright.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage  # noqa: E402


def load_functions(names):
    """Pulls named top-level functions out of the app and compiles them.

    The app is a single Streamlit script that renders a page on import, so
    importing it here would try to boot a whole app. Extracting the functions
    keeps the tests honest — they run the real shipped code, not a copy —
    without needing a running server.
    """
    namespace = {"re": re, "os": os}
    for name in names:
        start = SOURCE.index(f"def {name}(")
        end = SOURCE.index("\n\ndef ", start)
        exec(SOURCE[start:end], namespace)
    return namespace


fn = load_functions(["password_problem", "safe_link", "markdown_safe"])

# Backed by the real storage module rather than a re-implementation.
store = storage.JSONStorage(TEST_DATA_DIR)
fn["is_valid_username"] = lambda n: bool(storage.USERNAME_PATTERN.match(n or ""))
fn["user_data_path"] = store._path

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def expect_raises(label, call):
    try:
        call()
    except (ValueError, storage.StorageError):
        print(f"  ok   {label}")
        return
    print(f" FAIL  {label} — no error raised")
    failures.append(label)


print("\nUsername validation — usernames become filenames, so a name that")
print("escapes the data directory is a write primitive, not a typo.")
for bad in ["../../etc/passwd", "..", "a/b", "a\\b", ".hidden", "ab",
            "a" * 33, "a b", "a;b", "a\x00b", ""]:
    expect(f"rejects {bad!r}", fn["is_valid_username"](bad), False)
for good in ["Isaac77", "judge", "a.b-c_d", "abc"]:
    expect(f"accepts {good!r}", fn["is_valid_username"](good), True)

print("\nPath confinement — the second, independent guard on every write.")
expect(
    "an ordinary name resolves inside the data directory",
    fn["user_data_path"]("bullbear_watchlist_judge.json").startswith(
        os.path.realpath(TEST_DATA_DIR)),
    True,
)
for escape in ["../escape.json", "../../etc/cron.d/payload", "sub/../../out.json"]:
    expect_raises(f"refuses {escape!r}", lambda e=escape: fn["user_data_path"](e))

print("\nDocument-kind whitelist — a caller cannot invent a key that would")
print("become an arbitrary filename.")
for bad_kind in ["../../etc/passwd", "arbitrary", ""]:
    expect_raises(f"refuses kind {bad_kind!r}",
                  lambda k=bad_kind: store.get_doc("judge", k))
for good_kind in sorted(storage.DOC_KINDS):
    expect(f"accepts kind {good_kind!r}", store.get_doc("judge", good_kind, "fallback"), "fallback")

print("\nLink schemes — headline URLs come from an upstream feed and are")
print("rendered as markdown links, so anything but http(s) is refused.")
for bad in ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
            "JaVaScRiPt:alert(1)", "file:///etc/passwd", "vbscript:x",
            "https://example.com/a b", 'https://example.com/"onmouseover=x', ""]:
    expect(f"rejects {bad[:38]!r}", fn["safe_link"](bad), "")
expect(
    "accepts a normal https URL",
    fn["safe_link"]("https://finance.yahoo.com/news/story"),
    "https://finance.yahoo.com/news/story",
)

print("\nMarkdown escaping — a headline containing ']' must not be able to")
print("close its own link and inject markup after it.")
expect(
    "escapes link delimiters in a hostile headline",
    fn["markdown_safe"]("Bad](javascript:alert(1)) headline"),
    "Bad\\]\\(javascript:alert\\(1\\)\\) headline",
)

print("\nPassword policy.")
expect("rejects a short password", fn["password_problem"]("abc123") is not None, True)
expect("rejects a single repeated character", fn["password_problem"]("aaaaaaaaaaaa") is not None, True)
expect("rejects a keyboard run", fn["password_problem"]("qwertyuiop12") is not None, True)
expect("accepts a long passphrase", fn["password_problem"]("correct horse battery"), None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All security checks passed.")
