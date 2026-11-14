# The Tickveil website

A second way to run Tickveil, alongside the Streamlit app (`app_v30.py`), which is untouched.

| Part | Language | Where |
|---|---|---|
| Front end | TypeScript, React, CSS | `frontend/` |
| Back end (API) | Python, FastAPI | `backend/` |
| The maths | Python, shared with the Streamlit app | `scoring.py`, `verdict.py`, `quant.py`, `deals.py` |

The maths stayed in Python on purpose. Those four files carry 1,000+ lines of tests; rewriting
them in JavaScript would have thrown the tests away to gain nothing a visitor can see.

## Run it on your computer

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && npm run build && cd ..
uvicorn backend.main:app --port 8000        # then open http://localhost:8000
```

While editing the front end, run `npm run dev` inside `frontend/` instead of `npm run build`
and open the address it prints; it reloads as you save.

## Settings (environment variables)

| Name | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Switches the Boardroom on. Without it every other page works and the Boardroom says it is off. This is a password: set it in the host's dashboard, never in a file in this repo. |
| `DEBATE_MODEL` | Which Claude model argues. Default `claude-opus-5-5`. |
| `DEBATE_PER_HOUR` | Debates one visitor may start per hour. Default 5. |
| `DEBATE_DAILY_CAP` | Debates the whole site may start per day. Default 150. This is the ceiling on your bill. |
| `DEBATE_CACHE_MINUTES` | How long a finished debate is replayed for free. Default 45. |
| `TICKVEIL_DEMO` | `1` replaces the market feed with a seeded simulation, for development. Every page shows a banner. Never set this on the real site. |

## What a debate costs

Each debate is 12 calls to the Claude API: one web-search research pass, ten analyst turns and
the Chair's ruling. Expect it to cost real money per debate (web search results are the largest
part), which is why finished debates are replayed from memory and why the limits above exist.
Check the first few in the Anthropic console before sharing the link widely.

## Put it online

`web.Dockerfile` builds the whole site into one container (verified: builds, serves, runs as a
non-root user, health check passes). On Render: New → Web Service → this repo → Runtime
**Docker** → Dockerfile path `./web.Dockerfile` → add `ANTHROPIC_API_KEY` under Environment.

## Tests

```bash
python test_backend.py
python test_forecast.py
```

## Not carried over from the Streamlit app yet

Accounts and login, payments, the journal, watchlist, digest, calendar, multi-asset view, pairs
trading, portfolio optimiser, and the merger, comps and memo parts of the Deal Room. The website
has no sign-in, so nothing a visitor does is saved.
