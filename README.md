# Tickveil

A market-analysis terminal built in Streamlit: price data and technical
indicators, fundamentals and risk, a transparent composite score with its own
honest backtest, a trade planner, a journal, watchlist scanning and news
sentiment — with the method behind every number shown alongside it.

Nothing in the app predicts prices or recommends trades. That constraint is
deliberate and it is enforced in the copy throughout: the indicator lean is a
count, the price range is a volatility band, the composite score is a snapshot.
Where a number could be mistaken for a forecast, the interface says so.

## Running it

```bash
pip install -r requirements.txt
streamlit run app_v30.py
```

Create an account on first launch. Passwords are hashed with bcrypt; TOTP
two-factor (Google Authenticator, Authy) is optional and set up under Settings.

Run the checks with:

```bash
python test_security.py
python test_ordering.py
python test_storage.py                      # JSON backend
python test_scoring.py
python test_support.py
TEST_DATABASE_URL=postgresql://... python test_storage.py   # both backends
```

## Storage

`storage.py` picks its backend from one environment variable:

| `DATABASE_URL` | Backend | Use for |
| --- | --- | --- |
| unset | JSON files under `user_data/` | Local development. Zero setup. |
| set | Postgres | Anything real. |

The app cannot tell the two apart — `test_storage.py` runs the same assertions
against both, because a behaviour that holds for files but not for Postgres is
a bug that would only surface in production.

