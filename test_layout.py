"""
Layout regression guards for the app script.

Run with:  python test_layout.py

These catch a bug class that is invisible in a desktop browser and obvious
on a phone, which is the worst combination to rely on manual testing for.

The sign-in screen was centred with `st.columns([1, 2, 1])` and two empty
gutter columns. Streamlit lays columns out as a *wrapping* flex row and
gives each column a minimum width, so on a narrow screen the three no
longer fit: the empty left gutter keeps its place on row one, the real
panel is pushed to the right of it, and the right gutter wraps to a row
of its own where nothing shows. The panel ends up off-centre and narrow
enough that a two-item tab rail starts scrolling sideways.

Whether it happens depends on the layout viewport landing above or below
Streamlit's stacking threshold — so it rendered correctly in a desktop
browser resized to 390px and wrongly on a real iPhone at the same width.
A static check does not care about any of that.
"""

import ast
import os
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_v30.py")
SOURCE = open(APP, encoding="utf-8").read()
tree = ast.parse(SOURCE)

failures = []


def expect(label, actual, wanted):
    ok = actual == wanted
    print(f"{'  ok  ' if ok else ' FAIL '} {label}")
    if not ok:
        print(f"        expected {wanted!r}, got {actual!r}")
        failures.append(label)


print("\nNo centring by empty gutter columns. Use a max-width container —")
print("`st.container(key=...)` plus CSS — which has no breakpoint to fall off.")

offenders = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
        continue
    call = node.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "columns"):
        continue
    target = node.targets[0]
    if not isinstance(target, ast.Tuple) or len(target.elts) != 3:
        continue
    names = [e.id for e in target.elts if isinstance(e, ast.Name)]
    if len(names) != 3:
        continue
    # A throwaway name on both outside columns is the signature: the
    # gutters exist only to push the middle column inward.
    if names[0].startswith("_") and names[2].startswith("_"):
        offenders.append((node.lineno, ", ".join(names)))

for lineno, names in offenders:
    print(f"        line {lineno}: {names}")
expect("no empty-gutter column centring", offenders, [])


print("\nThe auth panel is centred by a keyed container, and the CSS that")
print("centres it is actually present — one without the other is a no-op.")
expect('app uses st.container(key="tv_auth_panel")',
       'st.container(key="tv_auth_panel")' in SOURCE, True)
expect("and .st-key-tv_auth_panel is styled",
       ".st-key-tv_auth_panel {" in SOURCE, True)
expect("with a max-width", "max-width: 440px;" in SOURCE, True)


print("\nThe page must never scroll sideways. When it does, mobile Safari")
print("widens the layout viewport and every column on the page stops")
print("stacking — a layout bug that is really an overflow bug.")
expect("html/body are clipped horizontally",
       "overflow-x: clip" in SOURCE, True)


print("\nEvery phone breakpoint the design relies on is still declared.")
for width in ("640px", "768px", "420px"):
    expect(f"@media (max-width: {width})",
           f"@media (max-width: {width})" in SOURCE, True)

print("\nThe host's chrome stays removed. Streamlit paints a coloured strip,")
print("a hamburger menu, a deploy button and a 'Made with Streamlit' footer;")
print("none of them belong on a product with its own domain, and a Streamlit")
print("upgrade that renames a test id would quietly bring them back.")
for selector in ['#MainMenu', '[data-testid="stToolbar"]',
                 '[data-testid="stDecoration"]', '[data-testid="stStatusWidget"]',
                 '[data-testid="stAppDeployButton"]',
                 'a[href^="https://streamlit.io"]']:
    expect(f"hides {selector}", selector in SOURCE, True)
expect("and the header is collapsed, not merely emptied",
       'header[data-testid="stHeader"] {' in SOURCE, True)

print("\nThe browser tab carries the product's own identity.")
expect("a real page title", 'page_title="Tickveil — Market Intelligence Terminal"' in SOURCE, True)
expect("a favicon asset, not an emoji", 'assets", "favicon.png"' in SOURCE, True)
expect("with a fallback if the asset is missing",
       'if os.path.exists(_ICON) else' in SOURCE, True)

import os as _os
_assets = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets")
for _f in ("favicon.png", "apple-touch-icon.png", "favicon.ico"):
    expect(f"assets/{_f} exists", _os.path.exists(_os.path.join(_assets, _f)), True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
    sys.exit(1)
print("All layout checks passed.")
