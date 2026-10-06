"""
Federated sign-in: mapping an OIDC identity to an app account.

Streamlit ships native OpenID Connect (`st.login`, `st.user`), so the
protocol work is not ours. What is ours is the join: this app keys every
account, every saved watchlist and every journal entry by *username*, and
an identity provider hands back an *email*. Something has to decide which
username an email belongs to, and that decision has to be stable forever
— if it ever changes for the same person, they lose their data.

THE DESIGN, AND WHY IT IS NOT THE OBVIOUS ONE
---------------------------------------------
The obvious approach is: take the part before the @, use it if free,
append a number if not. That needs a reverse index from email to
username, because on the second visit you must know whether `isaac` is
*this* Isaac or a different one. Maintaining that index means a new
column on the users table, which means a schema migration on the
Postgres backend and a different shape on the JSON one.

Instead the mapping is a pure function of the email:

    isaac@gmail.com  ->  isaac-4d9f1a

The suffix is six hex characters of a SHA-256 of the normalised address.
It makes the result collision-resistant without any stored state, so the
same person lands on the same account from any device, on either storage
backend, with no index to keep consistent and nothing to migrate.

The cost is an uglier username, which is why the display name from the
provider is what the interface actually shows. The username becomes a
storage key the reader rarely sees.

NORMALISATION IS A SECURITY BOUNDARY
------------------------------------
`Isaac@Gmail.com` and `isaac@gmail.com` are the same mailbox and must map
to the same account, or signing in with a differently-cased address
silently creates a second empty one. Case folding is therefore mandatory.

Gmail's dots-and-plus aliasing is deliberately NOT collapsed. It is
tempting — `i.saac@gmail.com` does reach the same inbox — but the rule is
Gmail-specific, and applying it to a domain that treats dots as
significant would merge two genuinely different people into one account.
Merging accounts is unrecoverable; leaving them separate is merely
inconvenient. The conservative direction is the correct one.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Must satisfy storage.USERNAME_PATTERN: 3-32 chars of [A-Za-z0-9._-],
# not starting with a dot. The suffix is 7 of those characters, so the
# stem is capped to leave room.
_SUFFIX_LEN = 6
_STEM_MAX = 32 - _SUFFIX_LEN - 1
_ALLOWED = re.compile(r"[^a-z0-9._-]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class IdentityError(ValueError):
    """Raised when a provider hands back something unusable."""


def normalise_email(email: str) -> str:
    """
    Lower-cased and whitespace-stripped. Nothing else.

    See the module docstring: provider-specific aliasing is not collapsed,
    because being wrong in that direction merges two people's accounts.
    """
    cleaned = (email or "").strip()
    # Unicode domains can be written more than one way; NFKC picks one so
    # two spellings of the same address do not become two accounts.
    cleaned = unicodedata.normalize("NFKC", cleaned).lower()
    if not _EMAIL_RE.match(cleaned):
        raise IdentityError(f"not a usable email address: {email!r}")
    return cleaned


def derive_username(email: str) -> str:
    """
    The stable account key for an email address.

    Deterministic: the same address always returns the same username, on
    every machine and every backend, with no stored mapping.
    """
    normalised = normalise_email(email)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:_SUFFIX_LEN]

    stem = _ALLOWED.sub("", normalised.split("@", 1)[0].replace("+", "-"))
    stem = stem.lstrip(".")[:_STEM_MAX].rstrip(".-_")
    # An address whose local part is entirely punctuation leaves nothing
    # to build on, and a bare suffix would be an opaque 6 characters.
    if len(stem) < 2:
        stem = "user"
    return f"{stem}-{digest}"


def display_name(user_info) -> str:
    """
    The name to show in the interface.

    Providers are inconsistent about which claims they return — Apple
    sends a name only on the very first authorisation and never again —
    so this falls back through what is actually available rather than
    assuming a claim exists.
    """
    def claim(key):
        if user_info is None:
            return None
        try:
            value = user_info.get(key)          # mapping-like
        except AttributeError:
            value = getattr(user_info, key, None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    name = claim("name") or claim("given_name")
    if name:
        return name[:60]
    email = claim("email")
    if email:
        return email.split("@", 1)[0][:60]
    return "Account"


def provider_label(provider: str) -> str:
    return {"google": "Google", "apple": "Apple",
            "microsoft": "Microsoft"}.get(provider, provider.title())
