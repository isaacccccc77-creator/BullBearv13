# Launching Tickveil on your own domain

Written in the order you have to do it. Each step says what it costs and
what it blocks, so you can stop partway and still have something working.

---

## 0. The thing that decides everything else

**Streamlit Community Cloud does not serve custom domains.** Your app
lives at `something.streamlit.app` and there is no setting to change
that. A CNAME pointed at it will not work, because the platform routes on
the hostname it issued.

I could not re-verify this from inside the sandbox — `docs.streamlit.io`
is blocked here — so check it yourself before acting on it: open your app
in Streamlit Cloud, go to **Settings → General**, and look for any domain
field. If there is none, the rest of this section applies. It has been
true for as long as the platform has existed, but it is the kind of thing
that changes, and it is cheap for you to confirm in ten seconds.

So "launch on a domain" means **moving off Community Cloud**. That is a
good move anyway: Community Cloud sleeps idle apps, which for a visitor
means a cold start of 30 seconds or more on a link you just shared.

### Where to move

| Host | Cost to start | Custom domain | Sleeps? | Notes |
|---|---|---|---|---|
| **Render** (Web Service) | free tier, ~$7/mo for always-on | yes, free TLS | free tier does | Simplest move. Point it at the repo, set the start command. |
| **Railway** | usage-based, ~$5/mo | yes, free TLS | no | Easiest Postgres story — database and app in one project. |
| **Fly.io** | ~$3–5/mo | yes, free TLS | configurable | Closest region to Singapore (`sin`), which matters for latency. |
| **Google Cloud Run** | pay-per-request, often <$5/mo | yes, via load balancer | scales to zero | You mentioned Google. Most fiddly of the four; the domain mapping goes through Cloud Run → Custom Domains. |

For a first launch from Singapore I would pick **Fly.io** (`sin` region)
or **Railway**. Render is the least work if you want it running in an hour.

### The start command, whichever you pick

```
streamlit run app_v30.py --server.port $PORT --server.address 0.0.0.0 \
  --server.headless true --server.enableCORS false \
  --server.enableXsrfProtection true
```

`$PORT` is supplied by the platform. Do not hardcode 8501.

---

## 1. The domain itself

Buy it anywhere — Cloudflare Registrar sells at cost and is the cheapest
honest option; Namecheap and Porkbun are fine. Google Domains no longer
exists as such; it was sold to Squarespace in 2023, so "buy a domain on
Google" now means Cloud Domains, which is priced for businesses. Do not
feel obliged to buy from Google just because you are deploying there.

`.com` runs about US$10–15/year. Budget for the renewal, not the
first-year promo price.

**DNS**, once you have it:

1. In your host's dashboard, add the custom domain. It gives you a target
   — either a `CNAME` value or an `A` record IP.
2. At your registrar, create that record. Use `@` for the apex
   (`tickveil.com`) and `www` for the subdomain, pointing both at the
   host.
3. TLS is automatic and free on all four hosts above. Do not buy a
   certificate.
4. Propagation is usually minutes, occasionally an hour.

**Put Cloudflare in front of it** (free plan). You get DDoS absorption, a
WAF, and caching, none of which you have today. Set SSL/TLS mode to
**Full (strict)**, not Flexible — Flexible leaves the leg between
Cloudflare and your host unencrypted, which defeats the point.

---

## 2. Postgres, before you launch, not after

Right now accounts live in JSON files on the container's disk. **Every
host above wipes that disk on redeploy.** The first time you push a fix,
every account, watchlist and journal entry is gone, with no error and
nothing to restore.

The app already supports Postgres — set `DATABASE_URL` and it switches
backends with no code change. Free tiers that are genuinely enough:
**Neon**, **Supabase**, or your host's own add-on.

There is a migration helper in `storage.py`
(`migrate_json_to_postgres`) if you want to carry existing accounts over.

---

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

## 6. Security checklist before you point a domain at it

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

Worth adding soon, not blocking:

- [ ] Idle session timeout
- [ ] Password reset by email (needs section 4 first)
- [ ] A login audit trail the account owner can see
- [ ] `app_v22.py` deleted — it is kept "for reference" and still contains
      the call-before-definition bug that `test_ordering.py` exists to catch

---

## 7. Order I would actually do it in

1. Postgres (an afternoon) — everything else is pointless without it
2. Move to Fly.io or Railway (an afternoon)
3. Buy the domain, point DNS, add Cloudflare (an hour)
4. Google sign-in (fifteen minutes)
5. Twelve Data or Finnhub instead of yfinance (a day)
6. Email via Resend, then password reset (half a day)
7. Apple sign-in — only if people ask
