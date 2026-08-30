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

**Phase 0 spine only** — proves the Razorpay money path works. No mandates or policy
gate in front of it yet; that lands in Phases 1–2.

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
4. `uvicorn main:app --app-dir control-plane --reload`
5. `curl -X POST localhost:8000/demo/checkout -H "Content-Type: application/json" \
   -d '{"amount_paise": 1299, "description": "test purchase"}'`

Returns a real test-mode Razorpay order + payment link (`order_id`, `payment_link_id`,
`short_url`, statuses). Passing the same `idempotency_key` on a retry returns the exact
same references instead of creating a second order/link — verified live, no double-charge.

**Note on the chain:** the live MCP server has no `payment_link_upi_create` tool, and its
S2S UPI tool (`initiate_payment`) 404s on standard test accounts (needs separate Razorpay
approval). So the automated chain is `create_order` → `create_payment_link` →
`fetch_payment_link`, with `fetch_payment` only called once the link shows an actual
payment — honest limit of a fully automated, no-human-clicks-a-link demo endpoint.

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
