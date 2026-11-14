"""
Checks for the web backend: the JSON endpoints and the debate engine.

Runs on the seeded demo feed, so it needs no network. The debate is driven
by a stand-in for the Anthropic client that records every request it is
sent, which lets the checks assert on what the engine actually asks for
(who sees whose words, and when) rather than only on what comes out.
"""

import asyncio
import logging
import json
import os
import sys
from types import SimpleNamespace as NS

os.environ["TICKVEIL_DEMO"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import analysis, debate, main, market  # noqa: E402

logging.disable(logging.CRITICAL)
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


web = TestClient(main.app)

print("Endpoints answer with strict JSON")
for path in ("/api/health", "/api/config", "/api/tape", "/api/stock/AAPL",
             "/api/stock/aapl?range=3M", "/api/stock/AAPL/factor", "/api/stock/AAPL/risk"):
    r = web.get(path)
    ok = r.status_code == 200
    if ok:
        json.dumps(r.json(), allow_nan=False)
    check(path, ok, f"status {r.status_code}")

r = web.get("/api/stock/AAPL/forecast")
lab = r.json() if r.status_code == 200 else {}
check("forecast lab answers with strict JSON", r.status_code == 200 and lab.get("status") == "ok"
      and json.dumps(lab, allow_nan=False) is not None)
check("the lab grades all three contenders",
      set(lab.get("grades", {}).get("contenders", {})) == {"always", "model", "factor"})
check("a random walk is not reported as an edge", lab.get("tone") != "positive", lab.get("headline", ""))
check("today's call is a probability", 0 < lab.get("today", {}).get("prob", -1) < 1)
check("all 69 weights are sent for display",
      sum(len(v) for v in lab.get("coefficients", {}).values()) == 69)
r = web.post("/api/stock/AAPL/buytest", json={})
check("buy test with defaults", r.status_code == 200 and r.json()["verdict"] in ("buy", "no", "thin"))
r = web.post("/api/deals/lbo", json={})
check("LBO with defaults", r.status_code == 200 and abs(r.json()["irr"] - 0.1661) < 1e-3,
      str(r.json().get("irr")))

print("Bad input is refused, not computed")
check("a ticker with a slash", web.get("/api/stock/AA%2FPL").status_code in (400, 404))
check("a ticker with a space", web.get("/api/stock/A%20B").status_code == 400)
check("an unknown range", web.get("/api/stock/AAPL?range=9Y").status_code == 422)
check("a negative trade size",
      web.post("/api/stock/AAPL/buytest", json={"notional": -5}).status_code == 422)
check("a 90% tax rate",
      web.post("/api/stock/AAPL/buytest", json={"tax_rate": 0.9}).status_code == 422)

print("Demo data is labelled and invents no news")
ov = web.get("/api/stock/AAPL").json()
fs = web.get("/api/stock/AAPL/factor").json()
check("overview is flagged demo", ov["demo"] is True)
check("no headlines are fabricated", fs["headlines"] == [] and fs["sentiment"] is None)
check("fundamentals are null, not zero", ov["stats"]["trailing_pe"] is None)
check("the score blends only what exists",
      abs(fs["raw"] - fs["technical"]) < 1e-9 and list(fs["weights_used"]) == ["technical"])
check("haircut shrinks magnitude",
      abs(fs["final"] - fs["raw"] * (1 - fs["risk_penalty"])) < 1e-9)

print("The backend agrees with the modules it wraps")
import scoring, verdict  # noqa: E402
df = market.history("AAPL")
tech, _ = scoring.technical_score(df)
check("factor technical score", abs(fs["technical"] - float(tech.iloc[-1])) < 1e-12)
odds = verdict.measure_odds(tech.dropna(), df["Close"], 21)
bt = web.post("/api/stock/AAPL/buytest", json={"notional": 5000, "portfolio": 50000}).json()
a = verdict.assess(odds, 5000, 50000, verdict.Costs())
check("buy test stake", abs(bt["stake_cash"] - a.stake_cash) < 1e-9 and bt["verdict"] == a.verdict)
check("buy test break-even", abs(bt["breakeven"] - a.breakeven) < 1e-12)

dossier = analysis.dossier("AAPL")
check("the Boardroom's dossier carries the forecast test",
      dossier["forecast_lab"].get("model_auc") == lab["grades"]["contenders"]["model"]["auc"])

print("Vote parsing")
check("a clean line", debate.parse_vote("x\nVOTE | short: HOLD | long: BUY | conviction: 4")
      == {"short": "HOLD", "long": "BUY", "conviction": 4})
check("case and spacing", debate.parse_vote("vote|short:avoid|long:hold|conviction:2")
      == {"short": "AVOID", "long": "HOLD", "conviction": 2})
check("the last line wins", debate.parse_vote(
    "VOTE | short: BUY | long: BUY | conviction: 5 ... VOTE | short: AVOID | long: BUY | conviction: 1"
)["short"] == "AVOID")
check("nothing to parse", debate.parse_vote("I abstain.") is None)
check("an out-of-range conviction is not a vote",
      debate.parse_vote("VOTE | short: BUY | long: BUY | conviction: 9") is None)
check("the line is stripped for display",
      debate.strip_vote("Solid.\nVOTE | short: BUY | long: BUY | conviction: 3") == "Solid.")


# ----------------------------------------------------------------------
# A stand-in Anthropic client
# ----------------------------------------------------------------------
class FakeStream:
    def __init__(self, text, blocks=None, stop="end_turn"):
        self.text, self.stop = text, stop
        self.blocks = blocks or [NS(type="text", text=text, citations=None)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def __aiter__(self):
        async def events():
            for block in self.blocks:
                if block.type == "server_tool_use":
                    yield NS(type="content_block_start", content_block=block)
            for i in range(0, len(self.text), 40):
                await asyncio.sleep(0)
                yield NS(type="content_block_delta",
                         delta=NS(type="text_delta", text=self.text[i:i + 40]))
        return events()

    async def get_final_message(self):
        return NS(content=self.blocks, stop_reason=self.stop, model="fake-model",
                  usage=NS(input_tokens=100, output_tokens=50,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0))


RULING = {
    "one_liner": "Wait for a better price.",
    "short_run": {"call": "HOLD", "conviction": 3, "thesis": "t", "drivers": ["d"]},
    "long_run": {"call": "BUY", "conviction": 4, "thesis": "t", "drivers": ["d"]},
    "bull_case": "b", "bear_case": "b", "key_risks": ["r"], "change_our_mind": ["c"],
    "dissent": "The Skeptic.", "sizing_note": "Small.",
}


class FakeClient:
    """Answers by reading the request, the way the engine's phases differ."""

    def __init__(self, fail_agent=None, refuse_research=False):
        self.calls: list[dict] = []
        self.fail_agent, self.refuse_research = fail_agent, refuse_research
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.calls.append(kw)
        if kw.get("tools"):
            if self.refuse_research:
                return FakeStream("", stop="refusal")
            blocks = [
                NS(type="server_tool_use", name="web_search", input={"query": "q"}),
                NS(type="web_search_tool_result", content=[
                    NS(url="https://example.com/a", title="Uncited"),
                    NS(url="http://insecure.example.com", title="Dropped")]),
                NS(type="text", text="BRIEFING-BODY rates were held on 1 Nov.",
                   citations=[NS(url="https://example.com/b", title="Cited")]),
            ]
            return FakeStream("BRIEFING-BODY rates were held on 1 Nov.", blocks)
        if (kw["output_config"].get("format") or {}).get("type") == "json_schema":
            return FakeStream(json.dumps(RULING))
        persona = kw["system"][1]["text"]
        agent = next(a for a in debate.AGENTS if a["persona"] == persona)
        if agent["id"] == self.fail_agent:
            raise RuntimeError("boom")
        is_rebuttal = "Round two" in kw["messages"][0]["content"]
        short = "AVOID" if agent["id"] == "bear" else ("HOLD" if is_rebuttal else "BUY")
        return FakeStream(f"I am {agent['id']} {'R2' if is_rebuttal else 'R1'}.\n"
                          f"VOTE | short: {short} | long: BUY | conviction: 3")


def play(client, dossier=None):
    async def go():
        return [e async for e in debate.run(client, "AAPL", dossier or analysis.dossier("AAPL"))]
    return asyncio.run(go())


print("A full debate")
client = FakeClient()
events = play(client)
kinds = [e["type"] for e in events]
phases = [e["phase"] for e in events if e["type"] == "phase"]
check("phases run in order", phases == ["research", "opening", "rebuttal", "verdict"], str(phases))
check("it ends with done", kinds[-1] == "done" and "error" not in kinds)
check("1 research + 5 openings + 5 rebuttals + 1 ruling = 12 calls", len(client.calls) == 12,
      str(len(client.calls)))

briefing = next(e for e in events if e["type"] == "briefing")
urls = [s["url"] for s in briefing["sources"]]
check("cited sources come first, plain http is dropped",
      urls == ["https://example.com/b", "https://example.com/a"], str(urls))
check("the search is announced", "search" in kinds)

ends = [e for e in events if e["type"] == "turn_end"]
check("ten turns end with a parsed vote", len(ends) == 10 and all(e["vote"] for e in ends))
final = events[-2]
check("the ruling is delivered whole", final["type"] == "verdict" and final["data"] == RULING)
check("the tally counts round-two votes, not round-one",
      final["tally"]["short"] == {"BUY": 0, "HOLD": 4, "AVOID": 1}, str(final["tally"]))
streamed = "".join(e["text"] for e in events
                   if e["type"] == "delta" and e["agent"] == "quant" and e["round"] == "opening")
check("deltas reassemble into the turn", streamed.startswith("I am quant R1."))

print("Who is shown what")
openings = [c for c in client.calls if not c.get("tools")
            and "Round one" in c["messages"][0]["content"]]
rebuttals = [c for c in client.calls if "Round two" in c["messages"][0]["content"]]
chair = client.calls[-1]
check("openings are blind: no colleague's words in any of them",
      len(openings) == 5 and not any("I am " in c["messages"][0]["content"] for c in openings))
check("each rebuttal quotes the other four and its own opening",
      all(c["messages"][0]["content"].count(" R1.") == 5 for c in rebuttals))
check("the briefing reaches every voice",
      all("BRIEFING-BODY" in c["system"][0]["text"] for c in openings + rebuttals + [chair]))
check("the shared context is byte-identical across voices, so it can be cached",
      len({c["system"][0]["text"] for c in openings + rebuttals + [chair]}) == 1
      and all(c["system"][0]["cache_control"] == {"type": "ephemeral"} for c in openings))
check("the chair gets both rounds and the count",
      " R1." in chair["messages"][0]["content"] and " R2." in chair["messages"][0]["content"]
      and "'HOLD': 4" in chair["messages"][0]["content"])
check("the chair is asked for the schema", chair["output_config"]["format"]["schema"]
      == debate.VERDICT_SCHEMA)
check("only the research call can search",
      [bool(c.get("tools")) for c in client.calls] == [True] + [False] * 11)
check("demo mode withholds the made-up numbers from the agents",
      "demo mode" in openings[0]["system"][0]["text"]
      and '"last"' not in openings[0]["system"][0]["text"])
check("no request sets a sampling or thinking parameter the model rejects",
      not any(k in c for c in client.calls for k in ("temperature", "top_p", "thinking")))

print("With live numbers the dossier is passed through")
live = {**analysis.dossier("AAPL"), "demo": False}
client = FakeClient()
play(client, live)
check("figures appear in the shared context",
      '"final_sigma"' in client.calls[1]["system"][0]["text"])

print("Failure is contained")
client = FakeClient(fail_agent="macro")
events = play(client)
kinds = [e["type"] for e in events]
check("one voice failing does not stop the debate",
      kinds[-1] == "done" and kinds.count("turn_error") == 1)
check("the failed voice is left out of round two",
      sum("Round two" in c["messages"][0]["content"] for c in client.calls) == 4)
check("the page is told something readable, not a stack trace",
      next(e for e in events if e["type"] == "turn_error")["message"]
      == "The debate hit an unexpected error.")
events = play(FakeClient(refuse_research=True))
check("a declined research call ends the debate with an error event",
      events[-1]["type"] == "error" and len(events) == 3, str([e["type"] for e in events]))

print("The debate endpoint")
check("off without a key", web.post("/api/debate/AAPL").status_code == 503)
os.environ["ANTHROPIC_API_KEY"] = "test"
main._client = FakeClient()
main.PER_HOUR = 2


def post_debate(path="/api/debate/AAPL"):
    r = web.post(path)
    if r.status_code != 200:
        return r.status_code, []
    return 200, [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]


status, events = post_debate()
check("streams events as server-sent data", status == 200 and events[-1]["type"] == "done")
calls_before = len(main._client.calls)
status, events = post_debate()
check("a repeat is replayed from memory at no cost",
      events[0]["type"] == "replay" and len(main._client.calls) == calls_before)
check("a replay does not use up the visitor's ration", len(main._by_visitor["testclient"]) == 1)
post_debate("/api/debate/MSFT")
status, _ = post_debate("/api/debate/NVDA")
check("the hourly ration is enforced", status == 429, str(status))
main._by_visitor.clear()
main._today[1] = main.DAILY_CAP
status, _ = post_debate("/api/debate/TSLA")
check("the daily ceiling is enforced", status == 429)
check("nothing is left counted as live", main._live == 0)

print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All backend checks passed.")
