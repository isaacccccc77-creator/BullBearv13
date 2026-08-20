# Tickveil

A market-analysis terminal built in Streamlit: price data and technical
indicators, fundamentals and risk, a transparent four-factor score, a trade
planner, a journal, watchlist scanning and news sentiment — with the method
behind every number shown alongside it.

Nothing in the app predicts prices or recommends trades. That constraint is
deliberate and it is enforced in the copy throughout: the indicator lean is a
count, the price range is a volatility band, the factor score is a snapshot.
Where a number could be mistaken for a forecast, the interface says so.

## Running it

```bash
pip install -r requirements.txt
streamlit run app_v30.py
```

Create an account on first launch. Passwords are hashed with bcrypt; TOTP
two-factor (Google Authenticator, Authy) is optional and set up under Settings.

Accounts, watchlists and journal entries are stored in JSON files next to the
app. That is reliable when running locally. On Streamlit Community Cloud's
free tier, local files are not guaranteed to survive a redeploy — for a
long-lived public deployment, move this to a hosted database.

## Files

| File | Purpose |
| --- | --- |
| `app_v30.py` | The current app. |
| `app_v22.py` | Earlier version, kept for reference. |
| `.streamlit/config.toml` | Base theme (dark, champagne primary). |
| `requirements.txt` | Dependencies. |

## The design system

v30 is a full visual rebuild. The look is a private-wealth terminal:
near-black glass surfaces, a single champagne accent, and jade/rose reserved
exclusively for direction. Three decisions carry most of it.

**Typography is a three-voice system.** Fraunces, a high-contrast optical
serif, speaks only for the brand and section titles. Inter carries all
interface text. JetBrains Mono carries every number, with tabular figures, so
digits sit in fixed columns and prices don't jitter as they tick. Pairing a
display serif with a neutral grotesque and a true tabular mono is the single
strongest signal that a data product was designed rather than assembled.

**Depth replaces borders.** Surfaces are lit by two off-screen colour sources
— a warm champagne wash from the top left, a cool jade one from the top right
— over a film-grain overlay, so the background has texture instead of being
flat black. Card edges are 1px hairlines at roughly 6% white: present, never
heavy. Shadows are long, soft and low-opacity, the way expensive light
actually behaves.

**Motion has intent.** Everything eases on one house curve
(`cubic-bezier(.16, 1, .3, 1)`) rather than linear. Content reveals in a
stagger so the page assembles instead of flashing in, metric values arrive
with a brief blur-to-focus, buttons catch a light sweep on hover, the live
status dot pulses a real expanding ring, the quote tape carries a slow
highlight, and factor bars grow from zero. All of it is pure CSS — Streamlit
strips `<script>`, and shipping a JS animation library through an iframe would
cost more than it returns.

None of this is paid for with accessibility. Text sits at or above WCAG AA
contrast on these surfaces, focus rings are explicit and gold, and the entire
motion layer switches off under `prefers-reduced-motion`.

### Charts

Every figure passes through one `style_chart()` function, so they share a
visual language rather than each carrying ad-hoc colours. The chart junk is
gone: no plot border, no vertical grid, no background fill, just faint
horizontal rules for reading values off. The price axis sits on the right
where trading platforms put it, tick labels are set in tabular mono, and
tooltips get the same dark glass and champagne hairline as the cards they sit
over.

### Notes for anyone extending this

Two implementation details are easy to trip over and worth knowing up front.

The stylesheet block **must** begin with `<style>` on its own line. Markdown
treats `<style>` as a raw-HTML block that runs to its closing tag, so the
blank lines inside it are safe — but leading it with anything else (a
`<link>`, a comment) downgrades it to an ordinary HTML block, which markdown
ends at the *first blank line*, dumping the rest of the CSS onto the page as
visible text.

Bordered containers are created through the `card()` helper rather than
`st.container(border=True)` directly. Streamlit gives them a hashed emotion
class and no stable test id — the id that used to exist was dropped in a later
release — so `card()` emits an invisible marker span and the CSS selects the
container with `:has()`. Widget selectors are likewise doubled up against both
the current react-aria markup and the older BaseWeb markup, so a Streamlit
upgrade in either direction degrades a flourish rather than the layout.

## Data

Price data, fundamentals and news come from Yahoo Finance via `yfinance` —
free and delayed, not a real-time exchange feed, and the interface says so
rather than implying otherwise. Yahoo rate-limits cloud-hosted traffic more
aggressively than home connections, so calls are cached and retried briefly
before giving up.

---

Educational tool only. Not financial advice.
