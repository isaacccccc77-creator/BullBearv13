# Launching Tickveil on your own domain

Written in the order you have to do it. Each step says what it costs and
what it blocks, so you can stop partway and still have something working.

---

## 0. "Can it not be under Streamlit?"

Three different questions hide in that one, with three different answers.

### a) Not on a `*.streamlit.app` address — yes, and it is the main job here

**Streamlit Community Cloud does not serve custom domains.** There is no
setting; the platform routes on the hostname it issued, so a CNAME
pointed at it will not work.

I could not re-verify that from inside the sandbox (`docs.streamlit.io`
is blocked here), so confirm it in ten seconds before acting: Streamlit
Cloud → your app → **Settings → General**, and look for a domain field.
If there is none, this section applies.

So putting Tickveil on `tickveil.com` means **moving host**. That is a
good move regardless: Community Cloud sleeps idle apps, so a link you
share cold-starts for 30 seconds or more.

There is now a `Dockerfile` in the repo, which means the host is a
decision you can change later without touching the app. `fly.toml` and
`render.yaml` are also committed and preconfigured.

### b) Not *looking* like a Streamlit app — done

Streamlit paints several things that announce the framework: a coloured
gradient strip across the very top, a hamburger menu, a "Manage app"
pill, a running-status widget, a deploy button and a "Made with
Streamlit" footer. All of them are now removed, and the browser tab
carries a real favicon drawn from the brand mark instead of an emoji.

Verified in a browser: zero Streamlit chrome elements in the DOM, and
the word "streamlit" does not appear anywhere in the visible text of the
page.

The one thing you cannot remove is the websocket under the hood — if
someone opens devtools they will see `/_stcore/stream`. No visitor does
that, and no amount of CSS changes it.

### c) Not *being* Streamlit at all — possible, but a rewrite

If you mean a conventional web app (React or similar front end, FastAPI
or Django behind it), that is a genuine rewrite of roughly 8,000 lines of
UI. The models would survive untouched — `scoring.py`, `quant.py`,
`deals.py`, `payments.py`, `theming.py` and `identity.py` all import no
Streamlit, which was deliberate — but every screen would be rebuilt.

Worth it eventually if the product grows. Not worth it to launch. What
you would gain is finer control over layout and a faster first paint;
what you would lose is weeks. With the chrome gone, nobody visiting
`tickveil.com` will know or care.

### Where to move (a)

| Host | Cost to start | Custom domain | Sleeps? | Notes |
|---|---|---|---|---|
| **Fly.io** | ~$3–5/mo | yes, free TLS | configurable | `sin` region is Singapore — the biggest latency win available, and it is one line in `fly.toml`. |
| **Railway** | ~$5/mo usage | yes, free TLS | no | Easiest Postgres story: app and database in one project. |
| **Render** | free, ~$7/mo always-on | yes, free TLS | free tier does | Least work if you want it live in an hour. `render.yaml` is committed. |
| **Google Cloud Run** | pay-per-request | yes, via load balancer | scales to zero | Most fiddly; domain mapping goes through a load balancer. |

For a Singapore audience I would pick **Fly.io**. `fly launch --no-deploy`
then `fly deploy` and it reads `fly.toml`.

## 1. The domain itself

Buy it anywhere — Cloudflare Registrar sells at cost and is the cheapest
honest option; Namecheap and Porkbun are fine. Google Domains no longer
exists as such; it was sold to Squarespace in 2023, so "buy a domain on
Google" now means Cloud Domains, which is priced for businesses. Do not
feel obliged to buy from Google just because you are deploying there.

`.com` runs about US$10–15/year. Budget for the renewal, not the
first-year promo price.

**DNS at Namecheap**, step by step:

1. In your host's dashboard, add the custom domain. It gives you a target
   — on Fly it is usually an `A` record IP plus an `AAAA`; on Render and
   Railway it is a `CNAME` hostname.
2. Namecheap → **Domain List** → *Manage* → **Advanced DNS**.
3. Delete the two parking records Namecheap adds by default (a `CNAME`
   for `www` pointing at `parkingpage.namecheap.com`, and a `URL
   Redirect`). Leaving them in place is the most common reason a new
   domain keeps showing a parking page after everything else is right.
4. Add your host's records:
   - Host `@` → the apex (`tickveil.com`)
   - Host `www` → the same target
   Namecheap writes `@` for the apex; you do not type the domain itself.
5. Set TTL to **Automatic** while you are setting up — a long TTL means a
   mistake takes hours to correct.
