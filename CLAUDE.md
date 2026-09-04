# CLAUDE.md — Pramaan

You are helping build **Pramaan**, a governance & control plane for agentic commerce, for the Razorpay AI Buildathon 2026 (Track 01). Read `ARCHITECTURE.md` for the full design. This file is the operating contract — follow it on every turn.

## What Pramaan is (one line)
A trust-and-control plane that makes a Razorpay merchant safely transactable by an autonomous AI buyer: every money action is bound by a signed mandate, gated by deterministic policy-as-code, executed only through Razorpay's official MCP server (test mode), and written to a tamper-evident audit ledger.

## THE PRIME DIRECTIVE (never violate)
Money can move **only** through the **Bounded Executor**, and the Executor must refuse to act unless BOTH are true:
1. a **mandate cryptographically verifies** (valid signature, not expired, cart within intent scope), AND
2. the **Policy Engine returns `ALLOW`** — or returns `STEP_UP` and a human explicitly approved.

There is no other code path that calls a money-moving Razorpay API. If you ever find yourself adding one, stop and reconsider.

## Non-negotiable invariants
- **Guardrails live in code, never in prompts.** A system-prompt instruction is not a control. Every rule is enforced by deterministic Python.
- **The Policy Engine is pure and deterministic.** No LLM, no network calls, no randomness, no clock reads except an injected timestamp. Same inputs → same verdict, always. It is fully unit-tested.
- **The audit ledger is append-only and hash-chained.** Never UPDATE or DELETE a ledger row. Each row stores `hash(prev_row_hash + canonical_payload)`. Every mandate check, policy verdict, human decision, and Razorpay call writes exactly one row.
- **Every money amount is an integer in paise.** Never use floats for money. `₹12.99` → `1299`.
- **Executor is read-only by default.** Money/write actions require a passing verdict. Use idempotency keys so a retry can never double-charge. Use short-lived, single-purpose credentials (JIT) per transaction.
- **Never log secrets or PII.** No card data, no API secrets, no full contact details in logs or the ledger. Store references (Razorpay IDs), not raw sensitive data.
- **Test mode only.** All Razorpay calls use test keys. Never wire live keys.

## Tech stack (do not substitute without asking)
- Control plane: **Python 3.11+ + FastAPI**
- Agent orchestration: **LangGraph** (Planner node + Executor-request node, kept separate)
- LLM: **Gemini** via `langchain-google-genai` (or local Ollama) — used ONLY by the buyer agent, never in the decision path
- Mandates/crypto: **Ed25519** via `pynacl`
- Policy-as-code: pure Python evaluating `control-plane/policies/rules.yaml`
- Ledger + data: **Postgres** (SQLAlchemy), append-only + hash chain
- Payments: **official Razorpay MCP server** (`https://mcp.razorpay.com/mcp`, test mode) — no custom payment HTTP code
- Dashboard/HITL: **Next.js + React**
- Tests: **pytest**

## Directory map
```
control-plane/          FastAPI service (the star)
  mandates/             Ed25519 sign/verify, Intent + Cart schemas, scope check
  policy/               deterministic rules engine (ALLOW/STEP_UP/DENY + rule_fired)
  policies/rules.yaml   the policy-as-code file (human-readable)
  executor/             Razorpay MCP adapter — the ONLY money path
  ledger/               hash-chained append-only store + "explain(transaction_id)"
  tests/                pytest — policy + mandate + ledger must be well covered
buyer-agent/            LangGraph buyer + scripted rogue scenarios
dashboard/              Next.js HITL console, live feed, explain view
eval/                   synthetic batch + metrics (blocked vs allowed, false-positive cost)
docs/                   diagrams, threat-model → control mapping, mandate examples
```

## Data models (keep these shapes)
- **Intent Mandate** (signed by user key): `{ intent_id, user_id, max_amount_paise, merchant_allowlist[], category?, expires_at, human_present, nonce, signature }`
- **Cart Mandate** (signed by merchant key): `{ cart_id, intent_id, merchant_id, items[{sku, qty, unit_price_paise}], total_paise, nonce, signature }`
- **Policy verdict**: `{ decision: ALLOW|STEP_UP|DENY, rule_fired, reason_human_readable, evaluated_at }`
- **Ledger row**: `{ id, ts, event_type, agent_id, intent_id?, cart_id?, verdict?, rule_fired?, razorpay_refs?, explanation, prev_hash, row_hash }`

## Coding conventions
- Type hints everywhere; Pydantic models for all mandate/verdict shapes.
- Pure functions for policy rules: `rule(context) -> Optional[Violation]`. Compose them; first violation wins for DENY; threshold rules can escalate to STEP_UP.
- Canonical JSON (sorted keys, no whitespace ambiguity) before signing/verifying and before hashing ledger rows — signatures and hashes must be reproducible.
- Small, testable modules. Every security-relevant function gets unit tests.
- When wiring Razorpay, verify tool names/params against the live MCP server and Razorpay docs; the live MCP server has no `payment_link_upi_create` tool, and `initiate_payment` (S2S UPI) returns 404 on this test account (S2S JSON v1 requires separate Razorpay approval not granted by default). The Phase 0 executor uses `create_order` → `create_payment_link` → `fetch_payment_link`, calling `fetch_payment` only if the link already shows a `payment_id` (it won't until a human actually pays it — that's the honest limit of a fully automated demo endpoint).

## Git & commit discipline
- Commit early and often — at minimum once per completed checkpoint, but also after any meaningfully complete sub-step (a working module, a passing test suite, a fixed bug), not just at the end of a phase.
- Never bundle unrelated changes into one commit. If a turn touches two different concerns (e.g. a docs fix and a new feature), make two commits.
- Write real commit messages, not "wip" or "update". Use conventional-commit style prefixes: feat:, fix:, docs:, test:, refactor:, chore:. Example: feat(mandates): add Ed25519 sign/verify with scope check
- Commit message body (when the change is non-trivial) should say what changed and why, in 1-3 short lines — not a diff restatement.
- After every commit, run git push so the remote (GitHub) stays in sync. Tell me if a push fails rather than silently retrying with force.
- Before starting a new phase, confirm the previous phase's work is committed and pushed.
- Never commit .env, secrets, or generated keypairs. If unsure whether something is sensitive, ask before committing.

## Build phase tracker (update as we go)
- [x] Phase 0 — Spine: FastAPI + Postgres + Razorpay MCP; one order→UPI link→fetch works
- [x] Phase 1 — Mandates: Ed25519 sign/verify + scope check
- [x] Phase 2 — Policy engine: deterministic rules + verdicts + tests
- [x] Phase 3 — Ledger: hash chain + explain() API
- [x] Phase 4 — HITL + dashboard
- [x] Phase 5 — Rogue-agent demo + metrics batch

## Scope discipline (what NOT to build)
One merchant, one product category, one buyer flow (UPI payment link, test mode). No marketplace. No real x402/stablecoin settlement (name it as future work). Lightweight signed-JSON mandates, not the full W3C Verifiable Credential stack — and say so honestly in the README. The buyer agent exists to be governed and attacked; do not gold-plate it. Depth over breadth: nothing slips past the gate.
