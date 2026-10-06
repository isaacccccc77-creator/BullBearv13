"""
Visual themes.

Five palettes the reader can switch between. This module imports no
Streamlit so the palettes can be checked for contrast directly, which
matters more than it sounds: a theme that looks striking in a mock-up and
fails WCAG on body text is worse than no choice at all.

Two rules shape every palette here.

**Directional colour never moves.** Green is up and red is down in all
five themes. The hues are retuned per background so they stay legible on
paper as well as on near-black, but a reader who learns the app in one
theme must not have to relearn it in another. Surfaces and the accent are
what change; the semantics do not.

**Overlays are a token, not a colour.** The dark themes build borders and
glass surfaces out of white at low alpha. On a light background that is
invisible, so the overlay colour is itself a variable — `--tint-rgb` —
and every border in the stylesheet is written against it. That one
indirection is what makes a genuine light theme possible rather than a
dark theme with the brightness turned up.
"""

from __future__ import annotations

# --- contrast -------------------------------------------------------

def _srgb_channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance for a #rrggbb string."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * _srgb_channel(r) + 0.7152 * _srgb_channel(g)
            + 0.0722 * _srgb_channel(b))


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio. 4.5 is the AA bar for body text, 3.0 for large."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


# --- palettes -------------------------------------------------------
#
# Each theme supplies the whole token set. Nothing falls back to another
# theme's value, because a half-defined palette fails in the one place
# nobody looks at.

THEMES: dict[str, dict] = {
    "champagne": {
        "label": "Champagne",
        "blurb": "Warm near-black and gold. The house look.",
        "is_light": False,
        "ink_950": "#05070B", "ink_900": "#080A10", "ink_850": "#0B0E15",
        "ink_800": "#0E1219", "ink_700": "#141924",
        "accent_300": "#F2E2C1", "accent_400": "#E4CB9E",
        "accent_500": "#D4B078", "accent_600": "#B08B52",
        "accent_grad": ("linear-gradient(135deg, #F6E9CE 0%, #E4CB9E 38%, "
                        "#C39C61 72%, #A37F4A 100%)"),
        "jade": "#5FCF9B", "rose": "#F0616F", "azure": "#8FB8E8",
        "text_100": "#F4F1EA", "text_200": "#DAD5CA",
        "text_300": "#ABA598", "text_500": "#7E786C",
        "tint_rgb": "255, 255, 255", "shade_rgb": "0, 0, 0",
        "glow": "0.35",
    },
    "graphite": {
        "label": "Graphite",
        "blurb": "Cool charcoal and platinum. Quietest of the five.",
        "is_light": False,
        "ink_950": "#06080A", "ink_900": "#0A0C0F", "ink_850": "#0E1114",
        "ink_800": "#12161A", "ink_700": "#1A1F25",
        "accent_300": "#E8EDF2", "accent_400": "#C7D2DC",
        "accent_500": "#9FADBA", "accent_600": "#75838F",
        "accent_grad": ("linear-gradient(135deg, #EFF3F7 0%, #C7D2DC 38%, "
                        "#92A0AC 72%, #6E7B86 100%)"),
        "jade": "#5CC79A", "rose": "#EC6475", "azure": "#8FB8E8",
        "text_100": "#F2F5F8", "text_200": "#D2D9E0",
        "text_300": "#A2ACB6", "text_500": "#76808A",
        "tint_rgb": "255, 255, 255", "shade_rgb": "0, 0, 0",
        "glow": "0.28",
    },
    "midnight": {
        "label": "Midnight",
        "blurb": "Deep navy and cold azure. Reads well at night.",
        "is_light": False,
        "ink_950": "#04060D", "ink_900": "#070A14", "ink_850": "#0A0E1B",
        "ink_800": "#0D1222", "ink_700": "#141B30",
        "accent_300": "#CFE2FF", "accent_400": "#A6C8F5",
        "accent_500": "#7AA7E6", "accent_600": "#5480B8",
        "accent_grad": ("linear-gradient(135deg, #DCEBFF 0%, #A6C8F5 38%, "
                        "#6E9BDB 72%, #4A74A8 100%)"),
        "jade": "#56CFA4", "rose": "#F2697C", "azure": "#9CC4F0",
        "text_100": "#EEF3FA", "text_200": "#CBD6E6",
        "text_300": "#9BA8BE", "text_500": "#6E7B91",
        "tint_rgb": "255, 255, 255", "shade_rgb": "0, 0, 0",
        "glow": "0.32",
    },
    "emerald": {
        "label": "Emerald",
        "blurb": "Green-black and aged brass.",
        "is_light": False,
        "ink_950": "#030806", "ink_900": "#060C09", "ink_850": "#09110D",
        "ink_800": "#0C1612", "ink_700": "#13201A",
        "accent_300": "#EBD9AE", "accent_400": "#D4BC87",
        "accent_500": "#B89B5E", "accent_600": "#8E7643",
        "accent_grad": ("linear-gradient(135deg, #F0E2BE 0%, #D4BC87 38%, "
                        "#A98C52 72%, #80693B 100%)"),
        "jade": "#5ED6A0", "rose": "#EE6A76", "azure": "#8FB8E8",
        "text_100": "#F1F4EF", "text_200": "#D3DACE",
        "text_300": "#A3AC9E", "text_500": "#767E72",
        "tint_rgb": "255, 255, 255", "shade_rgb": "0, 0, 0",
        "glow": "0.30",
    },
    "ivory": {
        "label": "Ivory",
        "blurb": "Warm paper and ink. Built for daylight and for printing.",
        "is_light": True,
        # On a light theme the "ink" scale runs the other way: 950 is the
        # page and 700 is the raised surface, so every rule in the
        # stylesheet that layers 950 under 800 still layers correctly.
        "ink_950": "#F8F5EF", "ink_900": "#FCFAF6", "ink_850": "#FFFFFF",
        "ink_800": "#FFFFFF", "ink_700": "#F2EDE3",
        "accent_300": "#8A6A32", "accent_400": "#7A5C2A",
        "accent_500": "#6B5024", "accent_600": "#543F1C",
        "accent_grad": ("linear-gradient(135deg, #C9A868 0%, #A8823F 38%, "
                        "#8A6A32 72%, #6B5024 100%)"),
        # Retuned for contrast on paper: the dark-theme jade is 1.6:1 on
        # white, which is unreadable. Same meaning, legible hue.
        "jade": "#15724A", "rose": "#B02A38", "azure": "#2C5F96",
        "text_100": "#161410", "text_200": "#33302A",
        "text_300": "#5C564B", "text_500": "#736C5C",
        "tint_rgb": "26, 22, 16", "shade_rgb": "26, 22, 16",
        "glow": "0.18",
    },
}

