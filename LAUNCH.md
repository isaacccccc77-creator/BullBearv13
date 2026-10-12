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

**Why this is not optional.** Right now the app keeps accounts in JSON
files on the machine it is running on. Render replaces that machine every
time you deploy. So without this step: you deploy, and every account,
watchlist and journal entry is gone. No error, no warning, no way back.
With it, the data lives somewhere else and the app is disposable.

**You do not run any SQL.** The app creates its own tables the first time
it connects. Your whole job in this step is to end up holding one long
string of text and to paste it into Render in Step 2. That is it.

#### 1a. Get the string

1. Go to **neon.tech** and sign in with GitHub.
2. You may already have a project — if you see one called something like
   `muddy-shadow-31011498`, open it and skip to 1a.4.
   Otherwise click **New project**, name it `tickveil`, and pick the
   region nearest Singapore.
3. Wait about thirty seconds while it builds.
4. On the project page there is a **Connect** button, or a panel headed
   **Connection string**. Click it.
5. There is a dropdown or toggle offering a **pooled** and a **direct**
   (sometimes "unpooled") connection. **Either one works.** Take the
   default. The app is configured to be fine with both, which is not
   true of every app and is the reason this guide no longer tells you to
   hunt for a particular one.
6. There may also be tabs for Next.js, Django, psql and so on. Ignore
   them. You want the plain string, the one that starts `postgresql://`
   and looks like:

   ```
   postgresql://neondb_owner:SOMEPASSWORD@ep-cool-name-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```

7. Click the eye / **Show password** control if the middle of it is
   dots, then copy the whole thing.

**Treat this like a password, because it is one.** Anyone holding it can
read and delete every account on your site. Do not paste it into a chat,
do not commit it, do not leave it in a screenshot. If it does leak, open
the Neon dashboard and reset the password — the string changes and the
old one dies.

#### 1b. Where it goes

You will paste it in exactly one place that matters:

* **Render**, as an environment variable named `DATABASE_URL`. That is
  Step 2, point 7. Nothing else is needed for the live site.

Optionally, to run the app on your own laptop against the same database,
put it in `.streamlit/secrets.toml`:

```toml
DATABASE_URL = "postgresql://..."
```

That file is already in `.gitignore`, so it will not be committed. The
app looks for the environment variable first and falls back to this.

#### 1c. How to know it actually worked

This is the part people skip and then wonder about six weeks later.

1. Once the site is live (end of Step 2), sign up for an account on it.
2. Go back to Render and hit **Manual Deploy → Deploy latest commit**.
3. Wait for it to finish, reload the site, and log in with that same
   account.

If you are still there, the database is wired up. If you are asked to
register again, `DATABASE_URL` did not reach the app — check for a typo
in the variable name, and check Render's **Logs** tab for a line
mentioning Postgres.

You can also check from the Neon side: its **Tables** view should show
`users` and `documents` once the app has started once.

#### If something goes wrong

* **"DATABASE_URL is set but psycopg is not installed"** — the build did
  not install dependencies. Make sure Render is building from the
  `Dockerfile`, not as a plain Python app.
* **Connection timeouts** — Neon free-tier databases go to sleep after
  five minutes idle and take a few seconds to wake. The first request
  after a quiet night is slow; that is normal, not a fault.
* **Everything works locally, nothing works live** — almost always the
  variable name. It is `DATABASE_URL`, all capitals, no spaces around
  the `=`, and the value must not have quotes around it in Render's box.


### Step 2 — Put the app on a computer (free to try, ~40 min)

I am recommending **Render** over Fly.io, even though Fly is cheaper and
slightly faster for Singapore, because Render is entirely click-through
in a browser and Fly needs the command line. Get it working first; you
can move later.

1. Go to **render.com**, sign up with GitHub.
2. **New → Web Service**.
3. Connect your GitHub and pick `isaacccccc77-creator/BullBearv13`.
4. Render should detect the `Dockerfile` on its own. **Check that it
   has.** There is now a `package.json` at the top of the repo, left
   behind by the Neon setup, and Render may read that and decide this is
   a Node project — it is not, it is Python, and a Node build will fail
   or produce a site that does nothing. If the Language or Runtime box
   says anything other than **Docker**, change it to Docker by hand.
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