6. Back in your host's dashboard, click *verify* / *check DNS*. TLS is
   issued automatically and free. **Do not buy an SSL certificate from
   Namecheap** — you do not need one, and the upsell is prominent.

Propagation is usually minutes. `dig tickveil.com +short` tells you what
the world currently sees.

**Put Cloudflare in front of it** (free plan). You get DDoS absorption, a
WAF, and caching, none of which you have today. Set SSL/TLS mode to
**Full (strict)**, not Flexible — Flexible leaves the leg between
Cloudflare and your host unencrypted, which defeats the point.

---

## 2. Postgres, before you launch, not after

Right now accounts live in JSON files on the container's disk. **Every
host above replaces that disk on redeploy.** The first time you push a
fix, every account, watchlist and journal entry is gone, with no error
and nothing to restore.

The app already supports Postgres: set `DATABASE_URL` and it switches
backend with no code change. This was tested properly rather than
assumed — against a real PostgreSQL 16 server, the app creates accounts,
refuses duplicates, round-trips every document kind, and signs a user in
through the browser on the Postgres backend with no JSON files present.

**Migrating existing accounts.** `storage.migrate_json_to_postgres`
copies them over. Also tested: two accounts with watchlists, preferences
and a journal moved across with bcrypt hashes and TOTP flags intact, and
running it a second time moved nothing and skipped both — it is
idempotent, so it is safe to leave wired into startup.

```python
import os, storage
pg = storage.PostgresStorage(os.environ["DATABASE_URL"])
pg.ensure_schema()
print(storage.migrate_json_to_postgres(pg))
```

Free tiers that are genuinely enough to launch on: **Neon**, **Supabase**,
or your host's own add-on. Append `?sslmode=require` to the URL.

## 3. Sign in with Google

See `.streamlit/secrets.toml.example` — the steps are written out there.
Free, about fifteen minutes, and the only thing that can go wrong is the
redirect URI not matching exactly.

One decision worth making deliberately: once Google sign-in exists,
**email 2FA is largely redundant for Google users.** Google has already
done the second factor, usually better than an emailed code. Keep TOTP
for password accounts, offer Google as the easy path, and do not send
codes to people who signed in with Google.

### Sign in with Apple — read before committing

It works through the same code path, but the real cost is not the code:

* **US$99/year** for an Apple Developer account. There is no free tier.
* The `client_secret` is **a JWT you generate and sign yourself**, and it
  **expires in at most six months**. When it expires, Apple sign-in stops
  working — silently, for everyone, with no warning email. You need a
  calendar reminder and a rotation procedure.
* Apple sends the user's name **only on the very first authorisation**,
  never again. Miss it and you have an account with no display name
  forever. (`identity.display_name` already falls back gracefully.)

My honest read: ship Google first. Add Apple when enough people ask that
the $99 and the rotation chore are worth it — not before.

---

## 4. Email

Gmail + an App Password works and costs nothing, limits at ~500/day.
Details in the secrets example.

The real constraint is **deliverability**: mail sent as `you@gmail.com`
from a server that is not Google will land in spam. Two options:

* **Short term:** send *through* Gmail's SMTP (which the config does), so
  it is genuinely from Google's servers. Fine up to a few hundred a day.
* **Properly:** a transactional provider — Resend, Postmark, Brevo — on
  your own domain, with SPF, DKIM and DMARC records. Resend's free tier is
  3,000/month. Do this once you own the domain; it is a 20-minute job and
  it is the difference between codes arriving and codes vanishing.

---

## 5. Data accuracy — the part that needs a real decision

This is the weakest link in the product right now, and it is worth being
blunt about it.

`yfinance` is **not an API**. It is an unofficial scraper of Yahoo
Finance's internal endpoints. Concretely:

* **It breaks without notice** when Yahoo changes their internals. It has
  happened repeatedly; it will happen again, probably on a day you are
  not watching.
* **It is rate-limited and will throttle you.** One user is fine. Fifty
  concurrent users on one server IP is not.
* **Yahoo's terms do not permit redistribution**, which is what a public
  website does. Free and personal is a grey area most people ignore. A
  public site on your own domain, with a donate button, is a long way
  further into that grey than a localhost script.
* **Fundamentals are patchy and sometimes wrong**, especially outside the
  US and for financial companies, whose chart of accounts does not match
  the fields the Deal Room reads.

If Tickveil is a portfolio piece, this is acceptable — say "data via
Yahoo Finance, delayed" and move on. If it is a public product on a
domain you own, budget for a real feed:

