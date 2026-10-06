"""
PayNow QR generation.

PayNow is Singapore's bank transfer scheme. A PayNow QR is an EMVCo
merchant-presented QR code — the same container Visa and Mastercard use —
carrying a payee proxy (a mobile number or a UEN) instead of a card
number. The payer scans it in their own banking app, which shows them the
payee name their bank has on file and asks them to confirm.

Why generate it here rather than link out, when support.py argues the
opposite for cards:

* **Nothing sensitive is involved.** The payload contains a payee proxy
  that is already public — the number on an invoice — and an amount. It
  carries no payer data at all, so there is nothing for this app to
  mishandle.
* **There is no processor to link to.** PayNow has no hosted checkout. A
  static QR is the whole mechanism.
* **It cannot move money on its own.** The QR is an instruction the payer
  authorises inside their own bank. A tampered QR sends money to the
  wrong place, which is why the proxy is validated here and the payload
  is checksummed — but it can never pull funds.

Cards remain a hosted-checkout link, for exactly the reasons in
support.py. The two are different problems and get different answers.

The spec implemented here is EMVCo's "Merchant Presented Mode" with the
Singapore PayNow fields. Field order matters: the CRC at the end is
computed over every preceding byte including its own tag and length, so
the payload has to be assembled before it can be signed.
"""

from __future__ import annotations

import io
import re
from datetime import date

# --- EMVCo field identifiers used here ------------------------------
_ID_PAYLOAD_FORMAT = "00"
_ID_INITIATION = "01"
_ID_MERCHANT_ACCOUNT = "26"     # PayNow lives in the 26-51 template range
_ID_CATEGORY_CODE = "52"
_ID_CURRENCY = "53"
_ID_AMOUNT = "54"
_ID_COUNTRY = "58"
_ID_MERCHANT_NAME = "59"
_ID_MERCHANT_CITY = "60"
_ID_ADDITIONAL = "62"
_ID_CRC = "63"

PROXY_MOBILE = "0"
PROXY_UEN = "2"

# A Singapore mobile number in the form PayNow expects: +65 then 8 digits
# starting 8 or 9. Stored with the country code because the payload wants
# it that way and because a bare 8 digits is ambiguous.
_MOBILE_RE = re.compile(r"^\+65[89]\d{7}$")
# UENs come in several shapes; this accepts the three ACRA formats rather
# than trying to validate the check letter, which is not public.
_UEN_RE = re.compile(r"^(\d{8}[A-Z]|\d{9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])$")


class PayNowError(ValueError):
    """Raised when a payload could not be built from the inputs given."""


def _tlv(identifier: str, value: str) -> str:
    """One EMVCo tag-length-value triple. Length is 2 digits, zero padded."""
    if len(value) > 99:
        raise PayNowError(f"field {identifier} is too long ({len(value)} chars)")
    return f"{identifier}{len(value):02d}{value}"


