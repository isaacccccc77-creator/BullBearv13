"""
Federated-identity mapping tests.

Run with:  python test_identity.py

The property under test is stability. This mapping decides which account
an email belongs to, and every watchlist, journal entry and saved theme
hangs off that decision. If it ever changes for the same person, they
sign in one day to an empty account and their data is still on disk under
a key nothing will ever look up again. There is no error message for
that, and no way to notice it in testing — only a user reporting it.

So: determinism, normalisation, and the guarantee that whatever comes out
is a username the storage layer will actually accept.
"""

import sys

import identity
import storage

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def raises(label, call):
    try:
        call()
    except identity.IdentityError:
        print(f"  ok   {label}")
        return
    print(f" FAIL  {label} — no error raised")
    failures.append(label)


print("\nDeterminism. The same address must map to the same account every")
print("time, on every machine, forever — this is the whole contract.")
for email in ["isaac@gmail.com", "a.b-c@sub.example.co.uk", "x1@q.io"]:
    runs = {identity.derive_username(email) for _ in range(50)}
    expect(f"{email} is stable across calls", len(runs), 1)

print("\nNormalisation. Case and whitespace are the same mailbox; signing in")
print("with a differently-cased address must not create a second account.")
base = identity.derive_username("isaac@gmail.com")
for variant in ["Isaac@Gmail.com", "ISAAC@GMAIL.COM", "  isaac@gmail.com  ",
                "\tisaac@gmail.com\n"]:
    expect(f"{variant!r} collapses", identity.derive_username(variant), base)

print("\nBut provider-specific aliasing is deliberately NOT collapsed.")
print("Merging two people's accounts is unrecoverable; leaving two")
print("addresses separate is merely inconvenient.")
expect("a dot-alias stays distinct",
       identity.derive_username("i.saac@gmail.com") != base, True)
expect("a plus-alias stays distinct",
       identity.derive_username("isaac+x@gmail.com") != base, True)
expect("a different domain stays distinct",
       identity.derive_username("isaac@outlook.com") != base, True)

print("\nWhatever comes out, the storage layer accepts it. A username the")
print("storage layer rejects means sign-in fails after the provider has")
print("already said yes, which is the worst place to discover it.")
hostile = [
    "isaac@gmail.com", "_@example.com", "...@example.com", "-@example.com",
    "a@example.com", "ab@example.com",
    "averyveryverylongaddressindeedthatkeepsgoing@example.org",
    "UPPER.CASE+tag@Example.COM", "123456@example.com",
    "user.name-with_everything@example.com",
    "ünïcödé@example.com", "'quote@example.com", "a!b#c$d@example.com",
]
for email in hostile:
    u = identity.derive_username(email)
    ok = bool(storage.USERNAME_PATTERN.match(u))
    expect(f"{email[:38]!r} -> {u!r} is a valid username", ok, True)
    expect(f"  and within length", 3 <= len(u) <= 32, True)

print("\nDistinct addresses get distinct accounts — no silent collisions")
print("across a realistic spread of inputs.")
corpus = [f"user{i}@example.com" for i in range(500)]
corpus += [f"{c}@example.com" for c in "abcdefghijklmnopqrstuvwxyz"]
corpus += ["isaac@a.com", "isaac@b.com", "isaac@c.com"]
derived = [identity.derive_username(e) for e in corpus]
expect(f"{len(corpus)} addresses give {len(set(derived))} usernames",
       len(set(derived)), len(corpus))

print("\nGarbage in is refused, not turned into an account.")
for bad in ["", "   ", "notanemail", "@example.com", "a@", "a@b",
            "a b@example.com", None]:
    raises(f"rejects {bad!r}", lambda b=bad: identity.derive_username(b))

print("\nDisplay names. Providers are inconsistent about which claims they")
print("send — Apple returns a name only on first authorisation, never again.")
expect("prefers the full name",
       identity.display_name({"name": "Isaac Tan", "email": "i@x.com"}), "Isaac Tan")
expect("falls back to the given name",
       identity.display_name({"given_name": "Isaac", "email": "i@x.com"}), "Isaac")
expect("then to the email local part",
       identity.display_name({"email": "isaac@x.com"}), "isaac")
expect("then to something harmless", identity.display_name({}), "Account")
expect("survives None", identity.display_name(None), "Account")
expect("ignores a blank name",
       identity.display_name({"name": "   ", "email": "isaac@x.com"}), "isaac")
expect("clips an absurd name",
       len(identity.display_name({"name": "N" * 500})), 60)


class _Attrs:
    """Streamlit's st.user is attribute-accessed, not a plain dict."""
    def __init__(self, **kw): self.__dict__.update(kw)


expect("reads attribute-style objects too",
       identity.display_name(_Attrs(name="Isaac Tan", email="i@x.com")), "Isaac Tan")

print("\nProvider labels.")
expect("google", identity.provider_label("google"), "Google")
expect("apple", identity.provider_label("apple"), "Apple")
expect("an unknown provider still reads sensibly",
       identity.provider_label("okta"), "Okta")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures[:6])}")
    sys.exit(1)
print("All identity checks passed.")
