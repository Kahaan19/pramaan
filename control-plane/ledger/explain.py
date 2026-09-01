"""explain(): impure loader maps ORM rows to frozen dataclasses at the
boundary (an ORM object can lazy-load on attribute access, i.e. perform I/O
from inside what's supposed to be a pure function -- policy/context.py
already applies this same rule), then a PURE renderer turns them into an
ordered, plain-English narrative.

When the chain is broken, that goes first and loud: rows at or after the
first bad seq render as UNVERIFIED rather than being narrated as fact. A
narrative rendered over a broken chain without saying so is not evidence --
it is the single most misleading thing this feature could produce.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger.models import LedgerRow
from ledger.verify import ChainVerification, verify_chain


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: str
    event_type: str
    transaction_id: str
    cart_id: str | None
    intent_id: str | None
    actor: str | None
    decision: str | None
    rule_fired: str | None
    razorpay_refs: str | None
    explanation: str


@dataclass(frozen=True)
class ExplainResult:
    found: bool
    transaction_id: str | None
    headline: str
    narrative: tuple[str, ...]
    integrity_status: str  # "OK" | "BROKEN" | "UNKNOWN" (no rows at all)
    integrity_findings: tuple[str, ...]
    entries: tuple[LedgerEntry, ...]


def _to_entry(row: LedgerRow) -> LedgerEntry:
    return LedgerEntry(
        seq=row.seq,
        ts=row.ts,
        event_type=row.event_type,
        transaction_id=row.transaction_id,
        cart_id=row.cart_id,
        intent_id=row.intent_id,
        actor=row.actor,
        decision=row.decision,
        rule_fired=row.rule_fired,
        razorpay_refs=row.razorpay_refs,
        explanation=row.explanation,
    )


def load_entries(db: Session, key: str) -> tuple[LedgerEntry, ...]:
    """`key` may be a transaction_id or a cart_id -- explain() accepts
    either. transaction_id is a minted UUID (opaque, not attacker-influenced
    before verification); cart_id is caller-supplied and may return several
    attempts against the same cart (retries, replays, a forged duplicate).
    """
    rows = (
        db.execute(select(LedgerRow).where(LedgerRow.transaction_id == key).order_by(LedgerRow.seq.asc()))
        .scalars()
        .all()
    )
    if not rows:
        rows = (
            db.execute(select(LedgerRow).where(LedgerRow.cart_id == key).order_by(LedgerRow.seq.asc()))
            .scalars()
            .all()
        )
    return tuple(_to_entry(r) for r in rows)


def explain(db: Session, key: str) -> ExplainResult:
    entries = load_entries(db, key)
    chain = verify_chain(db)
    return render_narrative(entries, chain, requested_key=key)


def render_narrative(
    entries: tuple[LedgerEntry, ...], chain: ChainVerification, requested_key: str
) -> ExplainResult:
    """Pure: takes already-loaded entries and an already-computed chain
    verification, does no I/O and reads no clock.
    """
    if not entries:
        return ExplainResult(
            found=False,
            transaction_id=None,
            headline=f"No ledger record found for {requested_key!r}.",
            narrative=(),
            integrity_status="UNKNOWN",
            integrity_findings=(),
            entries=(),
        )

    first_bad_seq = chain.first_bad_seq
    narrative = tuple(
        _narrate_entry(e, verified=(first_bad_seq is None or e.seq < first_bad_seq)) for e in entries
    )
    headline = _headline(entries, unverified=first_bad_seq is not None and entries[0].seq >= first_bad_seq)

    integrity_status = "OK" if chain.ok else "BROKEN"
    integrity_findings = tuple(f"{f.kind} at seq={f.seq}: {f.detail}" for f in chain.findings)

    return ExplainResult(
        found=True,
        transaction_id=entries[0].transaction_id,
        headline=headline,
        narrative=narrative,
        integrity_status=integrity_status,
        integrity_findings=integrity_findings,
        entries=entries,
    )


def _headline(entries: tuple[LedgerEntry, ...], unverified: bool) -> str:
    if unverified:
        return "CHAIN INTEGRITY BROKEN. This transaction's rows could not be verified -- treat any conclusion below with suspicion."
    if any(e.decision == "DENY" for e in entries):
        return "BLOCKED. No money moved."
    if any(e.event_type == "EXECUTION_COMMITTED" for e in entries):
        refs = next((e.razorpay_refs for e in reversed(entries) if e.razorpay_refs), None)
        return f"ALLOWED. Payment link created ({refs})." if refs else "ALLOWED. Payment link created."
    if any(e.event_type == "STEP_UP_QUEUED" for e in entries):
        return "PENDING HUMAN APPROVAL. No money has moved yet."
    if any(e.event_type == "EXECUTION_FAILED" for e in entries):
        return "EXECUTION FAILED. The payment attempt did not complete."
    if any(e.event_type == "IDEMPOTENT_REPLAY" for e in entries):
        return "REPLAY. This is a duplicate of an earlier request; see its cached outcome below."
    return "OUTCOME UNCLEAR from the recorded events."


def _narrate_entry(entry: LedgerEntry, verified: bool) -> str:
    prefix = f"[seq {entry.seq}]"
    if not verified:
        return f"{prefix} UNVERIFIED -- chain integrity is broken at or before this row; its content cannot be trusted."

    et = entry.event_type
    if et == "REQUEST_RECEIVED":
        return f"{prefix} Checkout request received for cart {entry.cart_id}."
    if et == "IDEMPOTENT_REPLAY":
        return f"{prefix} Replay of an earlier request for this cart: {entry.explanation}"
    if et == "MANDATE_VERIFIED":
        return f"{prefix} Signed intent and cart mandates verified: {entry.explanation}"
    if et == "MANDATE_REJECTED":
        return f"{prefix} Mandate verification FAILED: {entry.explanation}"
    if et == "NONCE_CONSUMED":
        return f"{prefix} Cart nonce consumed -- this exact cart can never be replayed again."
    if et == "NONCE_REPLAY_REJECTED":
        return f"{prefix} Replay rejected: {entry.explanation}"
    if et == "POLICY_VERDICT":
        if entry.decision == "DENY":
            return f"{prefix} BLOCKED by rule `{entry.rule_fired}`: {entry.explanation}"
        if entry.decision == "STEP_UP":
            return f"{prefix} Escalated to a human by rule `{entry.rule_fired}`: {entry.explanation}"
        return f"{prefix} ALLOWED: {entry.explanation}"
    if et == "STEP_UP_QUEUED":
        return f"{prefix} Queued for human approval: {entry.explanation}"
    if et in ("STEP_UP_APPROVED", "STEP_UP_REJECTED"):
        return f"{prefix} {et.replace('_', ' ').title()}: {entry.explanation}"
    if et == "SPEND_RESERVED":
        return f"{prefix} Budget reserved before execution: {entry.explanation}"
    if et == "RAZORPAY_CALL":
        return f"{prefix} Razorpay call: {entry.explanation}"
    if et == "EXECUTION_COMMITTED":
        return f"{prefix} Execution confirmed: {entry.explanation}"
    if et == "EXECUTION_DEDUPED":
        return f"{prefix} Duplicate execution attempt deduplicated: {entry.explanation}"
    if et == "EXECUTION_FAILED":
        return f"{prefix} Execution FAILED: {entry.explanation}"
    if et == "REQUEST_REJECTED_MALFORMED":
        return f"{prefix} Request rejected as malformed: {entry.explanation}"
    if et == "REQUEST_ERRORED":
        return f"{prefix} Unexpected error: {entry.explanation}"
    return f"{prefix} {et}: {entry.explanation}"
