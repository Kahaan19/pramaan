# Pramaan — Governance & Control Plane for Agentic Commerce

**Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce**

[github.com/Kahaan19/pramaan](https://github.com/Kahaan19/pramaan)

Pramaan makes a Razorpay merchant safely transactable by an autonomous AI buyer. Every
money action is **bound by a signed mandate**, **gated by deterministic policy-as-code**,
**executed only through Razorpay's official MCP server** (test mode), and written to a
**tamper-evident audit ledger**. Then we let a rogue agent loose — and it gets caught.

> Fill this README as you build. Judges read: problem → one architecture diagram →
> 20-second demo GIF of the rogue agent being blocked → how to run → metrics table.

## The one rule
Money moves only through the Bounded Executor, and only when a mandate cryptographically
verifies **and** the Policy Engine returns `ALLOW` (or a human approves a `STEP_UP`).
Guardrails live in code, never in prompts.

## Architecture
See `ARCHITECTURE.md`. Threat-model → control mapping in `docs/`.

## Run

### Clone
```
git clone https://github.com/Kahaan19/pramaan.git
cd pramaan
```

1. Postgres running locally and reachable at `DATABASE_URL` (e.g. `docker run --name
   pramaan-db -e POSTGRES_PASSWORD=pramaan -p 5432:5432 -d postgres`, then
   `docker exec pramaan-db psql -U postgres -c "CREATE DATABASE pramaan"`).
2. `.env` filled in from `.env.example` (test-mode Razorpay keys + `RAZORPAY_MERCHANT_TOKEN`).
3. `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
4. `python scripts/generate_keys.py` — writes demo user (`user_kahaan`) + merchant
   (`merchant_demo_01`) Ed25519 keypairs to `secrets/` (gitignored; the control plane loads
   only the public halves).
5. `uvicorn main:app --app-dir control-plane --reload`
6. `pytest` — 117 tests: mandate signatures/scope/replay, policy rules/purity, and the
   gated checkout flow (executor mocked, no live Razorpay calls in tests).

**`POST /demo/checkout` is now the full gate**: mandate verification → policy evaluation →
executor. The body is `{"intent": <signed Intent Mandate>, "cart": <signed Cart Mandate>}` —
no raw `amount_paise` field exists; the charged amount comes only from the signed
`cart.total_paise`. Responses: `200` (`ALLOW`, with real Razorpay refs), `202` (`STEP_UP`,
nothing executed yet — Phase 4 adds the human approval queue), `403` (`DENY`, or a mandate
failure), `409` (a replayed cart nonce), `422` (malformed request).

Live-verified against Razorpay test mode: a ₹1,299 cart returns `202 STEP_UP`
(`step_up_amount_threshold`) with zero Razorpay calls; a sub-₹1,000 cart returns `200 ALLOW`
with a real `order_id`/`short_url`; a cart over the ₹2,000 platform cap returns `403 DENY`
(`per_transaction_cap`) with zero Razorpay calls; a forged cart reusing an already-consumed
nonce returns `409`.

**Note on the Razorpay chain:** the live MCP server has no `payment_link_upi_create` tool,
and its S2S UPI tool (`initiate_payment`) 404s on standard test accounts (needs separate
Razorpay approval). So the automated chain is `create_order` → `create_payment_link` →
`fetch_payment_link`, with `fetch_payment` only called once the link shows an actual
payment — honest limit of a fully automated, no-human-clicks-a-link demo endpoint. Velocity
limits therefore meter *authorized spend commitments* (a payment link created under a
passing verdict), not settled payments — the executor never observes a completed payment,
so "count only completed money" would count nothing.

**Honest scoping note on `category`:** the policy engine's `allowed_categories` rule reads
`category` off the **Intent** Mandate, which the *user's own agent* signs. It is a
self-declared purpose label, not a merchant-attested fact — a compromised agent holding a
valid intent can pick its own category. Deriving category from what's actually in the cart
(a merchant/SKU registry) is future work; the rule still fails closed (an absent or
disallowed category is denied) because an omitted field must never be a way to skip a
control.

## Demo
_(Filled during Phase 5: the three blocked attacks + one successful purchase.)_

## Metrics (honest)
_(Filled during Phase 5: money moved vs blocked, false-positive cost, % actions audited.)_

## Scope & honest stubs
Signed-JSON mandates (Ed25519), not the full W3C Verifiable Credential stack. No real
x402/stablecoin settlement — named as future work. One merchant, one flow, test mode.

## Built with
Python · FastAPI · LangGraph · Ed25519 · Postgres (hash-chained ledger) ·
Razorpay official MCP server · Next.js
