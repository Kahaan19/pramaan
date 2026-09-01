"""Checkout execution: create_order -> create_payment_link -> fetch_payment_link,
with fetch_payment layered on only if the link already shows a completed payment.

Two-phase write around the Razorpay calls (see models.DemoCheckout): a row is
claimed as IN_FLIGHT and committed BEFORE any Razorpay call, then updated to
COMMITTED or FAILED. This closes two bugs that existed when the claim row was
written only after the calls returned:
  - concurrent duplicate requests would both create a real order + payment
    link before the idempotency check could reject either of them;
  - reference_id was derived from a truncated, caller-supplied idempotency
    key, so two different carts could collide on the same Razorpay
    reference_id (which Razorpay enforces as unique) and get a spurious 502.

reference_id and idempotency_key are both derived from cart_id via sha256, so
they're deterministic, collision-resistant, and (for idempotency_key) fit the
DemoCheckout column exactly regardless of the mandate layer's longer cart_id
length allowance.

No policy gate here (Phase 2 policy/ + executor/gate.py sit in front of this).
This module is the sole money path; it does not decide whether a transaction
is *allowed*, only how to execute one that already has been.

A replay of this function (the `existing is not None` returns below) reports
the CACHED row's actual status verbatim -- including FAILED or IN_FLIGHT.
Callers must not assume a replay means success; see executor/gate.py's
ReplayResult, which exists specifically because an earlier version of this
codebase fabricated an ALLOW verdict for any replay regardless of status.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from executor.razorpay_mcp import call_tool, razorpay_session, reraise_unwrapped
from ledger.events import LedgerEvent
from ledger.writer import append_event_best_effort
from mcp import ClientSession
from models import DemoCheckout


def reference_id_for_cart(cart_id: str) -> str:
    """Deterministic, collision-resistant Razorpay reference_id (<=40 chars)."""
    digest = hashlib.sha256(cart_id.encode("utf-8")).hexdigest()
    return f"pmn_{digest[:36]}"


def idempotency_key_for_cart(cart_id: str) -> str:
    """Deterministic idempotency key -- a 64-char hex digest, fitting
    DemoCheckout.idempotency_key's String(64) column exactly regardless of
    how long the signed cart_id itself is (mandates allow up to 128 chars).
    """
    return hashlib.sha256(cart_id.encode("utf-8")).hexdigest()


async def _call_and_log(
    session: ClientSession, tool: str, arguments: dict, *, transaction_id: str, cart_id: str
) -> dict:
    """Every MCP tool call gets its own RAZORPAY_CALL ledger row (CLAUDE.md:
    "every Razorpay call writes exactly one row" -- taken literally, since
    a checkout makes 3-4 separate calls). Best-effort: by the time we're
    calling Razorpay at all, the reservation already exists (executor/gate.py
    logs SPEND_RESERVED first), so we're past the fail-closed boundary.
    """
    now = datetime.now(timezone.utc)
    try:
        result = await call_tool(session, tool, arguments)
    except Exception as exc:
        append_event_best_effort(
            event_type=LedgerEvent.RAZORPAY_CALL,
            now=now,
            transaction_id=transaction_id,
            cart_id=cart_id,
            razorpay_tool=tool,
            razorpay_outcome="error",
            error_detail=type(exc).__name__,
            explanation=f"{tool} failed",
        )
        raise
    ref_id = result.get("id") or result.get("payment_link_id") or result.get("payment_id")
    append_event_best_effort(
        event_type=LedgerEvent.RAZORPAY_CALL,
        now=now,
        transaction_id=transaction_id,
        cart_id=cart_id,
        razorpay_tool=tool,
        razorpay_outcome="ok",
        order_id=ref_id if tool == "create_order" else None,
        payment_link_id=ref_id if tool in ("create_payment_link", "fetch_payment_link") else None,
        payment_id=ref_id if tool == "fetch_payment" else None,
        explanation=f"{tool} succeeded",
    )
    return result


def _to_response(row: DemoCheckout, replayed: bool) -> dict:
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


async def run_demo_checkout(
    db: Session,
    cart_id: str,
    amount_paise: int,
    description: str | None,
    transaction_id: str,
) -> dict:
    idempotency_key = idempotency_key_for_cart(cart_id)

    existing = (
        db.query(DemoCheckout).filter(DemoCheckout.idempotency_key == idempotency_key).one_or_none()
    )
    if existing is not None:
        return _to_response(existing, replayed=True)

    row = DemoCheckout(
        idempotency_key=idempotency_key,
        cart_id=cart_id,
        status="IN_FLIGHT",
        amount_paise=amount_paise,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent request for the same cart won the race -- discard our
        # insert attempt and return whatever it wrote (possibly still
        # IN_FLIGHT if it hasn't finished yet).
        db.rollback()
        existing = db.query(DemoCheckout).filter(DemoCheckout.idempotency_key == idempotency_key).one()
        return _to_response(existing, replayed=True)
    db.refresh(row)

    reference_id = reference_id_for_cart(cart_id)

    try:
        async with razorpay_session() as session:
            order = await _call_and_log(
                session,
                "create_order",
                {"amount": amount_paise, "currency": "INR", "receipt": reference_id},
                transaction_id=transaction_id,
                cart_id=cart_id,
            )

            link = await _call_and_log(
                session,
                "create_payment_link",
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "reference_id": reference_id,
                    "description": description or "Pramaan demo checkout",
                    "notes": {"order_id": order["id"], "cart_id": cart_id},
                },
                transaction_id=transaction_id,
                cart_id=cart_id,
            )

            link_status = await _call_and_log(
                session,
                "fetch_payment_link",
                {"payment_link_id": link["id"]},
                transaction_id=transaction_id,
                cart_id=cart_id,
            )

            payment_id = None
            payment_status = None
            payments = link_status.get("payments") or []
            if payments:
                payment_id = payments[0].get("payment_id") or payments[0].get("id")
            if payment_id:
                payment = await _call_and_log(
                    session,
                    "fetch_payment",
                    {"payment_id": payment_id},
                    transaction_id=transaction_id,
                    cart_id=cart_id,
                )
                payment_status = payment.get("status")
    except* Exception as eg:
        # `except*` (not a plain `except RazorpayToolError`) because the mcp
        # SDK's transport runs on anyio task groups, which wrap ANY exception
        # raised inside -- including RazorpayToolError -- in a
        # BaseExceptionGroup. A plain `except RazorpayToolError` misses that
        # wrapped case entirely (confirmed live in this session: a JSON parse
        # error surfaced as an unhandled ExceptionGroup), leaving this row
        # stuck IN_FLIGHT forever with no record of the failure. Catching
        # `Exception` (not just RazorpayToolError) is deliberate too: ANY
        # failure here must mark the row FAILED, not just a Razorpay-shaped one.
        row.status = "FAILED"
        db.commit()
        reraise_unwrapped(eg)

    row.order_id = order["id"]
    row.payment_link_id = link["id"]
    row.short_url = link["short_url"]
    row.payment_link_status = link_status.get("status", link.get("status"))
    row.payment_id = payment_id
    row.payment_status = payment_status
    row.status = "COMMITTED"
    db.commit()
    db.refresh(row)

    return _to_response(row, replayed=False)