**Use Postgres for any deployment with real users.** A container filesystem
does not survive a redeploy; Streamlit Community Cloud makes no guarantee about
`user_data/`. Losing someone's trade journal is not a recoverable mistake,
whether or not they paid for it. Supabase and Neon both have free tiers well
beyond what this needs. Set:

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/dbname'
```

On first start against an empty database the app lifts any existing local JSON
accounts and documents into Postgres automatically, then leaves them alone.
The migration is idempotent — accounts already in the database are skipped, so
it never clobbers newer data with a stale file.

Schema: `users` (one row per account; the `plan`, `plan_expires` and
`stripe_customer_id` columns are unused now the app is free, and are left in
place because dropping columns is the kind of migration worth avoiding until
there is a reason) and
`user_documents` (one JSONB blob per account per kind — watchlist, journal,
Telegram credentials, digest snapshot). Documents are opaque to the database,
so changing what a journal entry contains needs no schema migration.

Under the JSON backend the data directory is created `0700` and every file
written `0600`, with the mode set at creation rather than chmod-ed afterwards.

## Files

| File | Purpose |
| --- | --- |
| `app_v30.py` | The current app. |
| `app_v22.py` | Earlier version, kept for reference. |
| `test_security.py` | Input-handling boundaries: username→path, link schemes, password policy. |
| `test_ordering.py` | Guards the call-before-definition bug class described below. |
| `test_storage.py` | Same contract asserted against both storage backends, plus the migration. |
| `test_scoring.py` | Mathematical properties of the composite score, including p-value calibration. |
| `test_support.py` | Donation-link validation: https only, host-pinned, injection-proof. |
| `storage.py` | Persistence. JSON files or Postgres, chosen by `DATABASE_URL`. |
| `scoring.py` | The composite score. No Streamlit import, so the maths is testable directly. |
| `support.py` | Donation links. Validates and host-pins them; handles no money itself. |
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

## The composite score

Technical + sentiment, blended, then cut down by a macro risk haircut. It
replaced an earlier Value/Quality/Momentum/Sentiment model; the underlying
fundamental data it used is still shown on the Fundamentals tab.

Four statistical decisions carry it, and each fixes something that a
straightforward implementation gets wrong.

**The composite is re-standardised, so a threshold means one thing.** A
weighted sum of unit-variance z-scores does not itself have unit variance — it
has √(wᵀΣw), which moves with how correlated the parts happen to be. Left
alone, "+0.5" means 1.67σ when the four sub-signals are independent and 1.17σ
when they are correlated: measured firing rates of 4.7% and 12.0% of days for
the *same* number. Dividing by the composite's own rolling deviation makes one
unit one standard deviation for every ticker.

**Weights renormalise over available components.** With no headlines, weighting
technical at 0.55 and sentiment at 0.45 silently multiplies the reading by 0.55
— a 45% shrink toward neutral for a component that supplied no information.
Sentiment returns `None` rather than `0.0` when absent, because "no data" and
"balanced" are different claims.

**The backtest uses a block bootstrap, not `linregress`.** This is the big one.
Forward returns computed daily over a 10-day horizon share 9 of their 10 days
with the next observation, and the score is a 126-day rolling statistic with
autocorrelation near 0.99. Ordinary OLS inference assumes neither. Measured on
simulated data containing **no** relationship, at α = 0.05:

| Method | False-positive rate |
| --- | --- |
| `stats.linregress` | **51.7%** |
| Newey–West (lag 9) | 10.7% |
| Non-overlapping subsample | 5.3% |
| Circular block bootstrap | 6.3% |

The naive p-value calls pure noise significant about half the time. Both
numbers are displayed in the UI so the gap between them is visible.

**The macro term is a haircut, not a direction.** VIX (optionally blended with
an uploaded geopolitical-risk index) multiplies the score's magnitude, reducing
conviction in both directions rather than only penalising positive readings —
elevated volatility widens the distribution of everything, which makes any
signal less informative rather than more bearish. `geo_beta` is the maximum
haircut and is estimated per stock by regressing its returns on VIX changes.

Bands are named for what the reading *is* — "strongly positive conditions" —
never BUY or SELL. A score built from four correlated technical indicators over
a six-month window is nowhere near strong enough evidence to issue an
instruction, and the rest of the app does not issue them either.

## Donations

Tickveil is free. Every feature, for everyone, with nothing held back — the
Support tab asks plainly instead, which is more honest than degrading the free
product until people pay to undo it.

### The security model, in one line

**No payment detail ever touches this app.** There is no payment form, no card
field, no payment API key, and no money-handling code anywhere in the
repository. Every button on the Support tab is an outbound link to a checkout
page hosted by the provider on their own domain.

That is not laziness, it is the correct architecture. Taking card numbers
yourself means PCI-DSS obligations, cardholder data in your logs and backups,
fraud screening and chargeback handling. Linking out means the card is entered
on Ko-fi's or Stripe's page, the compliance burden collapses to the lightest
tier that exists, and a total compromise of this app leaks no payment data
because it never had any.

### Setting it up

Create an account with one or more providers, then set the matching variable.
Anything left unset simply doesn't appear.

| Variable | Provider | Their cut |
| --- | --- | --- |
| `SUPPORT_KOFI_URL` | Ko-fi | 0% on donations (payment processor fees still apply) |
| `SUPPORT_GITHUB_SPONSORS_URL` | GitHub Sponsors | 0% |
| `SUPPORT_STRIPE_URL` | Stripe Payment Link | ~2.9% + 30¢ |
| `SUPPORT_BMC_URL` | Buy Me a Coffee | ~5% |
| `SUPPORT_PAYPAL_URL` | PayPal.me | ~3.5% |

```bash
export SUPPORT_KOFI_URL='https://ko-fi.com/yourname'
```

On Streamlit Cloud, put the same keys in **Settings → Secrets** instead.

### Why the links are host-pinned

Each URL is checked against an allowlist of hostnames *and* required to be
`https`. Configuration arrives from environment variables and secrets files —
exactly where an attacker with partial access would try to redirect money from.
Pinning means a tampered value fails closed and the button never renders,
rather than quietly sending supporters to someone else's account.

`test_support.py` covers the spoofing attempts that matter:
`ko-fi.com.evil.tld`, `evilko-fi.com`, `ko-fi.evil.com`,
`https://user@evil.com`, `https://evil.com#ko-fi.com`, plain `http`, and any
URL carrying quotes or whitespace that could break out of the rendered
attribute. All of them produce no button.

Links also carry `rel="noopener noreferrer nofollow"` and open in a new tab, so
the destination page gets no handle on the opener window and no referrer.

### Protecting the receiving account

The app's security is the easy half. The account taking the money is the part
worth hardening:

- **A dedicated email address** for the payment account, not the one on your
  public profile or in the repo.
- **A unique password and app-based 2FA.** TOTP or a hardware key — not SMS,
  which is defeated by SIM-swapping and is the usual route into payment
  accounts.
- **Never accept payment details by email or DM.** Nobody legitimate needs to
  send you a card number, and anyone who offers is running a scam.
- **Expect phishing** styled as your provider. Log in by typing the address,
  never from a link in an email about your payouts.
- **Keep the canonical link in one place** — this repository — so supporters
  can verify where a link they were sent should actually go.
- **Never commit a payment API key.** This repo has already had one
  credentials-in-git incident; see the Security section.

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
