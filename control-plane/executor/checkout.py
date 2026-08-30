"""Phase 0 demo checkout: create_order -> create_payment_link -> fetch_payment_link,
with fetch_payment layered on only if the link already shows a completed payment.

No mandate verification and no policy gate here yet (Phase 1 / Phase 2). This
proves the Razorpay money path works; it is not yet bound by the Prime Directive.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from executor.razorpay_mcp import call_tool, razorpay_session
from models import DemoCheckout


def _to_response(row: DemoCheckout) -> dict:
    return {
        "idempotency_key": row.idempotency_key,
        "amount_paise": row.amount_paise,
        "order_id": row.order_id,
        "payment_link_id": row.payment_link_id,
        "short_url": row.short_url,
        "payment_link_status": row.payment_link_status,
        "payment_id": row.payment_id,
        "payment_status": row.payment_status,
        "replayed": True,
    }


async def run_demo_checkout(
    db: Session,
    amount_paise: int,
    idempotency_key: str,
    description: str | None,
) -> dict:
    existing = (
        db.query(DemoCheckout)
        .filter(DemoCheckout.idempotency_key == idempotency_key)
        .one_or_none()
    )
    if existing is not None:
        return _to_response(existing)

    receipt = idempotency_key[:40]

    async with razorpay_session() as session:
        order = await call_tool(
            session,
            "create_order",
            {"amount": amount_paise, "currency": "INR", "receipt": receipt},
        )

        link = await call_tool(
            session,
            "create_payment_link",
            {
                "amount": amount_paise,
                "currency": "INR",
                "reference_id": receipt,
                "description": description or "Pramaan Phase 0 demo checkout",
                "notes": {"order_id": order["id"]},
            },
        )

        link_status = await call_tool(
            session, "fetch_payment_link", {"payment_link_id": link["id"]}
        )

        payment_id = None
        payment_status = None
        payments = link_status.get("payments") or []
        if payments:
            payment_id = payments[0].get("payment_id") or payments[0].get("id")
        if payment_id:
            payment = await call_tool(session, "fetch_payment", {"payment_id": payment_id})
            payment_status = payment.get("status")

    row = DemoCheckout(
        idempotency_key=idempotency_key,
        amount_paise=amount_paise,
        order_id=order["id"],
        payment_link_id=link["id"],
        short_url=link["short_url"],
        payment_link_status=link_status.get("status", link.get("status")),
        payment_id=payment_id,
        payment_status=payment_status,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent request with the same idempotency key won the race —
        # discard our insert and return the row it wrote instead.
        db.rollback()
        existing = (
            db.query(DemoCheckout)
            .filter(DemoCheckout.idempotency_key == idempotency_key)
            .one()
        )
        return _to_response(existing)

    db.refresh(row)
    response = _to_response(row)
    response["replayed"] = False
    return response
