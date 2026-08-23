"""
Donation links.

Tickveil is free. This module holds the small amount of logic behind the
"support this project" page, and its single most important property is what
it does NOT do: it never collects, transmits, or stores a payment detail of
any kind. Every link here points outward to a hosted checkout run by a
company whose actual job is handling money safely.

WHY IT IS BUILT THIS WAY
------------------------
Taking card numbers yourself means PCI-DSS obligations, storing or proxying
cardholder data, fraud screening, and chargeback handling. Linking to a
hosted page means the payment page is served by Ko-fi or Stripe on their own
domain, the card never touches this app or its server, and the compliance
burden collapses to the lightest tier that exists (SAQ-A). There is no volume
of donations at which building your own form becomes the better trade.

HOST PINNING
------------
Each destination is checked against an allowlist of hostnames as well as
being required to be https. Configuration usually arrives from environment
variables or a secrets file, which are exactly the places an attacker with
partial access would try to redirect money from. Pinning the host means a
tampered or fat-fingered value fails closed and the button simply does not
render, rather than quietly sending supporters somewhere else.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

# Each provider: the config key it reads, the label shown on the button, the
# hostnames its links are allowed to use, and a one-line note about the cut it
# takes. The fee notes are here so the page can be honest about where the
# money goes rather than leaving it unsaid.
PROVIDERS: dict[str, dict] = {
    "kofi": {
        "key": "SUPPORT_KOFI_URL",
        "label": "Ko-fi",
        "blurb": "One-off tip. Ko-fi takes no cut on donations.",
        "hosts": {"ko-fi.com", "www.ko-fi.com"},
    },
    "buymeacoffee": {
        "key": "SUPPORT_BMC_URL",
        "label": "Buy Me a Coffee",
        "blurb": "One-off tip. The platform takes about 5%.",
        "hosts": {"buymeacoffee.com", "www.buymeacoffee.com"},
    },
    "github": {
        "key": "SUPPORT_GITHUB_SPONSORS_URL",
        "label": "GitHub Sponsors",
        "blurb": "Monthly or one-off. GitHub takes no cut.",
        "hosts": {"github.com", "www.github.com"},
    },
    "stripe": {
        "key": "SUPPORT_STRIPE_URL",
        "label": "Card payment",
        "blurb": "Direct card payment through Stripe.",
        "hosts": {"buy.stripe.com", "donate.stripe.com"},
    },
    "paypal": {
        "key": "SUPPORT_PAYPAL_URL",
        "label": "PayPal",
        "blurb": "One-off, if PayPal is what you already use.",
        "hosts": {"paypal.me", "www.paypal.me", "paypal.com", "www.paypal.com"},
    },
}


def _read_config(key: str) -> str:
    """
    Environment first, then Streamlit secrets.

    Streamlit is imported lazily and inside a try, so this module stays
    importable — and testable — with no Streamlit installed at all.
    """
    value = os.environ.get(key)
    if value:
        return value.strip()
    try:
        import streamlit as st
        return (st.secrets.get(key) or "").strip()
    except Exception:
        return ""


def validate_link(url: str, allowed_hosts: set[str]) -> str:
    """
    Returns the URL if it is a plausible, https link to an allowed host.

    Rejects, in order: anything empty, any scheme other than https (http is
    refused too — a donation link travelling in clear text is a downgrade a
    supporter cannot see), any host outside the allowlist, and anything with
    whitespace or quoting characters that could break out of the attribute
    it gets rendered into.
    """
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if any(ch in candidate for ch in ' \t\r\n"\'<>\\'):
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts:
        return ""
    return candidate


def configured_links() -> list[dict]:
    """
    Every donation destination that is configured AND passes validation.

    Returns a list of {id, label, blurb, url}. An empty list means the app
    should show no donation UI at all — better to show nothing than a dead
    button, and it keeps the page clean for anyone running their own copy who
    has not set any of this up.
    """
    out = []
    for provider_id, meta in PROVIDERS.items():
        url = validate_link(_read_config(meta["key"]), meta["hosts"])
        if url:
            out.append({"id": provider_id, "label": meta["label"],
                        "blurb": meta["blurb"], "url": url})
    return out


def is_configured() -> bool:
    return bool(configured_links())