DEFAULT_THEME = "champagne"


def theme_names() -> list[str]:
    return list(THEMES)


def get_theme(name: str | None) -> dict:
    """Always returns a usable palette; an unknown name falls back."""
    return THEMES.get(name or "", THEMES[DEFAULT_THEME])


def resolve(name: str | None) -> str:
    return name if name in THEMES else DEFAULT_THEME


def css_variables(name: str | None) -> str:
    """The `:root` block for one theme, ready to drop into a <style>."""
    t = get_theme(name)
    shadow_alpha = "0.7" if not t["is_light"] else "0.10"
    shadow_md_alpha = "0.95" if not t["is_light"] else "0.16"
    return f""":root {{
    --ink-950: {t['ink_950']};
    --ink-900: {t['ink_900']};
    --ink-850: {t['ink_850']};
    --ink-800: {t['ink_800']};
    --ink-700: {t['ink_700']};

    --gold-300: {t['accent_300']};
    --gold-400: {t['accent_400']};
    --gold-500: {t['accent_500']};
    --gold-600: {t['accent_600']};
    --gold-grad: {t['accent_grad']};

    --jade: {t['jade']};
    --jade-dim: rgba({_rgb(t['jade'])}, 0.14);
    --rose: {t['rose']};
    --rose-dim: rgba({_rgb(t['rose'])}, 0.14);
    --azure: {t['azure']};

    --text-100: {t['text_100']};
    --text-200: {t['text_200']};
    --text-300: {t['text_300']};
    --text-500: {t['text_500']};

    /* The overlay colour every border and glass surface is built from.
       White on the dark themes, ink on the light one. */
    --tint-rgb: {t['tint_rgb']};
    --shade-rgb: {t['shade_rgb']};

    --line: rgba(var(--tint-rgb), 0.065);
    --line-strong: rgba(var(--tint-rgb), 0.11);
    --line-gold: rgba({_rgb(t['accent_500'])}, 0.28);

    --shadow-sm: 0 2px 10px -4px rgba(var(--shade-rgb), {shadow_alpha});
    --shadow-md: 0 18px 44px -24px rgba(var(--shade-rgb), {shadow_md_alpha});
    --shadow-gold: 0 16px 40px -18px rgba({_rgb(t['accent_500'])}, {t['glow']});

    --ease: cubic-bezier(0.16, 1, 0.3, 1);
    --radius: 16px;
    --radius-sm: 11px;

    --font-display: 'Fraunces', 'Iowan Old Style', Georgia, serif;
    --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
}}"""


def _rgba(hex_colour: str, alpha: float) -> str:
    return f"rgba({_rgb(hex_colour)}, {alpha})"


def _rgb(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return ", ".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def chart_palette(name: str | None) -> dict:
    """
    Plotly colours for one theme.

    Charts cannot read CSS variables — Plotly serialises real colour
    strings into the figure — so the palette is handed over as literals
    and every figure is built from these rather than from hex typed at
    the call site.
    """
    t = get_theme(name)
    tint = t["tint_rgb"]
    return {
        "gold": t["accent_500"],
        "gold_soft": t["accent_400"],
        "jade": t["jade"],
        "rose": t["rose"],
        "azure": t["azure"],
        "text": t["text_300"],
        "muted": t["text_500"],
        "strong": t["text_100"],
        "grid": f"rgba({tint}, {0.055 if not t['is_light'] else 0.10})",
        "axis": f"rgba({tint}, {0.09 if not t['is_light'] else 0.18})",
        "paper": "rgba(0,0,0,0)",
        # Plotly hover cards and marker outlines sit ON the page, so they
        # need the page colour as a literal rather than a transparency.
        "hover_bg": _rgba(t["ink_850"], 0.95),
        "is_light": t["is_light"],
        "tint_rgb": tint,
    }


def tint(name: str | None, alpha: float) -> str:
    """An overlay colour at a given alpha, for Plotly marks."""
    return f"rgba({get_theme(name)['tint_rgb']}, {alpha})"


def contrast_report(name: str) -> list[tuple[str, str, float]]:
    """Body-text contrast for one theme, against its own page colour."""
    t = get_theme(name)
    bg = t["ink_950"]
    return [(label, t[key], contrast_ratio(t[key], bg)) for label, key in [
        ("primary text", "text_100"), ("secondary text", "text_200"),
        ("muted text", "text_300"), ("faint text", "text_500"),
        ("accent", "accent_500"), ("up", "jade"), ("down", "rose"),
    ]]
