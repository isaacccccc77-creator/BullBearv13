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

Accounts and per-user data are written to `user_data/`, created `0700`, with
each file written `0600`. That is reliable when running locally. On Streamlit
Community Cloud's free tier, local files are not guaranteed to survive a
redeploy — for a long-lived public deployment, move this to a hosted database.

Run the checks with:

```bash
python test_security.py
python test_ordering.py
```

## Files

| File | Purpose |
| --- | --- |
| `app_v30.py` | The current app. |
| `app_v22.py` | Earlier version, kept for reference. |
| `test_security.py` | Input-handling boundaries: username→path, link schemes, password policy. |
| `test_ordering.py` | Guards the call-before-definition bug class described below. |
| `.streamlit/config.toml` | Base theme (dark, champagne primary). |
| `requirements.txt` | Dependencies. |

## What makes it different from a Bloomberg terminal

The density and authority of a professional terminal are worth copying. The
hostility to newcomers is not — and that hostility isn't a side effect of the
density, it's a separate choice. So this keeps the dense readout and adds the
part that terminals leave out.

**Explain mode**, on by default, adds a plain-English note under every dense
panel: what the number means, and what it does *not* mean. The indicator lean
explains that it's a count rather than a forecast. The statistical range
explains that it assumes no direction at all. The news tone explains that it
reads wording, not substance. One toggle collapses all of it back to the terse
readout for someone who doesn't need it. Someone who found it noisy will turn
it off in one click; someone who needed it would never have known to turn it
on, which is why the default runs that way round.

**The market tape** across the top carries the usual index levels — and under
each symbol, what it actually is. `^VIX` is labelled "Fear gauge", `^TNX` is
"Bond yield". That one line of text is the difference between a strip a
professional scans and a strip anyone can read.

**The status bar** along the bottom reports only things that are true: the
data source and its delay, the ticker currently loaded, whether explain mode
is on, and the render time. No fake "LIVE" indicator over a 15-minute-delayed
free feed.

## Navigation

The command bar is **global** — it sits above the tab rail, not inside a tab.
That's load-bearing, not cosmetic. It used to live inside Analysis, which
meant Fundamentals and Factor Score showed "enter a ticker in the command bar
above" while pointing at a control on a different tab, and each tool tab
carried its own ticker field, so you could analyse AAPL and plan a trade on
MSFT with nothing flagging the mismatch. One instrument, set in one place,
read by every tab; tabs that follow it say so with a context chip.

Tabs are ordered by kind: instrument-scoped views (Analysis, Fundamentals,
Factor Score), then tools (Trade Setup, Journal, Watchlist, Daily Digest),
then reference (Multi-Asset, Calendar, Settings). Market News was removed as a
top-level tab — it held one collapsed accordion on an otherwise empty screen —
and now sits under the ticker-specific news in Analysis.

## Mobile

The phone layout is a designed target, not a shrunken desktop. Three things
break a Streamlit dashboard on a small screen, and each is fixed rather than
hidden:

- **`st.columns` does not stack.** Four metrics in a row become four
  unreadable slivers. Below the breakpoint the row is allowed to wrap and each
  column takes a minimum width, so a four-metric row lands as a tidy 2×2 — and
  a single column below 420px.
- **Eleven tabs wrap into four stacked rows** that push content off screen.
  The rail becomes one horizontally-scrolling line with scroll snapping, an
  inset that clears Streamlit's own scroll arrows, and a fade at the right edge
  to signal there's more.
- **The sidebar was the worst offender.** Streamlit renders it as an overlay
  that squeezed the real content into a ~150px column until dismissed. It only
  ever held the signed-in name and a logout button, so it's gone: the name is
  in the masthead and logout moved to Settings beside the other account
  controls.

Beyond that: tap targets meet the 44px minimum, inputs use 16px text so iOS
doesn't zoom on focus, hover-lift transforms are dropped (a card that lifts
under a finger looks like a rendering glitch), the quote tape becomes a
two-column grid, verdict panels stack, and the status bar leaves the viewport
to sit in the flow. Wide content scrolls inside its own container — the page
body never scrolls sideways at any width.

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

**Define before you call.** Streamlit re-executes the whole script top to
bottom on every interaction, so a helper defined *below* the code that calls it
raises `NameError` the moment a user touches that control — while passing
imports, linters and type checkers, because the name does exist by the time the
file finishes loading. This shipped twice here: the Settings tab called
`send_telegram_message` ~1,700 lines before its definition, so that button had
never worked. `test_ordering.py` now walks module-level statements in source
order and fails on any call to a function defined later.

Related: anything expensive at module scope runs on *every* interaction, not
once. A bcrypt hash at module level added ~270ms to every click in the app; it
lives behind `@st.cache_resource` now.

Bordered containers are created through the `card()` helper rather than
`st.container(border=True)` directly. Streamlit gives them a hashed emotion
class and no stable test id — the id that used to exist was dropped in a later
release — so `card()` emits an invisible marker span and the CSS selects the
container with `:has()`. Widget selectors are likewise doubled up against both
the current react-aria markup and the older BaseWeb markup, so a Streamlit
upgrade in either direction degrades a flourish rather than the layout.

## Security

Fixed in this pass, in rough order of severity:

**Path traversal through usernames.** Usernames are interpolated into per-user
filenames (`bullbear_journal_<username>.json`). Nothing validated them, so a
username like `../../etc/cron.d/x` directed a write outside the app directory.
Now: a character whitelist on registration, plus an independent
`realpath`-based confinement check on every single path. Two checks rather
than one, because the regex only guards accounts created after it existed.

**Credentials committed to the repository.** `bullbear_users.json` — password
hashes and TOTP secrets — was tracked in git. It's now untracked and ignored.
*It remains in git history, so the password hashed there should be treated as
disclosed and changed.*

**Secrets written world-readable.** The Telegram bot token was stored in
plaintext JSON at default permissions. All persisted files are now created
`0600` via `os.open` with the mode set *at creation* — chmod-ing afterwards
leaves a window where another local account can read the file.

**Unlimited password guessing.** No throttle existed. Now five failures per
account trigger a five-minute lockout.

**Username enumeration by timing.** An unknown username returned instantly
while a wrong password took ~270ms of bcrypt, which is trivially measurable.
Sign-in now always runs bcrypt, against a dummy hash when the account doesn't
exist, and both failures return the same message.

**Script injection via news links.** Headline URLs from the upstream feed were
rendered straight into markdown links, so a `javascript:` URL would have become
a clickable script. Only `http`/`https` are accepted now, and headline text has
its markdown delimiters escaped so a title containing `]` can't close its own
link and inject markup after it.

**Weak password floor.** Raised from 8 characters to 10, plus rejection of
single-character repeats and keyboard runs — without the symbol-and-digit
theatre that pushes people toward `Password1!`.

Every one of these is covered by `test_security.py`.

## Data

Price data, fundamentals and news come from Yahoo Finance via `yfinance` —
free and delayed, not a real-time exchange feed, and the interface says so
rather than implying otherwise. Yahoo rate-limits cloud-hosted traffic more
aggressively than home connections, so calls are cached and retried briefly
before giving up.

---

Educational tool only. Not financial advice.
