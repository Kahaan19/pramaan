# Pramaan — Governance & Control Plane for Agentic Commerce

**Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce**

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
_(Filled during Phase 0.)_

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
