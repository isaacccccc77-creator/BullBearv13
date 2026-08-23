"""
Donation link tests.

Run with:  python test_support.py

The property under test is that a donation button either points exactly where
it should or does not render at all. Configuration for these links comes from
environment variables and secrets files — precisely the places someone with
partial access would try to redirect money from — so anything unexpected has
to fail closed rather than fail open.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


KOFI = support.PROVIDERS["kofi"]["hosts"]

print("\nA correct link is accepted.")
expect("plain ko-fi link", support.validate_link("https://ko-fi.com/isaac", KOFI),
       "https://ko-fi.com/isaac")
expect("www subdomain is allowed too",
       support.validate_link("https://www.ko-fi.com/isaac", KOFI),
       "https://www.ko-fi.com/isaac")

print("\nScheme — https only. A donation link in clear text is a downgrade")
print("a supporter has no way to notice.")
for bad in ["http://ko-fi.com/isaac", "//ko-fi.com/isaac", "ko-fi.com/isaac",
            "javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd"]:
    expect(f"rejects {bad[:36]!r}", support.validate_link(bad, KOFI), "")

print("\nHost pinning — the whole point. A tampered config must not silently")
print("redirect donations to somebody else's account.")
for bad in [
    "https://ko-fi.com.evil.tld/isaac",       # suffix trick
    "https://evilko-fi.com/isaac",            # prefix trick
    "https://attacker.example/isaac",         # unrelated host
    "https://ko-fi.evil.com/isaac",           # subdomain trick
    "https://user@evil.com/isaac",            # userinfo confusion
    "https://evil.com#ko-fi.com",             # fragment decoration
    "https://evil.com/?next=https://ko-fi.com/isaac",
]:
    expect(f"rejects {bad[:46]!r}", support.validate_link(bad, KOFI), "")

print("\nInjection characters cannot reach the rendered attribute.")
for bad in ['https://ko-fi.com/a"onmouseover=x', "https://ko-fi.com/a b",
            "https://ko-fi.com/a\nb", "https://ko-fi.com/a'x", "https://ko-fi.com/<script>"]:
    expect(f"rejects {bad[:38]!r}", support.validate_link(bad, KOFI), "")

print("\nEmpty and malformed input.")
for bad in ["", "   ", None, "https://", "not a url at all"]:
    expect(f"rejects {bad!r}", support.validate_link(bad, KOFI), "")

print("\nEach provider only accepts its own hosts.")
expect("a Stripe URL is not valid as a Ko-fi link",
       support.validate_link("https://buy.stripe.com/xyz", KOFI), "")
expect("a Ko-fi URL is not valid as a Stripe link",
       support.validate_link("https://ko-fi.com/isaac",
                             support.PROVIDERS["stripe"]["hosts"]), "")

print("\nDiscovery from configuration.")
for meta in support.PROVIDERS.values():
    os.environ.pop(meta["key"], None)
expect("nothing configured means no donation UI at all", support.configured_links(), [])
expect("and is_configured agrees", support.is_configured(), False)

os.environ["SUPPORT_KOFI_URL"] = "https://ko-fi.com/isaac"
os.environ["SUPPORT_BMC_URL"] = "http://buymeacoffee.com/isaac"   # wrong scheme
links = support.configured_links()
expect("only the valid link is offered", [link["id"] for link in links], ["kofi"])
expect("and it carries its label", links[0]["label"], "Ko-fi")
expect("and a fee note", bool(links[0]["blurb"]), True)

os.environ["SUPPORT_BMC_URL"] = "https://buymeacoffee.com/isaac"
expect("fixing the scheme makes it appear",
       sorted(link["id"] for link in support.configured_links()), ["buymeacoffee", "kofi"])

print("\nEvery provider declares the fields the UI needs.")
for pid, meta in support.PROVIDERS.items():
    expect(f"{pid} is fully specified",
           all(meta.get(k) for k in ("key", "label", "blurb", "hosts")), True)

for meta in support.PROVIDERS.values():
    os.environ.pop(meta["key"], None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All support-link checks passed.")
