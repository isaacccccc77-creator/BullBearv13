"""
PayNow payload tests.

Run with:  python test_payments.py

A wrong PayNow QR is a quiet failure with a real cost: the payer's bank
app either refuses it, or — worse — accepts it and sends money to the
wrong proxy. Neither shows up in an error log here, because the app is
not in the payment path at all. So the payload is checked structurally,
the checksum against the published check value for the algorithm, and
the proxy validators against the ways people actually type a number.
"""

import sys
from datetime import date

import payments

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def raises(label, call, exc=payments.PayNowError):
    try:
        call()
    except exc:
        print(f"  ok   {label}")
        return
    except Exception as e:
        print(f" FAIL  {label} — wrong exception {type(e).__name__}")
        failures.append(label); return
    print(f" FAIL  {label} — no error raised")
    failures.append(label)


print("\nCRC-16/CCITT-FALSE, against the algorithm's published check value.")
print("This is the one number in the file that is not my arithmetic:")
print("the standard defines CRC(\"123456789\") = 0x29B1 for this variant.")
expect("check value matches", payments.crc16_ccitt("123456789"), "29B1")
expect("empty input is the init value", payments.crc16_ccitt(""), "FFFF")
expect("output is upper case hex",
       payments.crc16_ccitt("SG.PAYNOW").isupper(), True)
expect("output is always four digits",
       all(len(payments.crc16_ccitt(s)) == 4 for s in ["", "a", "abc", "x" * 500]), True)


print("\nEvery payload it builds verifies against its own checksum.")
cases = [
    dict(proxy_value="91234567"),
    dict(proxy_value="+6598765432", amount=5.0),
    dict(proxy_value="81234567", amount=12.34, reference="COFFEE"),
    dict(proxy_value="53012345A", proxy_type=payments.PROXY_UEN, amount=3.0),
    dict(proxy_value="91234567", expiry=date(2027, 1, 31)),
]
for c in cases:
    p = payments.paynow_payload(merchant_name="TICKVEIL", **c)
    expect(f"verifies: {c}", payments.verify_payload(p), True)

print("\nA tampered payload fails the checksum — which is the whole point")
print("of having one on a value that tells a bank where to send money.")
good = payments.paynow_payload("91234567", merchant_name="TICKVEIL", amount=5.0)
# Flip the last digit of the proxy and keep the original CRC.
tampered = good.replace("+6591234567", "+6591234568")
expect("the tamper actually changed it", tampered != good, True)
expect("and it no longer verifies", payments.verify_payload(tampered), False)
expect("a truncated payload is refused", payments.verify_payload(good[:-1]), False)
expect("so is an empty one", payments.verify_payload(""), False)


print("\nStructure: the fields a bank app reads are where it expects them.")
f = payments.parse_payload(good)
expect("payload format indicator", f["00"], "01")
expect("static QR, so it can be scanned more than once", f["01"], "11")
expect("currency is SGD", f["53"], "702")
expect("country is SG", f["58"], "SG")
expect("amount carries two decimals", f["54"], "5.00")
acct = payments.parse_payload(f["26"])
expect("the scheme is named", acct["00"], "SG.PAYNOW")
expect("proxy type is mobile", acct["01"], payments.PROXY_MOBILE)
expect("proxy is normalised to +65", acct["02"], "+6591234567")
expect("a priced QR locks the amount", acct["03"], "0")

open_qr = payments.paynow_payload("91234567", merchant_name="TICKVEIL")
expect("an unpriced QR leaves the amount editable",
       payments.parse_payload(payments.parse_payload(open_qr)["26"])["03"], "1")
expect("and carries no amount field", "54" in payments.parse_payload(open_qr), False)


print("\nMobile numbers, in the shapes people actually type them.")
for raw in ["91234567", "9123 4567", "+6591234567", "+65 9123 4567",
            "6591234567", "+65-9123-4567"]:
    expect(f"{raw!r} normalises", payments.normalise_mobile(raw), "+6591234567")
for bad in ["1234567", "71234567", "912345678", "", "abcdefgh", "+4479123456"]:
    raises(f"rejects {bad!r}", lambda b=bad: payments.normalise_mobile(b))

print("\nUENs, in the three ACRA shapes.")
for raw, want in [("53012345a", "53012345A"), ("201912345K", "201912345K"),
                  ("T12LL1234A", "T12LL1234A"), (" 53012345A ", "53012345A")]:
    expect(f"{raw!r} normalises", payments.normalise_uen(raw), want)
for bad in ["12345678", "ABCDEFGH", "", "5301234A"]:
    raises(f"rejects {bad!r}", lambda b=bad: payments.normalise_uen(b))


print("\nAmounts that cannot be right are refused rather than encoded.")
raises("zero", lambda: payments.paynow_payload("91234567", amount=0))
raises("negative", lambda: payments.paynow_payload("91234567", amount=-5))
raises("implausible", lambda: payments.paynow_payload("91234567", amount=10_000_000))
raises("unknown proxy type",
       lambda: payments.paynow_payload("91234567", proxy_type="9"))

print("\nText fields are clipped and stripped, not mangled. A payee name")
print("that arrives as mojibake is worse than one that arrives short.")
long_name = payments.paynow_payload("91234567", merchant_name="X" * 80)
expect("merchant name is clipped to 25",
       len(payments.parse_payload(long_name)["59"]), 25)
accented = payments.paynow_payload("91234567", merchant_name="Café Tickveil")
expect("non-ASCII is dropped, not transliterated",
       payments.parse_payload(accented)["59"], "Caf Tickveil")
expect("an empty name falls back rather than emitting an empty field",
       payments.parse_payload(
           payments.paynow_payload("91234567", merchant_name="   "))["59"], "NA")


print("\nThe PNG renders and is a PNG.")
png = payments.paynow_qr_png(good)
expect("has the PNG magic number", png[:8], b"\x89PNG\r\n\x1a\n")
expect("is not empty", len(png) > 200, True)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures[:6])}")
    sys.exit(1)
print("All payment checks passed.")
