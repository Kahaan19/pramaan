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
"""

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from executor.razorpay_mcp import RazorpayToolError, call_tool, razorpay_session
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
            order = await call_tool(
                session,
                "create_order",
                {"amount": amount_paise, "currency": "INR", "receipt": reference_id},
            )

            link = await call_tool(
                session,
                "create_payment_link",
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "reference_id": reference_id,
                    "description": description or "Pramaan demo checkout",
                    "notes": {"order_id": order["id"], "cart_id": cart_id},
                },
            )

            link_status = await call_tool(session, "fetch_payment_link", {"payment_link_id": link["id"]})

            payment_id = None
            payment_status = None
            payments = link_status.get("payments") or []
            if payments:
                payment_id = payments[0].get("payment_id") or payments[0].get("id")
            if payment_id:
                payment = await call_tool(session, "fetch_payment", {"payment_id": payment_id})
                payment_status = payment.get("status")
    except RazorpayToolError:
        row.status = "FAILED"
        db.commit()
        raise

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
