# Launching Tickveil — the simple version

`DEPLOY.md` is the detailed reference. This is the one to actually follow.
One path, in order, no choices to make.

---

## First: the thing that makes all of this confusing

**Namecheap does not run your website. It only sells the name.**

That is the whole confusion, and it is a completely reasonable one to
have, because nothing on their site makes it obvious.

Think of it as two separate purchases:

| | What you buy | Who from | Roughly |
|---|---|---|---|
| **The name** | `tickveil.com` — just the address | Namecheap | ~$10–15/year |
| **The computer** | A machine that actually runs your app, 24/7 | Render (or Fly, Railway) | ~$7/month |

A useful way to picture it:

```
   Namecheap                 Render
   ---------                 ------
   The sign with your        The actual shop where
   shop's name on it         the work happens
        │                          ▲
        │   "when someone asks     │
        └──  for tickveil.com,  ───┘
             send them here"
                 (this is DNS)
```

Namecheap's only job is that arrow. You tell it "tickveil.com means
*that* computer over there", and it tells the rest of the internet.

**You cannot upload your app to Namecheap.** They do sell a hosting
product, but it is the old kind — for WordPress and PHP sites — and it
will not run this. Ignore it.

---

## Your questions, answered

### "Do I need a specific domain like .io or .to?"

**No. The ending makes zero difference to the code.** `.com`, `.io`,
`.app`, `.sg`, `.xyz` — all identical as far as Tickveil is concerned.
Pick on price and how it sounds.

Two small notes:

* `.app` and `.dev` force HTTPS. You have HTTPS anyway, so this costs you
  nothing — it is just a thing people mention.
* Avoid the very cheap unusual endings (`.top`, `.click`, `.rest`). They
  get used for spam, so some corporate mail filters distrust them. `.com`
  is boring and never a problem.

Check the renewal price, not the first-year price. A $1 first year that
renews at $40 is common.

### "Is the code only for Streamlit hosting, or does it work with Namecheap now?"

Slightly wrong question, but the answer is good news.

Namecheap never hosts it — see above. What changed is that **the code is
no longer tied to Streamlit's hosting.** There is now a `Dockerfile` in
the repo, which is a recipe any modern host can follow. So it runs on
Render, Fly, Railway, Google Cloud Run, or your own server.

Before this week it was effectively "put it on Streamlit Cloud and hope".
Now the host is a choice you can change any time without touching the
app.

And it no longer *looks* like a Streamlit app either — the toolbar,
the "Manage app" button and the "Made with Streamlit" footer are gone,
and the browser tab shows your own icon.

### "Are there YouTube videos?"

Probably, but **I cannot browse the web from here, so I am not going to
invent video titles or links** — you would just end up at dead URLs.

Search these phrases instead:

* `deploy docker app to render` — the main one
* `connect namecheap domain to render` — the DNS half
* `namecheap custom domain DNS setup` — generic, lots of these exist
* `neon postgres getting started` — for the database step

One warning that will save you real frustration: **these tutorials go
stale fast.** Render, Namecheap and Neon all redesign their dashboards
every year or so. If a video says "click the blue Settings button" and
you see a different layout, the video is old — the *concept* is still
right, the button has just moved. Follow the written steps below for the
order, and use videos to see what the screens look like roughly.

---

## The actual steps

Budget about three hours, most of it waiting. Do them in order. Steps 1
and 2 are free.

### Step 1 — The database (free, ~20 min)

**Why first:** without it, every account, watchlist and journal entry is
wiped every single time you update the app. No error message, no warning.
This is not optional.

1. Go to **neon.tech**, sign up with GitHub.
2. Create a project. Call it `tickveil`. Pick the region closest to
   Singapore it offers.
3. It shows you a **connection string** that looks like
   `postgresql://user:password@ep-something.aws.neon.tech/neondb?sslmode=require`
4. **Copy it somewhere safe.** You need it twice. It is a password — do
   not paste it into a chat, a public repo, or a screenshot.

### Step 2 — Put the app on a computer (free to try, ~40 min)

I am recommending **Render** over Fly.io, even though Fly is cheaper and
slightly faster for Singapore, because Render is entirely click-through
in a browser and Fly needs the command line. Get it working first; you
can move later.

1. Go to **render.com**, sign up with GitHub.
2. **New → Web Service**.
3. Connect your GitHub and pick `isaacccccc77-creator/BullBearv13`.
4. Render should detect the `Dockerfile` on its own. If it asks for a
   Language or Runtime, choose **Docker**.
