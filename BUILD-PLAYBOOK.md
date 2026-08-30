# Pramaan — Build Playbook

Copy-paste prompts for Claude Code, in order. Each phase says which **model** to use, what to have in context, the **prompt**, and the **checkpoint** that means "done, move on."

---

## Model strategy (Claude Pro — spend Opus where it matters)

**The pattern: Opus plans, Sonnet builds.**
1. Start a security-critical phase in **Opus + Plan Mode** (Claude Code: press `shift+tab` to cycle to *plan mode*, and `/model opus`). Ask it to produce a plan, not code.
2. Approve/refine the plan.
3. Switch to **Sonnet** (`/model sonnet`) and tell it to execute the approved plan. Sonnet does the code, wiring, UI, and tests.
4. Drop back to **Opus** only for gnarly debugging or a design decision Sonnet is thrashing on.

| Phase | Task type | Model call |
|---|---|---|
| 0 — Spine | Wiring, scaffolding, follow Razorpay docs | **Sonnet** (whole phase) |
| 1 — Mandates | Crypto correctness, scope semantics | **Opus** to design → **Sonnet** to implement |
| 2 — Policy engine | The core IP; must be airtight & deterministic | **Opus** to design the decision model → **Sonnet** for rules + tests |
| 3 — Ledger | Hash-chain / tamper-evidence correctness | **Opus** to design → **Sonnet** to implement CRUD + explain() |
| 4 — HITL + dashboard | Standard full-stack UI | **Sonnet** (whole phase) |
| 5 — Rogue demo + metrics | Honest methodology → then scripting | **Opus** for the batch/metrics design → **Sonnet** to build it |

Rule of thumb: if a bug in the code would be a **security hole or silent money error**, use Opus to design it. If it's plumbing or pixels, Sonnet.

---

## One-time setup (do before Phase 0)

1. **Razorpay test account** → Dashboard → make sure you're in **Test Mode** → Settings → API Keys → generate **Test** Key ID + Secret. Save both.
2. **Merchant token for the remote MCP server:**
   ```bash
   printf '%s:%s' '<RAZORPAY_TEST_KEY_ID>' '<RAZORPAY_TEST_KEY_SECRET>' | base64
   ```
   Save the output as `RAZORPAY_MERCHANT_TOKEN`.
   > Use `printf`, **not** `echo`: `echo` appends a newline, so the token encodes a
   > trailing `\n` and Razorpay rejects it with a 401. Sanity check: a standard test
   > key pair (23-char ID + `:` + 24-char secret = 48 bytes) encodes to exactly
   > 64 base64 chars with no `=` padding.
3. **Add the Razorpay MCP server to Claude Code:**
   ```bash
   claude mcp add razorpay -- npx -y mcp-remote https://mcp.razorpay.com/mcp --header "Authorization: Basic <RAZORPAY_MERCHANT_TOKEN>"
   ```
   (Needs Node.js installed for `npx`.) Verify with `claude mcp list`.
4. **Postgres**: local install or `docker run --name pramaan-db -e POSTGRES_PASSWORD=pramaan -p 5432:5432 -d postgres`.
5. **Gemini API key** (for the buyer agent) → put in `.env`.
6. Copy `.env.example` → `.env` and fill it in. **Never commit `.env`.**

**Files to have in the repo before you start:** `CLAUDE.md`, `ARCHITECTURE.md`, `BUILD-PLAYBOOK.md`, `.gitignore`, `.env.example`, `requirements.txt`, `README.md`, `control-plane/policies/rules.yaml`, `docs/mandates/*.example.json`. Claude Code reads `CLAUDE.md` automatically.

---

## Phase 0 — Spine (Sonnet)
**Goal:** prove a rupee can move in test mode through the executor.

**Prompt:**
> Read CLAUDE.md and ARCHITECTURE.md. We're on Phase 0. Scaffold the FastAPI control-plane service and a Postgres connection (SQLAlchemy). Then build a minimal `executor` module that calls the Razorpay MCP server (test mode) to do exactly one end-to-end flow: `create_order` → `payment_link_upi_create` → `fetch_payment`, all amounts in integer paise, with an idempotency key. Expose one endpoint `POST /demo/checkout` that runs it and returns the Razorpay references. No mandates or policy yet — just prove the money path works. Add a README section on how to run it. Keep secrets in `.env`.

**Checkpoint:** hitting `/demo/checkout` creates a real test-mode order + UPI payment link and you can fetch its status. Update the Phase tracker in CLAUDE.md.

---

## Phase 1 — Mandates (Opus to design → Sonnet to build)
**Goal:** signed Intent + Cart mandates, and a scope check that rejects a cart exceeding its intent.

**Opus, plan mode:**
> Read CLAUDE.md and the mandate examples in docs/mandates. Design the mandate layer: Pydantic schemas for Intent Mandate and Cart Mandate; Ed25519 signing/verification with pynacl; canonical-JSON serialization so signatures are reproducible; and the scope-check logic (cart total ≤ intent max, merchant in allowlist, not expired, nonce not replayed). List the exact functions, their signatures, failure modes, and the unit tests we need. Output a plan only — no code yet.