| Provider | Free tier | Paid entry | Good for |
|---|---|---|---|
| **Alpha Vantage** | 25 req/day | ~$50/mo | Easy swap, generous paid tier |
| **Finnhub** | 60 req/min | ~$50/mo | Good fundamentals + news |
| **Twelve Data** | 800 req/day | ~$30/mo | Best free tier of the three |
| **Polygon.io** | limited | ~$30/mo | Best US coverage and quality |

**Twelve Data's free tier is the one to try first** — 800 requests a day
is enough for a real but small user base, and swapping it in means
rewriting only the fetchers at the top of `app_v30.py`, not the models.

Whatever you pick: keep the `@st.cache_data` TTLs. They are what stands
between you and a rate limit.

---

## 6. Run the preflight check

```bash
DATABASE_URL="postgresql://..." python preflight.py
```

It answers one question — *if I point a domain at this right now, what
breaks?* — and is meant to be run against the live deployment's
environment, not your laptop's, because the configuration that matters is
the one the running container has.

Exit 0 means nothing is blocking. Exit 1 means at least one BLOCKER:
something that loses user data or leaves the app insecure. Right now, with
no database configured, it reports exactly one blocker, which is the
Postgres item above.

It checks dependencies, the database connection, that no secret or user
data is tracked by git, that `cookie_secret` is real randomness rather
than the placeholder, that the OAuth redirect is https, that the PayNow
payee actually produces a valid QR, and that all eight static test suites
pass.

## 7. Security checklist before you point a domain at it

Already done:

- [x] bcrypt password hashing, with a dummy-hash comparison so a bad
      username and a bad password take the same time
- [x] Login rate limiting and temporary lockout
- [x] Optional TOTP two-factor
- [x] Username validation plus independent path confinement
- [x] Link scheme allowlisting and markdown escaping on anything from a feed
- [x] HTML escaping at the boundary where untrusted text enters the memo
- [x] Host-pinned donation links, so a tampered config fails closed
- [x] No card data anywhere in the system

Do before launch:

- [ ] **Postgres** (section 2) — the highest-priority item on this page
- [ ] **Run `python preflight.py` against the deployed environment**
- [ ] **Change the `Isaac77` password.** Its bcrypt hash is in git history
      at commit `319affb`. Rewriting history will not reliably remove it
      from forks or caches; changing the password is what actually fixes it.
- [ ] **`cookie_secret`** set to real randomness and never rotated casually
- [ ] **Cloudflare in front**, SSL mode Full (strict)
- [ ] **Keep XSRF protection on** (it is on by default; the start command
      above sets it explicitly so nobody turns it off by accident)
- [ ] **A real privacy notice**, once you hold emails. Collecting an email
      address in Singapore brings you under the PDPA: you need a stated
      purpose, a way to ask for deletion, and reasonable security. The
      third you have; the first two are a page of text.
- [ ] **Decide the data story** (section 5) before you advertise accuracy

Done since this guide was first written:

- [x] Streamlit's own chrome removed, real favicon, own page title
- [x] `Dockerfile`, `fly.toml`, `render.yaml` — host is now swappable
- [x] Postgres backend and the JSON migration both tested end to end
- [x] `preflight.py` to check a deployment before pointing DNS at it
- [x] Container runs as a non-root user

Worth adding soon, not blocking:

- [ ] Idle session timeout
- [ ] Password reset by email (needs section 4 first)
- [ ] A login audit trail the account owner can see
- [ ] `app_v22.py` deleted — it is kept "for reference" and still contains
      the call-before-definition bug that `test_ordering.py` exists to catch

---

## 8. Order I would actually do it in

1. **Postgres** (an hour) — nothing else matters if accounts vanish.
   Create a Neon database, copy the URL.
2. **Deploy the container** (an hour). `fly launch --no-deploy`, set
   `DATABASE_URL` with `fly secrets set`, `fly deploy`.
3. **`python preflight.py`** against it. Fix anything it calls a blocker.
4. **Buy the domain at Namecheap, point DNS, add Cloudflare** (an hour).
5. **Google sign-in** (fifteen minutes) — now that you have a real
   redirect URI to register.
6. **Change the `Isaac77` password.**
7. **Twelve Data or Finnhub instead of yfinance** (a day). This is the
   one that decides whether the product is honest about its numbers.
8. **Email via Resend, then password reset** (half a day).
9. **Apple sign-in** — only if people ask.

Steps 1–5 are a weekend. Step 7 is the one worth doing properly.
