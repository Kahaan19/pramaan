"""The gate: the only place mandate verification, nonce consumption, and
policy evaluation are wired together in front of the executor. This module
plus executor/checkout.py are the entire money path -- there is no other
code path that calls a money-moving Razorpay API.

Flow:
    idempotency check (by cart_id)          [return cached result]
    -> acquire a per-user Postgres advisory lock (closes the velocity TOCTOU:
       two concurrent carts from one user must not both read an empty
       window and both pass)
    -> verify_mandate_chain(now)             [MandateError -> distinct status]
    -> consume_cart_nonce()                  [its own session -- does not
       touch, and cannot release, the lock just acquired: see the F2 fix in
       mandates/nonce.py]
    -> load_recent_spend() + count_pending()
    -> evaluate()
       DENY     -> rollback (releases the lock; nothing was written), 403
       STEP_UP  -> persist a step_up_request, COMMIT (releases the lock), 202
       ALLOW    -> reserve_spend PENDING, COMMIT (releases the lock) -- only
                   now, with no lock held, do we make the slow Razorpay call
    -> run_demo_checkout()  -> mark the reservation COMMITTED or FAILED, 200/502

Known simplification, stated rather than hidden: this endpoint stays
`async def` with inline synchronous DB calls (matching the rest of this
codebase), rather than off-loading them to a thread pool. The advisory lock
is held only through the brief local-DB critical section above -- never
across the Razorpay network call -- so the actual event-loop blocking window
is small. Fully non-blocking DB access is a reasonable follow-up, not a
correctness issue at this scale.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from executor.checkout import idempotency_key_for_cart, run_demo_checkout
from executor.razorpay_mcp import reraise_unwrapped
from executor.spend import load_recent_spend, mark_committed, mark_failed, reserve_spend
from executor.step_up import count_pending, create_step_up_request
from mandates.errors import MandateErrorCode
from mandates.keys import Keyring, get_keyring
from mandates.nonce import consume_cart_nonce
from mandates.schemas import CartMandate, IntentMandate
from mandates.verify import verify_mandate_chain
from models import DemoCheckout
from policy.context import build_context
from policy.engine import evaluate
from policy.rules_schema import get_rules_config
from policy.verdict import Decision, Verdict

# Mandate failures that are NOT "replayed nonce" or "malformed" collapse to
# 403, same as a policy DENY -- all of them mean "this request is not
# authorized", just for different reasons, and the body always names which.
_MANDATE_STATUS_CODES: dict[MandateErrorCode, int] = {
    MandateErrorCode.MALFORMED: 422,
    MandateErrorCode.REPLAYED_NONCE: 409,
}
_DEFAULT_MANDATE_STATUS = 403


def mandate_error_status_code(code: MandateErrorCode) -> int:
    return _MANDATE_STATUS_CODES.get(code, _DEFAULT_MANDATE_STATUS)


def describe_cart(cart: CartMandate) -> str:
    """Derived ONLY from the signed cart -- never the request body. This is
    what a human sees when paying the link or reviewing a STEP_UP approval;
    letting a client-supplied field control it would let a rogue agent show
    a human "Tea, Rs 12.99" for a Rs 1,999 charge.
    """
    items_desc = ", ".join(f"{item.qty}x {item.sku}" for item in cart.items)
    return f"{items_desc} (Rs {cart.total_paise / 100:.2f})"


@dataclass(frozen=True)
class DenyResult:
    verdict: Verdict


@dataclass(frozen=True)
class StepUpResult:
    verdict: Verdict
    step_up_request_id: int


@dataclass(frozen=True)
class AllowResult:
    verdict: Verdict
    checkout: dict


@dataclass(frozen=True)
class ReplayResult:
    """A prior attempt against this exact cart already exists. Deliberately
    carries NO verdict -- a replay is not a fresh policy decision, and an
    earlier version of this code fabricated a synthetic Decision.ALLOW here
    regardless of the cached row's actual status, which meant a cached
    FAILED or IN_FLIGHT checkout was reported to the caller as a success.
    `checkout["status"]` is the cached row's real, current status; the
    caller (routers/demo.py) maps it to an honest HTTP status.
    """

    checkout: dict


GateResult = DenyResult | StepUpResult | AllowResult | ReplayResult


def _acquire_user_lock(db: Session, user_id: str) -> None:
    """A transaction-scoped advisory lock keyed on user_id. Auto-released the
    moment this session's current transaction commits or rolls back -- which
    is exactly what happens a few lines later in each branch below.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:user_id))"), {"user_id": user_id})


