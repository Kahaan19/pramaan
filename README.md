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
6. `pytest` — 167 tests: mandate signatures/scope/replay, policy rules/purity, the gated
   checkout flow (executor mocked, no live Razorpay calls in tests), and the hash-chained
   ledger (payload determinism, chain-tamper detection, concurrent-append safety).

**`POST /demo/checkout` is now the full gate**: mandate verification → policy evaluation →
executor. The body is `{"intent": <signed Intent Mandate>, "cart": <signed Cart Mandate>}` —
no raw `amount_paise` field exists; the charged amount comes only from the signed
`cart.total_paise`. Responses: `200` (`ALLOW`, with real Razorpay refs), `202` (`STEP_UP`,
queued for human approval — see Dashboard below), `403` (`DENY`, or a mandate failure),
`409` (a replayed cart nonce), `422` (malformed request).

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

### Audit ledger (Phase 3)

Every mandate check, policy verdict, human-queue entry, individual Razorpay MCP call, and
execution outcome writes exactly one row to a hash-chained, append-only Postgres table
(`ledger_rows`). `GET /ledger/{transaction_id_or_cart_id}/explain` reconstructs the full
story in plain English. Live example (a real `$1.299` ALLOW, values shortened):

```json
{
  "headline": "ALLOWED. Payment link created ({\"order_id\":\"order_TWs...\",\"payment_link_id\":\"plink_TWs...\"}).",
  "narrative": [
    "[seq 5] Checkout request received for cart cart_allow_2edca4d8.",
    "[seq 6] Signed intent and cart mandates verified: cart is within the intent's scope",
    "[seq 7] Cart nonce consumed -- this exact cart can never be replayed again.",
    "[seq 8] ALLOWED: no rule fired; transaction is within all limits",
    "[seq 9] Budget reserved before execution: 50000 paise reserved against the hourly velocity budget",
    "[seq 10] Razorpay call: create_order succeeded",
    "[seq 11] Razorpay call: create_payment_link succeeded",
    "[seq 12] Razorpay call: fetch_payment_link succeeded",
    "[seq 13] Execution confirmed: order order_TWs... / payment link plink_TWs... created"
  ],
  "integrity_status": "OK",
  "integrity_findings": []
}
```

`GET /ledger/verify` walks the whole chain and reports the first tampered row by exact
`seq`; `GET /ledger/head` returns the current `(seq, row_hash)` so it can be checkpointed
outside the database. Verified live: a raw `UPDATE ledger_rows ...` is rejected by a
Postgres trigger; disabling that trigger and mutating a row's payload directly is still
caught by `verify_chain()` recomputing hashes from the stored bytes — the trigger stops
casual mistakes, the hash chain is the actual tamper detector.

**Honest limitations, not hidden:**
- The app connects to Postgres as the `postgres` superuser, which bypasses every grant and
  can drop the trigger or the table outright. The append-only trigger guards against this
  codebase's own bugs, not a DBA with credentials.
- A hash chain cannot detect its own **tail** being truncated (`DELETE ... WHERE seq > N`
  leaves a chain that still verifies). Mitigated by cross-checking against
  `spend_reservations`/`demo_checkouts` — tables written on a separate transaction path — so
  an operational record with zero matching ledger rows raises a specific
  `MISSING_AUDIT_FOR_KNOWN_TRANSACTION` finding instead of silently passing.
- Both `/ledger` endpoints are unauthenticated in this demo.
- `agent_id` from CLAUDE.md's documented ledger-row shape doesn't exist yet — there's no
  agent identity until the Phase 5 buyer agent — so rows use `user_id`/`merchant_id` instead.

### Dashboard (Phase 4)

A Next.js HITL console in `dashboard/`. Panels: a live transaction feed (verdict + rule
fired), a STEP-UP approval queue showing the reviewer the real mandate details (buyer,
merchant, items, intent cap, expiry — not a rubber stamp), an Explain view (calls the
Phase 3 `/ledger/{key}/explain` endpoint), and a red "Rogue agent blocked" panel filtering
DENY events specifically. Clicking any transaction row drives the Explain view.

Run it alongside the control plane:
```
cd dashboard
cp .env.example .env.local        # NEXT_PUBLIC_API_BASE_URL, defaults to localhost:8000
npm install
npm run dev                        # http://localhost:3000
```

**Approval is not a shortcut.** Clicking Approve calls `POST /demo/step-up/{cart_id}/approve`,
which re-verifies the mandate from its stored signed snapshot (never a resubmitted body) with
a fresh clock, re-evaluates policy with fresh velocity/pending-approval state, and — only if
that re-check still clears — executes through the *exact same* code path an automatic `ALLOW`
uses. The re-evaluation is a veto only: it can downgrade a stale `STEP_UP` to `DENY` (e.g. the
intent expired while queued) but a human's approval is what authorizes execution, never a
re-evaluation to `ALLOW` skipping the human. Both veto paths are tested directly.

New backend endpoints this phase added: `GET /demo/step-up` (queue), `GET
/demo/step-up/{cart_id}`, `POST .../approve`, `POST .../deny`, and `GET /ledger/recent` (one
summary per transaction, reusing `explain()`'s own headline logic so the dashboard feed and
the Explain view can never disagree about an outcome).

**Honest limitations:** `actor` on approve/deny is a plain, unauthenticated string naming who
clicked the button — there is no operator identity or auth system in this demo. Both `/ledger`
and `/demo/step-up` endpoints are open to anyone who can reach the API.

