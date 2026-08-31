"""Pending human-approval requests. Phase 2 only creates and counts these
rows (closing the "flood the approval queue" hole via max_pending_step_ups);
Phase 4 builds the actual approval UI and executor call.

A pending STEP_UP request does NOT count toward velocity -- it hasn't
executed and may never be approved. It counts only toward
max_pending_step_ups, a separate limit purpose-built for this scenario: an
agent that creates unlimited STEP_UP-eligible carts (each individually
passing velocity, none executed) to flood a human's queue.

Stores a full JSON snapshot of the verified, signed mandate -- not just IDs
-- so a future approval endpoint re-verifies and re-charges from STORAGE,
never from a re-submitted request body. Without this, an agent could get a
small approved amount applied to a much larger cart at approval time.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from db import Base
from mandates.schemas import CartMandate, IntentMandate
from policy.verdict import Verdict


class StepUpRequest(Base):
    __tablename__ = "step_up_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cart_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    intent_id: Mapped[str] = mapped_column(String(128))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    rule_fired: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING|APPROVED|REJECTED|EXPIRED
    intent_json: Mapped[str] = mapped_column(Text)
    cart_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Bounded by the intent's own expiry -- an approval can never outlive
    # the authorization it's approving.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def count_pending(db: Session, user_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(StepUpRequest)
        .where(StepUpRequest.user_id == user_id, StepUpRequest.status == "PENDING")
    ).scalar_one()


def create_step_up_request(
    db: Session, *, intent: IntentMandate, cart: CartMandate, verdict: Verdict
) -> StepUpRequest:
    row = StepUpRequest(
        cart_id=cart.cart_id,
        user_id=intent.user_id,
        intent_id=intent.intent_id,
        amount_paise=cart.total_paise,
        rule_fired=verdict.rule_fired or "",
        reason=verdict.reason,
        status="PENDING",
        intent_json=intent.model_dump_json(),
        cart_json=cart.model_dump_json(),
        expires_at=intent.expires_at,
    )
    db.add(row)
    # Commits on the caller's session -- same linearization-point role as
    # executor/spend.py::reserve_spend: this is what releases the per-user
    # advisory lock taken in executor/gate.py.
    db.commit()
    db.refresh(row)
    return row
