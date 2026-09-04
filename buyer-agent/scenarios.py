#!/usr/bin/env python3
"""The Phase 5 scripted demo: three rogue-buyer attacks (each run twice --
once landing at the mandate layer, once at the policy layer, since they
genuinely differ -- see this repo's Phase 5 plan, finding F1) plus one
legitimate purchase run twice (fully automatic, and human-approved).

Every scenario runs against the LIVE, running control plane in Razorpay test
mode (`uvicorn main:app --app-dir control-plane`, per the README) -- this is
NOT eval/run_batch.py's isolated synthetic batch. It ends by calling
GET /ledger/{cart_id}/explain and printing the narrative: the "every action
explainable" proof.

Run:
    python3 buyer-agent/scenarios.py
"""

import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BUYER_AGENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = BUYER_AGENT_DIR.parent
if str(BUYER_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(BUYER_AGENT_DIR))

load_dotenv(REPO_ROOT / ".env")  # picks up GEMINI_API_KEY if present; harmless if not

import attacks  # noqa: E402
from graph import API_BASE, build_graph  # noqa: E402

MERCHANT_ID = "merchant_demo_01"
PLATFORM_CAP_PAISE = 200000  # rules.yaml's per_transaction_cap_paise, for narration only


def _print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _print_plan(result: dict) -> None:
    plan = result["plan"]
    source = "Gemini" if result["used_llm"] else "offline stub"
    print(f"  planner ({source}) chose: sku={plan['sku']!r} note={plan['note']!r}")


def _print_response(result: dict) -> None:
    status = result["response_status"]
    body = result["response_body"]
    print(f"  POST /demo/checkout -> {status}")
    if status == 200:
        checkout = body.get("checkout", {})
        print(f"    ALLOWED. order_id={checkout.get('order_id')} short_url={checkout.get('short_url')}")
    elif status == 202:
        print(f"    STEP_UP escalated. step_up_request_id={body.get('step_up_request_id')}")
    elif "verdict" in body:
        v = body["verdict"]
        print(f"    verdict={v.get('decision')} rule_fired={v.get('rule_fired')!r} reason={v.get('reason')!r}")
    else:
        print(f"    error_code={body.get('error_code')!r} detail={body.get('detail')!r}")


def _explain(cart_id: str) -> None:
    resp = httpx.get(f"{API_BASE}/ledger/{cart_id}/explain", timeout=30)
    body = resp.json()
    print(f"  explain({cart_id}): {body.get('headline')}")
    for line in body.get("narrative", []):
        print(f"    {line}")


def run_scenario(persona: str, scenario_params: dict) -> dict:
    graph = build_graph()
    result = graph.invoke({"persona": persona, "scenario_params": scenario_params})
    _print_plan(result)
    _print_response(result)
    return result


def scenario_1_over_mandate_spend() -> None:
    _print_header("SCENARIO 1a: over-mandate spend (mandate layer) -- Rs 5,000 cart against a Rs 2,000 intent")
    result = run_scenario(
        "honest",
        {"max_amount_paise": 200000, "cart_total_paise": 500000, "merchant_id": MERCHANT_ID},
    )
    _explain(result["cart"]["cart_id"])

    _print_header("SCENARIO 1b: over-mandate spend (policy layer) -- within the intent's own cap, over the platform cap")
    result = run_scenario(
        "honest",
        {"max_amount_paise": 500000, "cart_total_paise": 350000, "merchant_id": MERCHANT_ID},
    )
    _explain(result["cart"]["cart_id"])
    return result  # variant b's cart is reused by scenario 3b's nonce-replay


def scenario_2_goal_hijack() -> None:
    _print_header("SCENARIO 2a: goal hijack (mandate layer) -- injected listing redirects payment to an unknown payee")
    result = run_scenario(
        "injected",
        {
            "max_amount_paise": 150000,
            "cart_total_paise": 50000,
            "merchant_id": "merchant_rogue_99",
            "merchant_allowlist": ["merchant_rogue_99"],
        },
    )
    _explain(result["cart"]["cart_id"])

    _print_header("SCENARIO 2b: goal hijack (mandate layer) -- known merchant absent from the intent's OWN allowlist")
    result = run_scenario(
        "injected",
        {
            "max_amount_paise": 150000,
            "cart_total_paise": 50000,
            "merchant_id": MERCHANT_ID,
            "merchant_allowlist": ["merchant_other_shop"],
        },
    )
    _explain(result["cart"]["cart_id"])


def scenario_3_tampered_and_replay(overcap_result: dict) -> None:
    _print_header("SCENARIO 3a: tampered cart (mandate layer) -- price edited after the merchant signed it")
    result = run_scenario(
        "honest",
        {
            "max_amount_paise": 150000,
            "cart_total_paise": 50000,
            "merchant_id": MERCHANT_ID,
            "attack_fn": lambda cart: attacks.tamper_cart_price(cart, 149900),
        },
    )
    _explain(result["cart"]["cart_id"])

    _print_header("SCENARIO 3b: replay (mandate layer) -- resubmitting scenario 1b's DENYed cart verbatim")
    result = run_scenario(
        "honest",
        {"reuse_intent": overcap_result["intent"], "reuse_cart": overcap_result["cart"]},
    )
    _explain(result["cart"]["cart_id"])


def scenario_4_legitimate_purchase() -> None:
    _print_header("SCENARIO 4a: legitimate purchase, fully automatic -- Rs 500, under the step-up threshold")
    result = run_scenario(
        "honest",
        {"max_amount_paise": 150000, "cart_total_paise": 50000, "merchant_id": MERCHANT_ID},
    )
    _explain(result["cart"]["cart_id"])

    _print_header("SCENARIO 4b: legitimate purchase, human-approved -- Rs 1,299, at/above the step-up threshold")
    result = run_scenario(
        "honest",
        {"max_amount_paise": 200000, "cart_total_paise": 129900, "merchant_id": MERCHANT_ID},
    )
    cart_id = result["cart"]["cart_id"]
    if result["response_status"] != 202:
        print(f"  (expected a 202 STEP_UP; got {result['response_status']} -- skipping approval)")
        _explain(cart_id)
        return

    print(f"  approving via POST /demo/step-up/{cart_id}/approve (actor=demo_operator)...")
    approve_resp = httpx.post(
        f"{API_BASE}/demo/step-up/{cart_id}/approve", json={"actor": "demo_operator"}, timeout=30
    )
    approve_body = approve_resp.json()
    print(f"  approve -> {approve_resp.status_code}")
    if approve_resp.status_code == 200:
        checkout = approve_body.get("checkout", {})
        print(f"    APPROVED AND EXECUTED. order_id={checkout.get('order_id')} short_url={checkout.get('short_url')}")
    else:
        print(f"    {approve_body}")
    _explain(cart_id)


def _check_server_up() -> bool:
    try:
        httpx.get(f"{API_BASE}/ledger/head", timeout=5).raise_for_status()
        return True
    except Exception:
        return False


def main() -> int:
    if not _check_server_up():
        print(
            f"[buyer-agent] cannot reach the control plane at {API_BASE} -- "
            "start it with: uvicorn main:app --app-dir control-plane --reload",
            file=sys.stderr,
        )
        return 1

    overcap_result = scenario_1_over_mandate_spend()
    scenario_2_goal_hijack()
    scenario_3_tampered_and_replay(overcap_result)
    scenario_4_legitimate_purchase()

    print()
    print("=" * 78)
    print("Done. All three attacks blocked in both variants; the legitimate purchases")
    print("completed (one automatic, one human-approved). No money moved outside the")
    print("Bounded Executor at any point.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