async def run_gate(
    db: Session, intent: IntentMandate, cart: CartMandate, keyring: Keyring | None = None
) -> GateResult:
    """keyring defaults to the service's real, disk-loaded Keyring
    (get_keyring()); tests inject their own in-memory Keyring so they never
    depend on secrets/ existing on disk.
    """
    now = datetime.now(timezone.utc)

    # Idempotency FIRST, before touching the nonce: a legitimate retry with
    # the same cart must get the cached result, not a spurious replay error.
    idempotency_key = idempotency_key_for_cart(cart.cart_id)
    existing = db.query(DemoCheckout).filter(DemoCheckout.idempotency_key == idempotency_key).one_or_none()
    if existing is not None:
        # No fresh policy decision was made -- report the cached row's ACTUAL
        # status (COMMITTED, FAILED, or IN_FLIGHT), not a fabricated verdict.
        return ReplayResult(checkout=_checkout_response(existing, replayed=True))

    _acquire_user_lock(db, intent.user_id)

    active_keyring = keyring if keyring is not None else get_keyring()
    verified = verify_mandate_chain(intent, cart, active_keyring, now)  # raises MandateError

    consume_cart_nonce(cart_id=cart.cart_id, nonce=cart.nonce)  # own session; does not touch our lock

    rules_config, rules_sha256 = get_rules_config()
    recent_spend = load_recent_spend(
        db, verified.intent.user_id, now, rules_config.limits.velocity.window_seconds
    )
    pending_step_up_count = count_pending(db, verified.intent.user_id)

    ctx = build_context(
        verified=verified,
        recent_spend=recent_spend,
        pending_step_up_count=pending_step_up_count,
        rules=rules_config,
        rules_sha256=rules_sha256,
    )
    verdict = evaluate(ctx)

    if verdict.decision is Decision.DENY:
        db.rollback()  # nothing was written; this also releases the lock
        return DenyResult(verdict=verdict)

    if verdict.decision is Decision.STEP_UP:
        step_up_row = create_step_up_request(db, intent=intent, cart=cart, verdict=verdict)
        return StepUpResult(verdict=verdict, step_up_request_id=step_up_row.id)

    # ALLOW: reserve BEFORE calling Razorpay. This commit releases the lock;
    # the Razorpay call below holds no lock at all.
    reservation = reserve_spend(
        db,
        cart_id=cart.cart_id,
        user_id=intent.user_id,
        intent_id=intent.intent_id,
        amount_paise=cart.total_paise,
    )

    try:
        checkout = await run_demo_checkout(
            db=db,
            cart_id=cart.cart_id,
            amount_paise=cart.total_paise,
            description=describe_cart(cart),
        )
    except* Exception as eg:
        # Same reasoning as executor/checkout.py's except* -- defense in
        # depth in case a wrapped exception somehow escapes checkout.py's own
        # handling. Any failure here must mark the reservation FAILED, never
        # leave it PENDING (which would count against the user's velocity
        # budget forever with no corresponding audit).
        mark_failed(db, reservation)
        reraise_unwrapped(eg)

    mark_committed(db, reservation, checkout["payment_link_id"])
    return AllowResult(verdict=verdict, checkout=checkout)


def _checkout_response(row: DemoCheckout, replayed: bool) -> dict:
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
