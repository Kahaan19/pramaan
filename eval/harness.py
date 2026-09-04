"""Runs one Attempt through the REAL gate (executor/gate.py::run_gate) --
real Ed25519 verification, real scope checks, the real Postgres nonce store,
real velocity accounting, the real policy engine over the shipped
policies/rules.yaml, and real hash-chained ledger writes. The only thing
stubbed is the Razorpay network call itself (fake_run_demo_checkout below),
mirroring control-plane/tests/test_gate.py's own `_fake_run_demo_checkout` --
same shape, same DemoCheckout bookkeeping, so the gate's idempotency-cache
path (needed for the L4/legit-retry case) still works exactly as it does in
production.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from eval.batch import Attempt
from executor.gate import AllowResult, DenyResult, ReplayResult, StepUpResult, run_gate
from executor.checkout import idempotency_key_for_cart
from ledger.models import LedgerRow
from mandates.errors import MandateError
from mandates.keys import Keyring
from models import DemoCheckout

OUTCOME_EXECUTED = "executed"
OUTCOME_BLOCKED = "blocked"
OUTCOME_ESCALATED = "escalated"

CONTROL_MANDATE = "mandate"
CONTROL_POLICY = "policy"
CONTROL_IDEMPOTENCY = "idempotency"


async def fake_run_demo_checkout(db: Session, cart_id: str, amount_paise: int, description: str, transaction_id: str) -> dict:
    """Stands in for executor.checkout.run_demo_checkout -- no live Razorpay
    calls in the eval batch. Deterministic per cart_id so re-running the
    batch produces the same fake refs.
    """
    idempotency_key = idempotency_key_for_cart(cart_id)
    existing = db.query(DemoCheckout).filter(DemoCheckout.idempotency_key == idempotency_key).one_or_none()
    if existing is not None:
        return _checkout_dict(existing, replayed=True)

    row = DemoCheckout(
        idempotency_key=idempotency_key,
        cart_id=cart_id,
        status="COMMITTED",
        amount_paise=amount_paise,
        order_id=f"order_eval_{idempotency_key[:16]}",
        payment_link_id=f"plink_eval_{idempotency_key[:16]}",
        short_url=f"https://rzp.io/eval/{idempotency_key[:12]}",
        payment_link_status="created",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _checkout_dict(row, replayed=False)


def _checkout_dict(row: DemoCheckout, replayed: bool) -> dict:
    return {
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "amount_paise": row.amount_paise,
        "order_id": row.order_id,
        "payment_link_id": row.payment_link_id,
        "short_url": row.short_url,
        "payment_link_status": row.payment_link_status,
        "payment_id": row.payment_id,
        "payment_status": row.payment_status,
        "replayed": replayed,
    }


@dataclass(frozen=True)
class AttemptResult:
    attempt: Attempt
    outcome: str  # executed | blocked | escalated
    actual_decision: str | None  # ALLOW | STEP_UP | DENY | None (mandate error / replay)
    actual_control: str | None  # mandate | policy | idempotency
    rule_fired: str | None
    mandate_error_code: str | None
    checkout_status: str | None
    transaction_id: str | None


def _lookup_transaction_id(db: Session, cart_id: str) -> str | None:
    """The gate mints a fresh transaction_id per call and unconditionally
    logs REQUEST_RECEIVED under it as the very first thing it does -- even on
    a replay (see executor/gate.py::run_gate). run_gate itself never returns
    this id to the caller, so we read it back off the ledger the same way
    routers/ledger.py's /recent endpoint reads transaction summaries: by
    querying LedgerRow directly. Ordered by seq desc because a reused
    cart_id (L4, M9) has more than one REQUEST_RECEIVED row -- we want THIS
    call's.
    """
    row = (
        db.execute(
            select(LedgerRow)
            .where(LedgerRow.cart_id == cart_id, LedgerRow.event_type == "REQUEST_RECEIVED")
            .order_by(LedgerRow.seq.desc())
        )
        .scalars()
        .first()
    )
    return row.transaction_id if row else None


async def run_attempt(db: Session, attempt: Attempt, keyring: Keyring) -> AttemptResult:
    try:
        result = await run_gate(db, attempt.intent, attempt.cart, keyring=keyring)
    except MandateError as exc:
        transaction_id = _lookup_transaction_id(db, attempt.cart.cart_id)
        return AttemptResult(
            attempt=attempt,
            outcome=OUTCOME_BLOCKED,
            actual_decision=None,
            actual_control=CONTROL_MANDATE,
            rule_fired=None,
            mandate_error_code=exc.code.value,
            checkout_status=None,
            transaction_id=transaction_id,
        )

    if isinstance(result, DenyResult):
        outcome, decision, control = OUTCOME_BLOCKED, "DENY", CONTROL_POLICY
        rule_fired, checkout_status = result.verdict.rule_fired, None
    elif isinstance(result, StepUpResult):
        outcome, decision, control = OUTCOME_ESCALATED, "STEP_UP", CONTROL_POLICY
        rule_fired, checkout_status = result.verdict.rule_fired, None
    elif isinstance(result, AllowResult):
        checkout_status = result.checkout["status"]
        outcome = OUTCOME_EXECUTED if checkout_status == "COMMITTED" else OUTCOME_BLOCKED
        decision, control, rule_fired = "ALLOW", CONTROL_POLICY, None
    elif isinstance(result, ReplayResult):
        checkout_status = result.checkout["status"]
        outcome = {"COMMITTED": OUTCOME_EXECUTED, "FAILED": OUTCOME_BLOCKED}.get(checkout_status, OUTCOME_ESCALATED)
        decision, control, rule_fired = None, CONTROL_IDEMPOTENCY, None
    else:  # pragma: no cover -- GateResult is a closed union
        raise TypeError(f"unexpected GateResult type: {type(result)!r}")

    transaction_id = _lookup_transaction_id(db, attempt.cart.cart_id)
    return AttemptResult(
        attempt=attempt,
        outcome=outcome,
        actual_decision=decision,
        actual_control=control,
        rule_fired=rule_fired,
        mandate_error_code=None,
        checkout_status=checkout_status,
        transaction_id=transaction_id,
    )