def crc16_ccitt(data: str) -> str:
    """
    CRC-16/CCITT-FALSE over the payload, as EMVCo specifies.

    Polynomial 0x1021, initial value 0xFFFF, no final XOR, most
    significant bit first. Returned as four uppercase hex digits, which
    is what goes on the wire — a lowercase checksum is rejected by some
    bank apps and accepted by others, so it is normalised here.
    """
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def normalise_mobile(raw: str) -> str:
    """Accepts the ways people actually type a number; emits +65XXXXXXXX."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+65"):
        candidate = digits
    elif digits.startswith("65") and len(digits) == 10:
        candidate = "+" + digits
    elif len(digits) == 8:
        candidate = "+65" + digits
    else:
        candidate = digits
    if not _MOBILE_RE.match(candidate):
        raise PayNowError(
            "That does not look like a Singapore mobile number. PayNow needs "
            "eight digits starting 8 or 9, with or without +65."
        )
    return candidate


def normalise_uen(raw: str) -> str:
    candidate = re.sub(r"\s", "", (raw or "")).upper()
    if not _UEN_RE.match(candidate):
        raise PayNowError(
            "That does not look like a Singapore UEN. Expected formats are "
            "12345678A, 123456789A, or T12AB1234C."
        )
    return candidate


def paynow_payload(proxy_value: str, *, proxy_type: str = PROXY_MOBILE,
                   merchant_name: str = "NA", amount: float | None = None,
                   editable_amount: bool | None = None,
                   reference: str | None = None,
                   expiry: date | None = None,
                   city: str = "Singapore") -> str:
    """
    Builds the string that goes inside a PayNow QR code.

    `editable_amount` defaults to the sensible thing: locked when an
    amount is given, open when it is not. A QR with an amount the payer
    can still edit is a confusing object — it looks like a demand and
    behaves like a suggestion — so the two are tied together unless the
    caller insists otherwise.
    """
    if proxy_type == PROXY_MOBILE:
        proxy = normalise_mobile(proxy_value)
    elif proxy_type == PROXY_UEN:
        proxy = normalise_uen(proxy_value)
    else:
        raise PayNowError(f"unknown proxy type {proxy_type!r}")

    if amount is not None:
        if amount <= 0:
            raise PayNowError("amount must be positive")
        if amount >= 1_000_000:
            raise PayNowError("amount is implausibly large for a donation")

    if editable_amount is None:
        editable_amount = amount is None

    merchant = _sanitise_text(merchant_name, 25) or "NA"

    account = (
        _tlv("00", "SG.PAYNOW")
        + _tlv("01", proxy_type)
        + _tlv("02", proxy)
        + _tlv("03", "1" if editable_amount else "0")
    )
    if expiry is not None:
        account += _tlv("04", expiry.strftime("%Y%m%d"))

    payload = (
        _tlv(_ID_PAYLOAD_FORMAT, "01")
        # 11 = static, may be scanned repeatedly. 12 = single use. A
        # donation QR is scanned by many people, so it is always static.
        + _tlv(_ID_INITIATION, "11")
        + _tlv(_ID_MERCHANT_ACCOUNT, account)
        + _tlv(_ID_CATEGORY_CODE, "0000")
        + _tlv(_ID_CURRENCY, "702")                       # SGD, ISO 4217
    )
    if amount is not None:
        payload += _tlv(_ID_AMOUNT, f"{amount:.2f}")
    payload += (
        _tlv(_ID_COUNTRY, "SG")
        + _tlv(_ID_MERCHANT_NAME, merchant)
        + _tlv(_ID_MERCHANT_CITY, _sanitise_text(city, 15) or "Singapore")
    )
    if reference:
        payload += _tlv(_ID_ADDITIONAL, _tlv("01", _sanitise_text(reference, 25)))

    # The CRC covers its own tag and length, so they are appended before
    # the digest is taken and the four-character result completes it.
    payload += _ID_CRC + "04"
    return payload + crc16_ccitt(payload)


def _sanitise_text(value: str, limit: int) -> str:
    """
    EMVCo fields are a restricted character set in practice. Anything
    outside printable ASCII is dropped rather than transliterated —
    a mangled payee name is worse than a shortened one.
    """
    cleaned = re.sub(r"[^\x20-\x7E]", "", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit]


def parse_payload(payload: str) -> dict:
    """
    Reads a payload back into its fields. Used by the tests, and by the
    UI to show a human what the QR actually says before they trust it.
    """
    out, i = {}, 0
    body = payload
    while i + 4 <= len(body):
        tag = body[i:i + 2]
        try:
            length = int(body[i + 2:i + 4])
        except ValueError:
            raise PayNowError(f"malformed length at offset {i}")
        value = body[i + 4:i + 4 + length]
        if len(value) != length:
            raise PayNowError(f"truncated value for tag {tag}")
        out[tag] = value
        i += 4 + length
    return out


def verify_payload(payload: str) -> bool:
    """True when the trailing checksum matches the rest of the payload."""
    if len(payload) < 8 or payload[-8:-4] != _ID_CRC + "04":
        return False
    return crc16_ccitt(payload[:-4]) == payload[-4:].upper()


def paynow_qr_png(payload: str, *, scale: int = 9, border: int = 2) -> bytes:
    """
    Renders the payload as a PNG.

    Error correction is set to M rather than the library default. PayNow
    payloads are short enough that the higher level costs almost no
    module count, and a donation QR gets photographed off a screen at an
    angle more often than it gets scanned flat.
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=scale, border=border)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
