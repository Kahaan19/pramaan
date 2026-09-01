"""append_event(): the ledger's only write path.

Own short-lived session, own advisory lock, own retry -- deliberately NOT
the caller's request-scoped session. This mirrors mandates/nonce.py's
consume_cart_nonce fix: a row written on the caller's session would be
erased by any later db.rollback() on that session (executor/gate.py's DENY
path does exactly that), which would silently destroy the audit trail of the
one event the whole rogue-agent demo exists to show.

Locking uses the TWO-KEY advisory lock form, `pg_advisory_xact_lock(2, 0)` --
never the single-argument bigint form. executor/gate.py's per-user lock uses
`pg_advisory_xact_lock(hashtext(user_id))`, which resolves to the
single-arg-bigint overload; that is the SAME 64-bit key space a
fixed-integer ledger lock would collide into, and a collision would hang a
request indefinitely (this codebase's db.py sets no lock_timeout globally,
confirmed 0 = wait forever). The two-key form is a separate, documented
lock space, so this cannot collide with it structurally.

Correctness does not actually depend on the lock being perfect, on purpose:
UNIQUE(prev_hash) on ledger_rows makes a fork (two rows claiming the same
predecessor) an IntegrityError, not a silent corruption -- even if isolation
were ever raised above READ COMMITTED (which would otherwise let a stale
tail read through undetected). The lock is a performance optimization that
avoids retries in the common case; the constraint is what actually
guarantees the chain never forks. On IntegrityError, this simply retries: a
fresh session, a fresh tail read, a fresh hash.

seq comes from MAX(seq)+1 read under the lock -- never a Postgres SEQUENCE
(gaps on rollback, allocates out of commit order) and never `id` (same
BigInteger-autoincrement problem, only safe today by relying on the lock
never being bypassed). MAX(seq)+1 is genuinely gapless: an append either
fully commits or leaves no trace at all (own session, single INSERT+COMMIT),
so a failed attempt never consumes a seq value.
"""

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from ledger.events import LedgerEvent
from ledger.hashing import GENESIS_PREV_HASH, chain_hash, render_ts
from ledger.models import LedgerRow
from ledger.payload import LedgerPayload
from mandates.canonical import canonical_json

_MAX_RETRIES = 5
_LOCK_TIMEOUT_MS = 5000


def append_event(
    *,
    event_type: LedgerEvent,
    now: datetime,
    transaction_id: str,
    explanation: str,
    cart_id: str | None = None,
    intent_id: str | None = None,
    user_id: str | None = None,
    merchant_id: str | None = None,
    actor: str | None = None,
    amount_paise: int | None = None,
    human_present: bool | None = None,
    intent_digest: str | None = None,
    intent_signature: str | None = None,
    cart_digest: str | None = None,
    cart_signature: str | None = None,
    mandate_error_code: str | None = None,
    mandate_error_message: str | None = None,
    decision: str | None = None,
    rule_fired: str | None = None,
    reason: str | None = None,
    all_violations: tuple[str, ...] | None = None,
    rules_version: int | None = None,
    rules_sha256: str | None = None,
    razorpay_tool: str | None = None,
    razorpay_outcome: str | None = None,
    order_id: str | None = None,
    payment_link_id: str | None = None,
    payment_id: str | None = None,
    checkout_status: str | None = None,
    error_detail: str | None = None,
) -> LedgerRow:
    payload = LedgerPayload(
        event_type=event_type.value,
        ts=render_ts(now),
        transaction_id=transaction_id,
        explanation=explanation,
        cart_id=cart_id,
        intent_id=intent_id,
        user_id=user_id,
        merchant_id=merchant_id,
        actor=actor,
        amount_paise=amount_paise,
        human_present=human_present,
        intent_digest=intent_digest,
        intent_signature=intent_signature,
        cart_digest=cart_digest,
        cart_signature=cart_signature,
        mandate_error_code=mandate_error_code,
        mandate_error_message=mandate_error_message,
        decision=decision,
        rule_fired=rule_fired,
        reason=reason,
        all_violations=all_violations,
        rules_version=rules_version,
        rules_sha256=rules_sha256,
        razorpay_tool=razorpay_tool,
        razorpay_outcome=razorpay_outcome,
        order_id=order_id,
        payment_link_id=payment_link_id,
        payment_id=payment_id,
        checkout_status=checkout_status,
        error_detail=error_detail,
    )
    # model_dump(mode="json") with NO exclude_none -- every field is present
    # in the dumped dict regardless of whether it's None, which is exactly
    # what makes "None" and "missing" byte-identical-by-construction.
    payload_canonical = canonical_json(payload.model_dump(mode="json"))
    payload_canonical_str = payload_canonical.decode("utf-8")
    razorpay_refs = _razorpay_refs_json(payload)

    last_error: Exception | None = None
    for _ in range(_MAX_RETRIES):
        session = SessionLocal()
        try:
            session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT_MS}ms'"))
            session.execute(text("SELECT pg_advisory_xact_lock(2, 0)"))

            tail = session.execute(
                select(LedgerRow.seq, LedgerRow.row_hash).order_by(LedgerRow.seq.desc()).limit(1)
            ).one_or_none()
            next_seq = 0 if tail is None else tail.seq + 1
            prev_hash = GENESIS_PREV_HASH if tail is None else tail.row_hash
            row_hash = chain_hash(prev_hash, payload_canonical)

            row = LedgerRow(
                seq=next_seq,
                ts=payload.ts,
                event_type=payload.event_type,
                transaction_id=payload.transaction_id,
                cart_id=payload.cart_id,
                intent_id=payload.intent_id,
                actor=payload.actor,
                decision=payload.decision,
                rule_fired=payload.rule_fired,
                razorpay_refs=razorpay_refs,
                explanation=explanation,
                payload_canonical=payload_canonical_str,
                prev_hash=prev_hash,
                row_hash=row_hash,
            )
            session.add(row)
            session.commit()
            # Out-of-DB copy of the head, per the honest tail-truncation
            # mitigation: a chain that verifies internally can still have had
            # its tail deleted, which is invisible without an anchor outside
            # the database itself. This is the cheapest one.
            print(f"[ledger] seq={next_seq} event={payload.event_type} head={row_hash}", flush=True)
            session.refresh(row)
            return row
        except IntegrityError as exc:
            session.rollback()
            last_error = exc
            continue
        finally:
            session.close()

    raise RuntimeError(f"ledger append failed after {_MAX_RETRIES} retries") from last_error


def _razorpay_refs_json(payload: LedgerPayload) -> str | None:
    refs = {
        k: v
        for k, v in (
            ("order_id", payload.order_id),
            ("payment_link_id", payload.payment_link_id),
            ("payment_id", payload.payment_id),
        )
        if v is not None
    }
    if not refs:
        return None
    return canonical_json(refs).decode("utf-8")