**Then Sonnet:**
> Execute the approved mandate plan. Implement `control-plane/mandates/` with signing, verification, scope check, and pytest tests covering: valid pass, bad signature, expired, cart-over-cap, merchant-not-allowlisted, replayed nonce. Add a small script to generate a demo user keypair and merchant keypair.

**Checkpoint:** tests green; a tampered or over-scope cart is provably rejected.

---

## Phase 2 — Policy engine (Opus to design → Sonnet to build)
**Goal:** deterministic `ALLOW / STEP_UP / DENY` with the rule that fired.

**Opus, plan mode:**
> Read CLAUDE.md and control-plane/policies/rules.yaml. Design a pure, deterministic policy engine that loads rules.yaml and evaluates a transaction context into a verdict {decision, rule_fired, reason}. Rules: per-transaction cap, cumulative/velocity cap, merchant allowlist, category rule, mandate-expiry, and an amount threshold that escalates to STEP_UP. Define the rule interface, evaluation order (first DENY wins; else highest escalation; else ALLOW), and the full unit-test matrix. No LLM, no clock except an injected timestamp. Plan only.

**Then Sonnet:**
> Execute the approved policy-engine plan. Implement `control-plane/policy/` as pure functions + a loader for rules.yaml, returning a typed verdict. Write exhaustive pytest tests including boundary cases (exactly at cap, one paise over, expired-by-one-second, velocity edge). Then wire it into the checkout endpoint: verify mandate → evaluate policy → only ALLOW reaches the executor.

**Checkpoint:** verdicts reproducible and fully tested; DENY blocks the executor; STEP_UP does not execute yet (queued for Phase 4).

---

## Phase 3 — Audit ledger (Opus to design → Sonnet to build)
**Goal:** tamper-evident, append-only ledger + plain-English explain().

**Opus, plan mode:**
> Read CLAUDE.md. Design the hash-chained append-only audit ledger in Postgres: row schema, canonical-payload hashing, prev_hash linkage, and a verify_chain() that detects any tampering. Design `explain(transaction_id)` that reconstructs the full story (intent → cart → verdict → execution → Razorpay refs) in plain English. Specify where in the request flow each row is written. Plan only.

**Then Sonnet:**
> Execute the approved ledger plan. Implement `control-plane/ledger/` with append-only writes (no update/delete), hash chaining, verify_chain(), and explain(). Instrument the checkout flow so every mandate check, verdict, and executor call writes exactly one row. Add tests: chain verifies on a clean log; verify_chain() fails if any row is mutated. Expose `GET /ledger/{transaction_id}/explain`.

**Checkpoint:** every transaction reconstructs in plain English; mutating any row is detectable.

---

## Phase 4 — HITL + dashboard (Sonnet)
**Goal:** a legible operator UI + human approval for STEP_UP.

**Prompt:**
> Read CLAUDE.md. Build a Next.js dashboard in `dashboard/` talking to the FastAPI service. Panels: (1) live transaction feed with each verdict and the rule that fired; (2) a STEP_UP approval queue where a human sees the mandate + reason and approves/denies — approval then triggers the executor and logs the decision; (3) an "Explain" view calling the ledger explain endpoint; (4) a red "Rogue agent blocked" panel showing DENY events. Clean, minimal, demo-friendly. Add any backend endpoints you need for the approval action (must still log to the ledger).

**Checkpoint:** a human can approve a STEP_UP in the UI and it executes + logs; DENY events show up in red.

---

## Phase 5 — Rogue-agent demo + metrics (Opus to design → Sonnet to build)
**Goal:** the winning demo + honest evidence.

**Opus, plan mode:**
> Read CLAUDE.md and ARCHITECTURE.md §7. Design (a) three scripted rogue-buyer scenarios — over-mandate spend, off-allowlist/goal-hijack, tampered-cart/replay — each expected to be blocked, plus one legitimate purchase that succeeds; and (b) an honest evaluation batch of ~40 synthetic buyer attempts (mix legit/malicious) with metrics: money moved vs blocked, and false-positive cost (legit wrongly blocked). Specify the batch composition and how each metric is computed. Plan only.

**Then Sonnet:**
> Execute the approved plan. Build `buyer-agent/` scenarios and `eval/` (batch runner + metrics script + a printed/JSON report). Add a metrics panel to the dashboard. Record a short demo sequence in the README.

**Checkpoint:** the 5-minute story runs start to finish; metrics report prints; README shows the numbers.

---

## If you run low on time
Phases 0–3 + the three rogue scenarios = a complete, defensible submission. Phase 4 (dashboard) and Phase 5 (metrics batch) raise the ceiling but the core thesis — mandate-bound, policy-gated, audited money movement with a caught rogue agent — is fully demonstrated by Phase 3 + a CLI demo.

## Good habits with Claude Code
- Commit at every green checkpoint (`git commit` after each phase).
- Keep phases in separate sessions/`/clear` between them so context stays sharp.
- Ask Sonnet to run the tests itself and fix failures before you review.
- When Sonnet loops on a bug for more than a couple of tries, switch to Opus for that one fix, then switch back.
