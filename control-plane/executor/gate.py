"""The gate: the only place mandate verification, nonce consumption, policy
evaluation, and audit logging are wired together in front of the executor.
This module plus executor/checkout.py are the entire money path -- there is
no other code path that calls a money-moving Razorpay API.

Flow:
    mint transaction_id -> log REQUEST_RECEIVED
    -> idempotency check (by cart_id)     -> log IDEMPOTENT_REPLAY, return
    -> acquire a per-user Postgres advisory lock (closes the velocity TOCTOU:
       two concurrent carts from one user must not both read an empty
       window and both pass)
    -> verify_mandate_chain(now)  -> log MANDATE_VERIFIED / MANDATE_REJECTED
    -> consume_cart_nonce()       -> log NONCE_CONSUMED / NONCE_REPLAY_REJECTED
       [its own session -- does not touch, and cannot release, the lock
       just acquired: see the F2 fix in mandates/nonce.py]
    -> load_recent_spend() + count_pending()
    -> evaluate()                 -> log POLICY_VERDICT
       DENY     -> rollback (releases the lock; nothing was written), 403
       STEP_UP  -> persist a step_up_request, log STEP_UP_QUEUED,
                   COMMIT (releases the lock), 202
       ALLOW    -> reserve_spend PENDING, log SPEND_RESERVED,
                   COMMIT (releases the lock) -- only now, with no lock
                   held, do we make the slow Razorpay call
    -> run_demo_checkout()  -> (per MCP call: RAZORPAY_CALL, logged inside
       executor/checkout.py) -> mark the reservation COMMITTED or FAILED,
       log EXECUTION_COMMITTED / EXECUTION_DEDUPED / EXECUTION_FAILED, 200/502

Ledger write policy (see ledger/writer.py's own docstring for the
mechanism): every event through POLICY_VERDICT/STEP_UP_QUEUED/SPEND_RESERVED
is PRE-money-move and therefore fail-closed -- if the ledger cannot durably
record what we're about to decide, we must not decide it, and
append_event()'s own exception (after its bounded retries) is allowed to
propagate and abort the request. Once run_demo_checkout has actually been
called, a ledger-write failure is logged loudly to stderr but does NOT fail
the request -- the money already moved (or a real external call was
attempted); refusing to respond just confuses the client into a retry.

Known simplification, stated rather than hidden: this endpoint stays
`async def` with inline synchronous DB calls (matching the rest of this
codebase), rather than off-loading them to a thread pool. The advisory lock
is held only through the brief local-DB critical section above -- never
across the Razorpay network call -- so the actual event-loop blocking window
is small. Fully non-blocking DB access is a reasonable follow-up, not a
correctness issue at this scale.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from executor.checkout import idempotency_key_for_cart, run_demo_checkout
from executor.razorpay_mcp import reraise_unwrapped
from executor.spend import load_recent_spend, mark_committed, mark_failed, reserve_spend
from executor.step_up import count_pending, create_step_up_request
from ledger.events import LedgerEvent
from ledger.writer import append_event, append_event_best_effort
from mandates.canonical import signing_bytes
from mandates.errors import MandateError, MandateErrorCode
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


def _mandate_digest(mandate: IntentMandate | CartMandate) -> str:
    """Over signing_bytes(), not model_dump() -- covers exactly what the
    Ed25519 signature covers (including the domain tag), so the digest is a
    faithful proxy for "this exact signed object", not an approximation.
    """
    return hashlib.sha256(signing_bytes(mandate)).hexdigest()


def _log(**kwargs) -> None:
    """PRE-money-move: fail closed. If the ledger write ultimately fails
    (after append_event's own bounded retries), the exception propagates and
    the request aborts -- an unaudited decision must not be made.
    """
    append_event(**kwargs)


# POST-money-move logging (after run_demo_checkout is invoked) uses
# ledger.writer.append_event_best_effort directly -- see its docstring for
# the fail-open policy, shared with executor/checkout.py's own RAZORPAY_CALL
# logging so the policy lives in exactly one place.
_log_best_effort = append_event_best_effort


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

    Uses the SINGLE-ARG bigint form. The ledger's own append lock
    (ledger/writer.py) deliberately uses the TWO-ARG form instead, which
    Postgres documents as a separate key space -- confirmed live -- so the
    two can never collide regardless of what hashtext(user_id) happens to
    equal.
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
    transaction_id = str(uuid.uuid4())

    _log(
        event_type=LedgerEvent.REQUEST_RECEIVED,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart.cart_id,
        intent_id=intent.intent_id,
        user_id=intent.user_id,
        merchant_id=cart.merchant_id,
        actor=intent.user_id,
        amount_paise=cart.total_paise,
        explanation=f"checkout request received for cart {cart.cart_id}",
    )

    # Idempotency FIRST, before touching the nonce: a legitimate retry with
    # the same cart must get the cached result, not a spurious replay error.
    idempotency_key = idempotency_key_for_cart(cart.cart_id)
    existing = db.query(DemoCheckout).filter(DemoCheckout.idempotency_key == idempotency_key).one_or_none()
    if existing is not None:
        _log(
            event_type=LedgerEvent.IDEMPOTENT_REPLAY,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            checkout_status=existing.status,
            explanation=f"duplicate request for cart {cart.cart_id}; cached checkout status is {existing.status}",
        )
        # No fresh policy decision was made -- report the cached row's ACTUAL
        # status (COMMITTED, FAILED, or IN_FLIGHT), not a fabricated verdict.
        return ReplayResult(checkout=_checkout_response(existing, replayed=True))

    _acquire_user_lock(db, intent.user_id)

    active_keyring = keyring if keyring is not None else get_keyring()
    intent_digest = _mandate_digest(intent)
    cart_digest = _mandate_digest(cart)

    try:
        verified = verify_mandate_chain(intent, cart, active_keyring, now)
    except MandateError as exc:
        _log(
            event_type=LedgerEvent.MANDATE_REJECTED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            user_id=intent.user_id,
            merchant_id=cart.merchant_id,
            actor=intent.user_id,
            intent_digest=intent_digest,
            intent_signature=intent.signature,
            cart_digest=cart_digest,
            cart_signature=cart.signature,
            mandate_error_code=exc.code.value,
            mandate_error_message=exc.message,
            explanation=f"({exc.code.value}) {exc.message}",
        )
        raise

    _log(
        event_type=LedgerEvent.MANDATE_VERIFIED,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart.cart_id,
        intent_id=intent.intent_id,
        user_id=intent.user_id,
        merchant_id=cart.merchant_id,
        actor=intent.user_id,
        intent_digest=intent_digest,
        intent_signature=intent.signature,
        cart_digest=cart_digest,
        cart_signature=cart.signature,
        explanation="cart is within the intent's scope",
    )

    try:
        consume_cart_nonce(cart_id=cart.cart_id, nonce=cart.nonce)  # own session; does not touch our lock
    except MandateError as exc:
        _log(
            event_type=LedgerEvent.NONCE_REPLAY_REJECTED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            mandate_error_code=exc.code.value,
            mandate_error_message=exc.message,
            explanation=exc.message,
        )
        raise

    _log(
        event_type=LedgerEvent.NONCE_CONSUMED,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart.cart_id,
        intent_id=intent.intent_id,
        actor=intent.user_id,
        explanation="cart nonce consumed; this exact cart can never be replayed again",
    )

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

    _log(
        event_type=LedgerEvent.POLICY_VERDICT,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart.cart_id,
        intent_id=intent.intent_id,
        user_id=intent.user_id,
        merchant_id=cart.merchant_id,
        actor="system",
        amount_paise=cart.total_paise,
        decision=verdict.decision.value,
        rule_fired=verdict.rule_fired,
        reason=verdict.reason,
        all_violations=tuple(f"{v.rule_fired}: {v.reason}" for v in verdict.all_violations),
        rules_version=verdict.rules_version,
        rules_sha256=verdict.rules_sha256,
        explanation=verdict.reason,
    )

    if verdict.decision is Decision.DENY:
        db.rollback()  # nothing else was written; this also releases the lock
        return DenyResult(verdict=verdict)

    if verdict.decision is Decision.STEP_UP:
        step_up_row = create_step_up_request(db, intent=intent, cart=cart, verdict=verdict)
        _log(
            event_type=LedgerEvent.STEP_UP_QUEUED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            decision=verdict.decision.value,
            rule_fired=verdict.rule_fired,
            explanation=f"(rule {verdict.rule_fired}) {verdict.reason}",
        )
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
    _log(
        event_type=LedgerEvent.SPEND_RESERVED,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart.cart_id,
        intent_id=intent.intent_id,
        actor=intent.user_id,
        amount_paise=cart.total_paise,
        explanation=f"{cart.total_paise} paise reserved against the hourly velocity budget",
    )

    try:
        checkout = await run_demo_checkout(
            db=db,
            cart_id=cart.cart_id,
            amount_paise=cart.total_paise,
            description=describe_cart(cart),
            transaction_id=transaction_id,
        )
    except* Exception as eg:
        # Same reasoning as executor/checkout.py's except* -- defense in
        # depth in case a wrapped exception somehow escapes checkout.py's own
        # handling. Any failure here must mark the reservation FAILED, never
        # leave it PENDING (which would count against the user's velocity
        # budget forever with no corresponding audit).
        mark_failed(db, reservation)
        exc = eg.exceptions[0] if len(eg.exceptions) == 1 else eg
        _log_best_effort(
            event_type=LedgerEvent.EXECUTION_FAILED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            error_detail=type(exc).__name__,
            explanation="execution failed after the money-moving call was attempted",
        )
        reraise_unwrapped(eg)

    if checkout["replayed"]:
        _log_best_effort(
            event_type=LedgerEvent.EXECUTION_DEDUPED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            checkout_status=checkout["status"],
            explanation="a concurrent duplicate execution was deduplicated",
        )
    else:
        _log_best_effort(
            event_type=LedgerEvent.EXECUTION_COMMITTED,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart.cart_id,
            intent_id=intent.intent_id,
            actor=intent.user_id,
            amount_paise=cart.total_paise,
            order_id=checkout["order_id"],
            payment_link_id=checkout["payment_link_id"],
            payment_id=checkout["payment_id"],
            checkout_status=checkout["status"],
            explanation=f"order {checkout['order_id']} / payment link {checkout['payment_link_id']} created",
        )

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
