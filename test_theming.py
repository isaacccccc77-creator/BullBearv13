"""
Palette guards.

Run with:  python test_theming.py

A theme is a product surface, not decoration: if one of them puts 3:1
grey on white, a reader who picks it gets an app they cannot read, and
nobody tests every theme by eye on every screen. So contrast is asserted
here, in numbers, for every role in every palette.

The bar is WCAG AA — 4.5:1 for body text. Large display type is allowed
3:1 but nothing here relies on that exemption, so the stricter bar
applies throughout.
"""

import sys
import theming

AA_BODY = 4.5
failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


def at_least(label, value, floor):
    ok = value >= floor
    print(f"{'  ok  ' if ok else ' FAIL '} {label} ({value:.2f})")
    if not ok:
        print(f"        needs at least {floor}")
        failures.append(label)


print("\nEvery text role in every theme clears WCAG AA on its own page colour.")
for name in theming.theme_names():
    for role, colour, ratio in theming.contrast_report(name):
        at_least(f"{name}/{role}", ratio, AA_BODY)

print("\nDirectional colour keeps its meaning in every theme. A reader who")
print("learns green-is-up in one palette must not relearn it in another.")
for name in theming.theme_names():
    t = theming.get_theme(name)
    # Green really is greener than it is red, and vice versa.
    def rgb(h):
        h = h.lstrip("#"); return [int(h[i:i+2], 16) for i in (0, 2, 4)]
    up, down = rgb(t["jade"]), rgb(t["rose"])
    expect(f"{name}: up is green-dominant", up[1] > up[0] and up[1] > up[2], True)
    expect(f"{name}: down is red-dominant", down[0] > down[1] and down[0] > down[2], True)

print("\nEvery palette defines the whole token set — a theme that inherits")
print("a missing value from another one fails exactly where nobody looks.")
reference = set(theming.THEMES[theming.DEFAULT_THEME])
for name, t in theming.THEMES.items():
    expect(f"{name} has no missing tokens", sorted(reference - set(t)), [])
    expect(f"{name} has no stray tokens", sorted(set(t) - reference), [])

print("\nThe CSS block is well formed and carries the overlay token that")
print("makes a light theme possible at all.")
for name in theming.theme_names():
    css = theming.css_variables(name)
    expect(f"{name}: braces balance", css.count("{") == css.count("}"), True)
    expect(f"{name}: declares --tint-rgb", "--tint-rgb:" in css, True)
    expect(f"{name}: no unexpanded placeholder", "{" in css.split("\n", 1)[1].replace("{{", ""), False)

print("\nAn unknown or missing theme name falls back rather than raising.")
expect("None falls back", theming.resolve(None), theming.DEFAULT_THEME)
expect("junk falls back", theming.resolve("neon-hotdog"), theming.DEFAULT_THEME)
expect("a real name survives", theming.resolve("ivory"), "ivory")
expect("get_theme never returns None", theming.get_theme("nope") is not None, True)

print("\nThe chart palette is literal colours. Plotly serialises the figure,")
print("so a CSS variable reaches it as the string 'var(--gold-500)'.")
for name in theming.theme_names():
    pal = theming.chart_palette(name)
    for key in ("gold", "jade", "rose", "text", "muted", "grid"):
        expect(f"{name}/{key} is not a CSS var", "var(" in str(pal[key]), False)
    expect(f"{name} exposes is_light", isinstance(pal["is_light"], bool), True)

print("\nExactly one theme is light, and it is the one that says so.")
light = [n for n in theming.theme_names() if theming.get_theme(n)["is_light"]]
expect("one light theme", light, ["ivory"])

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures[:8])}")
    sys.exit(1)
print("All theme checks passed.")
