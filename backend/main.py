"""
HTTP layer. Run from the repository root:

    uvicorn backend.main:app --port 8000

Serves the JSON API under /api and, when frontend/dist exists, the built
site at / so one process is the whole website.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, debate, market

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tickveil")

app = FastAPI(title="Tickveil API", docs_url="/api/docs", openapi_url="/api/openapi.json")

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

TAPE = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^VIX", "VIX"), ("^STI", "Straits Times"),
        ("GC=F", "Gold"), ("CL=F", "Oil"), ("BTC-USD", "Bitcoin"), ("EURUSD=X", "EUR/USD")]


@app.exception_handler(market.FeedError)
async def feed_error(_: Request, exc: market.FeedError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


def _ticker(raw: str) -> str:
    try:
        return market.normalise_ticker(raw)
    except market.FeedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def debate_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# ----------------------------------------------------------------------
# Data endpoints. Plain `def`, so FastAPI runs them in its thread pool:
# the data fetches and the bootstrap are blocking work.
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    return {"demo": market.DEMO, "debate_enabled": debate_enabled(),
            "agents": debate.PUBLIC_AGENTS}


@app.get("/api/tape")
def tape():
    out = []
    for symbol, label in TAPE:
        try:
            close = market.history(symbol)["Close"]
            out.append({"symbol": symbol, "label": label, "price": float(close.iloc[-1]),
                        "change_pct": float((close.iloc[-1] / close.iloc[-2] - 1) * 100)})
        except Exception:
            continue
    return {"demo": market.DEMO, "items": out}


@app.get("/api/stock/{ticker}")
def stock(ticker: str, range: str = Query("1Y", pattern="^(1M|3M|6M|1Y|5Y)$")):
    return analysis.overview(_ticker(ticker), range)


@app.get("/api/stock/{ticker}/factor")
def factor(ticker: str):
    return analysis.factor(_ticker(ticker))


@app.get("/api/stock/{ticker}/forecast")
def forecast_lab(ticker: str):
    return analysis.forecast_lab(_ticker(ticker))


class BuyTestIn(BaseModel):
    notional: float = Field(2_000, gt=0, le=10_000_000)
    portfolio: float = Field(20_000, gt=0, le=1_000_000_000)
    horizon_days: int = Field(21, ge=5, le=63)
    commission_pct: float = Field(0.08, ge=0, le=5)
    commission_min: float = Field(1.0, ge=0, le=100)
    spread_pct: float = Field(0.03, ge=0, le=5)
    fx_pct: float = Field(0.0, ge=0, le=5)
    tax_rate: float = Field(0.0, ge=0, le=0.6)


@app.post("/api/stock/{ticker}/buytest")
def buy_test(ticker: str, body: BuyTestIn):
    return analysis.buy_test(_ticker(ticker), **body.model_dump())


@app.get("/api/stock/{ticker}/risk")
def risk(ticker: str, position: float = Query(10_000, gt=0, le=1_000_000_000)):
    return analysis.risk(_ticker(ticker), position)


class LBOIn(BaseModel):
    entry_ebitda: float = Field(100e6, gt=0, le=1e12)
    entry_multiple: float = Field(9.0, gt=0, le=40)
    debt_turns: float = Field(4.5, ge=0, le=12)
    interest_rate: float = Field(0.09, ge=0, le=0.4)
    revenue: float = Field(500e6, gt=0, le=1e13)
    revenue_growth: float = Field(0.04, ge=-0.3, le=0.6)
    margin_improvement: float = Field(0.005, ge=-0.05, le=0.05)
    exit_multiple: float = Field(9.0, gt=0, le=40)
    hold_years: int = Field(5, ge=2, le=10)


@app.post("/api/deals/lbo")
def lbo(body: LBOIn):
    return analysis.lbo(body.model_dump())


# ----------------------------------------------------------------------
# The debate. Each one costs real money, so: finished debates are replayed
# from memory for a while, each visitor is rationed, and there is a daily
# ceiling no amount of traffic can push the bill past.
# ----------------------------------------------------------------------
CACHE_SECONDS = int(os.environ.get("DEBATE_CACHE_MINUTES", "45")) * 60
PER_HOUR = int(os.environ.get("DEBATE_PER_HOUR", "5"))
DAILY_CAP = int(os.environ.get("DEBATE_DAILY_CAP", "150"))
MAX_LIVE = int(os.environ.get("DEBATE_MAX_CONCURRENT", "4"))

_finished: dict[str, tuple[float, list[dict]]] = {}
_by_visitor: dict[str, deque] = defaultdict(deque)
_today: list = [None, 0]
_live = 0
_client = None


def _visitor(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def _admit(visitor: str) -> str | None:
    """None if a new debate may start, otherwise the reason it may not."""
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _today[0] != day:
        _today[0], _today[1] = day, 0
    if _today[1] >= DAILY_CAP:
        return "The Boardroom has hit its daily limit. It reopens at midnight UTC."
    if _live >= MAX_LIVE:
        return "Every seat in the Boardroom is taken. Try again in a minute."
    recent = _by_visitor[visitor]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    if len(recent) >= PER_HOUR:
        wait = int((3600 - (now - recent[0])) / 60) + 1
        return f"You have started {PER_HOUR} debates this hour. Next one in about {wait} min."
    recent.append(now)
    _today[1] += 1
    return None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@app.post("/api/debate/{ticker}")
async def run_debate(ticker: str, request: Request, fresh: bool = False):
    global _live, _client
    symbol = _ticker(ticker)
    if not debate_enabled():
        raise HTTPException(status_code=503, detail="The Boardroom is not switched on: "
                            "the server has no ANTHROPIC_API_KEY.")

    cached = _finished.get(symbol)
    if cached and not fresh and time.time() - cached[0] < CACHE_SECONDS:
        age = int((time.time() - cached[0]) / 60)

        async def replay():
            yield _sse({"type": "replay", "age_minutes": age})
            for event in cached[1]:
                yield _sse(event)
        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    refusal = _admit(_visitor(request))
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)

    dossier = await asyncio.to_thread(analysis.dossier, symbol)

    if _client is None:
        import anthropic
        _client = anthropic.AsyncAnthropic()

    async def stream():
        global _live
        _live += 1
        events: list[dict] = []
        try:
            async for event in debate.run(_client, symbol, dossier):
                events.append(event)
                yield _sse(event)
            if events and events[-1]["type"] == "done":
                _finished[symbol] = (time.time(), events)
                while len(_finished) > 200:
                    _finished.pop(min(_finished, key=lambda k: _finished[k][0]))
        finally:
            _live -= 1

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------
# The built site
# ----------------------------------------------------------------------
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def site(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = (DIST / path).resolve()
        if path and target.is_file() and DIST in target.parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
