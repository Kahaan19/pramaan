"""The buyer agent: a two-node LangGraph, kept separate per CLAUDE.md.

    planner (Gemini)  ->  executor_request (signs + POSTs /demo/checkout)

The LLM decides WHAT to buy (one SKU off a fixed catalog, via structured
output); it never sees or produces max_amount_paise, merchant_id, or any
other field that decides whether money moves. Every one of those comes from
`scenario_params`, fixed in code before the graph runs (see scenarios.py) --
so the gate's verdict never depends on what the model said, only on what the
signed mandates actually contain. This is "guardrails live in code, never in
prompts" applied to the demo's OWN buyer, not just to Pramaan's control
plane.

If GEMINI_API_KEY is unset or the call fails for any reason, the planner
node falls back to a fixed stub plan and says so -- the scripted demo must
run offline (no live API dependency during judging).
"""

import os
import re
import sys
import time
from typing import TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from personas import CATALOG, PERSONAS

API_BASE = os.environ.get("PRAMAAN_API_BASE", "http://localhost:8000")

# gemini-2.0-flash was retired (404). The free tier then caps each model at
# 5 requests/minute AND 20 requests/day, per project -- 3.6 and 3.7 were both
# exhausted in turn. Quota is per-model, so each switch buys a fresh 20/day;
# a full scenarios.py run costs 8. 3.8 was returning 503 "high demand", so
# 3.5-flash (stable, untouched budget) is the pick. Pinned to an exact
# version rather than gemini-flash-latest so a demo run is reproducible.
# No temperature= here: these models use fixed sampling defaults and warn that
# the parameter is ignored.
GEMINI_MODEL = "gemini-3.5-flash"


class PurchasePlan(BaseModel):
    sku: str
    qty: int = 1
    category: str
    note: str = ""


class BuyerState(TypedDict, total=False):
    persona: str  # "honest" | "injected"
    scenario_params: dict  # fixed in code -- see scenarios.py; never LLM output
    plan: dict
    used_llm: bool
    intent: dict
    cart: dict
    response_status: int
    response_body: dict


def _catalog_text() -> str:
    lines = []
    for item in CATALOG:
        price = f"Rs {item['unit_price_paise'] / 100:.2f}"
        lines.append(f"- {item['sku']} ({price}, category={item['category']}): {item['description']}")
    return "\n".join(lines)


def _stub_plan() -> PurchasePlan:
    """The offline fallback -- always picks the first, unremarkable catalog
    item. Deliberately dumb: this path exists so the demo runs without a
    live API key, not to simulate a smart offline buyer.
    """
    item = CATALOG[0]
    return PurchasePlan(sku=item["sku"], qty=1, category=item["category"], note="offline stub planner")


# The free tier caps generate_content at 5 requests per MINUTE per model
# (quotaId GenerateRequestsPerMinutePerProjectPerModel-FreeTier). scenarios.py
# fires eight planner calls back to back, so the tail of a run trips it even
# though the daily budget is untouched. The 429 carries the wait it wants; the
# client's own max_retries gives up before that long, so honour it here.
_RETRY_HINT = re.compile(r"retry in (\d+(?:\.\d+)?)s|retryDelay['\"]?: ?['\"]?(\d+)s")


def _retry_after_seconds(exc: Exception) -> float | None:
    """Seconds the API asked us to wait, or None if waiting cannot help.

    Only the per-MINUTE cap is worth retrying. A per-DAY exhaustion cannot be
    outwaited inside a demo run, and every retry is itself a counted request --
    retrying into a daily cap just burns the rest of the budget. Learned the
    hard way: an earlier version retried on both and consumed a model's whole
    remaining daily allowance.
    """
    text = str(exc)
    if "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return None
    if "PerDay" in text:
        return None
    m = _RETRY_HINT.search(text)
    if not m:
        return 20.0
    return float(m.group(1) or m.group(2)) + 1.0


def _invoke_planner(structured_llm, messages, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return structured_llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001 -- re-raised below if not a rate limit
            delay = _retry_after_seconds(exc)
            if delay is None or attempt == attempts - 1:
                raise
            print(
                f"[buyer-agent] rate-limited by the free tier; waiting {delay:.0f}s "
                f"then retrying (attempt {attempt + 2}/{attempts})",
                file=sys.stderr,
            )
            time.sleep(delay)


def planner_node(state: BuyerState) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[buyer-agent] GEMINI_API_KEY not set -- using the offline stub planner", file=sys.stderr)
        return {"plan": _stub_plan().model_dump(), "used_llm": False}

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        system_prompt = PERSONAS[state["persona"]]
        llm = ChatGoogleGenerativeAI(model=GEMINI_MODEL, api_key=api_key)
        structured_llm = llm.with_structured_output(PurchasePlan)
        plan = _invoke_planner(
            structured_llm,
            [
                SystemMessage(content=f"{system_prompt}\n\nCatalog:\n{_catalog_text()}"),
                HumanMessage(content="Choose one item and return your purchase plan."),
            ],
        )
        return {"plan": plan.model_dump(), "used_llm": True}
    except Exception as exc:  # noqa: BLE001 -- any LLM failure falls back, never crashes the demo
        print(f"[buyer-agent] Gemini call failed ({exc!r}); falling back to the offline stub planner", file=sys.stderr)
        return {"plan": _stub_plan().model_dump(), "used_llm": False}


def executor_request_node(state: BuyerState) -> dict:
    """Signs the mandates from scenario_params (NOT from state["plan"] --
    see this module's docstring) and POSTs to the live control plane. The
    attack_fn, if any, is applied to the already-signed cart, mirroring a
    relay altering a price in flight (see attacks.py).
    """
    import mandates_io  # local import: mandates_io.py inserts control-plane onto sys.path

    params = state["scenario_params"]

    if "reuse_intent" in params:
        # M9-shaped nonce-replay variants resubmit an EARLIER attempt's exact
        # signed cart verbatim -- there is no new "decision" to make, so no
        # fresh signing happens here.
        intent, cart = params["reuse_intent"], params["reuse_cart"]
    else:
        intent = mandates_io.sign_intent(
            max_amount_paise=params["max_amount_paise"],
            merchant_allowlist=params.get("merchant_allowlist"),
            category=params.get("category", state["plan"]["category"]),
            human_present=params.get("human_present", True),
            expires_in_hours=params.get("expires_in_hours", 24.0),
        )
        cart = mandates_io.sign_cart(
            intent_id=intent["intent_id"],
            total_paise=params["cart_total_paise"],
            items=params.get("items"),
            merchant_id=params.get("merchant_id", mandates_io.MERCHANT_ID),
        )
        attack_fn = params.get("attack_fn")
        if attack_fn is not None:
            cart = attack_fn(cart)

    resp = httpx.post(f"{API_BASE}/demo/checkout", json={"intent": intent, "cart": cart}, timeout=30)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return {"intent": intent, "cart": cart, "response_status": resp.status_code, "response_body": body}


def build_graph():
    graph = StateGraph(BuyerState)
    graph.add_node("planner", planner_node)
    graph.add_node("executor_request", executor_request_node)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor_request")
    graph.add_edge("executor_request", END)
    return graph.compile()