5. Region: **Singapore**.
6. Instance type: the **free** one is fine to test with. It falls asleep
   after ~15 minutes of no visitors, so the next person waits ~30 seconds
   for it to wake. Upgrade to the cheapest paid tier before you share the
   link with anyone.
7. Find **Environment** / **Environment Variables** and add one:
   * Key: `DATABASE_URL`
   * Value: the Neon string from Step 1
8. Click **Create Web Service** and wait. The first build takes
   5–10 minutes — it is installing pandas, scipy and plotly.
9. When it finishes you get a URL like `tickveil-abcd.onrender.com`.
   **Open it. Make an account. Check it works.**

At this point you have a working website. It just has an ugly address.

### Step 3 — Buy the name (~$12, ~10 min)

1. Go to **namecheap.com**, search `tickveil`.
2. Buy whichever ending you like. Check the **renewal** price.
3. **Decline every upsell.** Specifically:
   * **SSL certificate — you do not need this.** Render gives you HTTPS
     free and automatically. This is the upsell most people fall for.
   * PremiumDNS — not needed.
   * Hosting — not needed, and will not run this app.
   * **WhoisGuard / domain privacy — keep this if it is free** (it
     usually is). It hides your home address from public records.

### Step 4 — Point the name at the computer (~20 min, then waiting)

1. **In Render:** your service → **Settings** → **Custom Domain** → add
   `tickveil.com` and also `www.tickveil.com`. Render shows you what DNS
   records it wants. Leave this tab open.
2. **In Namecheap:** Domain List → **Manage** → **Advanced DNS**.
3. **Delete the two records Namecheap put there by default.** There will
   be a `CNAME` for `www` pointing at `parkingpage.namecheap.com`, and a
   `URL Redirect Record`. **This is the step everyone misses**, and
   leaving them is the number one reason a new domain keeps showing a
   parking page after everything else is correct.
4. Add what Render asked for. Usually:
   * Type `A Record`, Host `@`, Value *(the IP Render gave you)*
   * Type `CNAME Record`, Host `www`, Value *(the hostname Render gave you)*

   In Namecheap, `@` means the bare domain. You do not type `tickveil.com`
   in the Host box.
5. Set TTL to **Automatic**.
6. Go back to Render and click **Verify**.
7. **Wait.** Usually 10–30 minutes, occasionally a few hours. HTTPS turns
   itself on once DNS resolves — you do nothing for that.

### Step 5 — Check it (~5 min)

In this repo:

```bash
python preflight.py
```

It tells you what is still wrong, if anything. Run it with your real
database URL set:

```bash
DATABASE_URL="postgresql://..." python preflight.py
```

Green means nothing is blocking.

### Step 6 — Change your password

Your old `Isaac77` password's hash is in this repo's git history. Change
the password. That is the fix — deleting it from history does not reliably
remove it from forks and caches.

---

## When it goes wrong

**Build fails on Render.** Open the build log and read the last ~20
lines. It is almost always a missing dependency. Paste the log to me.

**"This site can't be reached" after DNS.** Normal for the first 30
minutes. Check progress by searching `dnschecker.org` and entering your
domain.

**Still shows a Namecheap parking page.** You did not delete the two
default records in Step 4.3. Go back and delete them.

**Certificate / "Not secure" warning.** Give it an hour after DNS
resolves. If it persists, remove the custom domain in Render and re-add
it.

**App loads but logging in does nothing.** `DATABASE_URL` is wrong or
missing. Check Render's Environment tab, then the logs.

---

## What this costs, roughly

I could not check live prices from where I am, so treat these as
ballpark and verify on the sites:

| | Per month |
|---|---|
| Domain | ~$1 (a ~$12/year bill) |
| Render, cheapest always-on tier | ~$7 |
| Neon database, free tier | $0 |
| **Total** | **~$8/month** |

The free Render tier makes it $1/month, with the 30-second wake-up for
visitors. Fine while you are testing, not fine once you are sharing it.

---

## What I would not worry about yet

Google sign-in, Apple sign-in, email 2FA, custom data feeds. All of it is
either already built and waiting for credentials, or documented in
`DEPLOY.md`. **Get the thing live on a domain first.** Everything else is
easier once there is a real URL to point at.

The one exception, once you are live and if you ever want to promote it:
the market data comes from `yfinance`, which is an unofficial scraper of
Yahoo, not a licensed feed. See `DEPLOY.md` §5. It is fine for a portfolio
project. It is not fine as the basis of a service you advertise.