## Demo (Phase 5)

`buyer-agent/scenarios.py` runs a two-node LangGraph buyer (`planner` on Gemini →
`executor_request`) against the live control plane in Razorpay test mode: three attacks, each in
two variants, plus one legitimate purchase run twice. The LLM only ever picks which catalog item
to talk about; every field that decides whether money moves (`max_amount_paise`, `merchant_id`,
category, expiry) is fixed in code before the graph runs — so the gate's verdict never depends on
what the model said, only on what the signed mandates actually contain. With `GEMINI_API_KEY`
unset the planner falls back to an offline stub and says so; the scripted demo needs no live LLM
to run.

ARCHITECTURE §7 predicted the over-mandate-spend and goal-hijack attacks would be caught by the
*policy engine*. Live-verified against Razorpay test mode: they're actually caught one layer
earlier, by mandate verification's own scope checks — `verify_mandate_chain` runs
`check_within_cap` and `check_merchant_allowed` before the gate ever calls `evaluate()`. Both
layers are real defence-in-depth (`mandates/scope.py`: "Both must pass; neither substitutes for
the other"), so each scenario below runs a second variant specifically engineered to reach the
policy layer instead, rather than letting a demo narrate a verdict the policy engine never
produced.

| # | Variant | Result |
|---|---|---|
| 1a | Rs 5,000 cart against a Rs 2,000 intent (mandate layer) | `403 cart_exceeds_intent` |
| 1b | Rs 3,500 cart within a generous intent, over the Rs 2,000 platform cap (policy layer) | `403 DENY per_transaction_cap` |
| 2a | injected listing redirects payment to an unregistered payee (mandate layer) | `403 unknown_signer` |
| 2b | known merchant absent from the intent's own allowlist (mandate layer) | `403 merchant_not_allowlisted` |
| 3a | cart price edited after the merchant signed it (mandate layer) | `403 bad_signature_cart` |
| 3b | resubmitting 1b's already-DENYed cart verbatim (mandate layer) | `409 replayed_nonce` |
| 4a | Rs 500 purchase, fully automatic | `200 ALLOW`, real `order_id` + `short_url` |
| 4b | Rs 1,299 purchase, escalated then human-approved via the dashboard's approval endpoint | `202 STEP_UP` → `200` after approve |

All eight end with `GET /ledger/{cart_id}/explain`, printed in full — the "every action
explainable" proof, not an assertion. Live-run note: one 4b attempt hit a transient failure on the
Razorpay test-mode call itself (`create_order` failed on the network hop); the executor marked the
reservation `FAILED`, logged `EXECUTION_FAILED`, moved no money, and a retry moments later
succeeded cleanly — an unstaged demonstration of the "one failure, handled gracefully" bar this
architecture targets, on the infrastructure path rather than the security path.

**Honest scope limit:** the live demo runs against the single registered `merchant_demo_01`
keypair, so it cannot reproduce a *policy-layer* `merchant_allowlist` DENY (that needs a second,
registered-but-not-platform-allowlisted merchant) — the eval batch below covers that rule instead,
with its own synthetic multi-merchant keyring.

## Metrics (honest)

Generated by `eval/run_batch.py` against a throwaway `pramaan_eval` database (created and dropped
by the runner) — same schema, same append-only triggers, same hash chain as the demo database, so
audit coverage is measured under the real constraints, never a relaxed copy. Only the Razorpay
network call is stubbed; everything else (Ed25519 verification, the nonce store, velocity
accounting, the policy engine over the shipped `policies/rules.yaml`, and the ledger) is real. Full
per-attempt data: [`eval/reports/attempts.csv`](eval/reports/attempts.csv). Raw report:
[`eval/reports/latest.json`](eval/reports/latest.json).

- **40 synthetic buyer attempts**: 19 legitimate, 16 malicious, 5 indistinguishable (a runaway
  loop's first five iterations — individually within every stated bound, so scoring them as missed
  attacks would misrepresent a rate limiter as a preventer of first spend; see `eval/batch.py`).
- **Attack block rate: 100.0%** (16/16 malicious attempts blocked, 0 false negatives).
- **False-positive rate: 10.5%** (2/19 legitimate attempts wrongly blocked) — the honest cost of
  the velocity cap (a buyer's 6th purchase in an hour) and the approval-queue-flooding defense (a
  buyer's 4th simultaneous pending high-value cart). Not tuned away: reporting a batch with exactly
  zero false positives would read as cherry-picked.
- **Escalation rate: 36.8%** of legitimate attempts required a human — neither allowed nor blocked,
  the honest friction of human-in-the-loop review.
- **Money moved: Rs 5,795.00** (executed, deduplicated by cart so a legitimate retry is never
  double-counted). **Money blocked: Rs 28,948.00** — attacker-chosen amounts, explicitly *not* a
  savings claim; the count of attacks blocked above is the meaningful number.
- **Audit coverage: 100%** — every one of the 40 attempts has at least one ledger row.
- **Ledger chain: verified intact** (174 rows, `verify_chain().ok is True`, zero findings).

See `eval/reports/latest.md` for the full per-attack-group breakdown.

## Scope & honest stubs
Signed-JSON mandates (Ed25519), not the full W3C Verifiable Credential stack. No real
x402/stablecoin settlement — named as future work. One merchant, one flow, test mode.

## Built with
Python · FastAPI · LangGraph · Ed25519 · Postgres (hash-chained ledger) ·
Razorpay official MCP server · Next.js
