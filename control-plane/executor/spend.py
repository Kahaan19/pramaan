"""Spend reservations: the impure boundary between the pure policy engine
and Postgres. Meters AUTHORIZED SPEND COMMITMENTS, not settlements -- the
executor only ever observes a created payment link (executor/checkout.py),
never a completed payment, so counting only completed payments would mean
velocity could never fire. A payment link created under a passing verdict is
an obligation against the user's hourly budget; an unpaid link still
consumes budget for the window. Over-counting is the fail-closed direction;
under-counting is the hole.

Reserve -> act -> confirm: a PENDING row is inserted and committed (releasing
the caller's advisory lock -- see executor/gate.py) BEFORE the Razorpay call,
then updated to COMMITTED or FAILED afterward. Velocity counts
PENDING + COMMITTED, never FAILED, so a crash between reservation and
confirmation over-counts rather than under-counts.

This table is deliberately MUTABLE operational state -- NOT the Phase 3
hash-chained ledger, which will be append-only. Do not make this table
append-only (it would break reconciliation), and do not add UPDATEs to the
real ledger when it lands.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, DateTime, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from db import Base
from policy.context import SpendRecord


class SpendReservation(Base):
    __tablename__ = "spend_reservations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # UNIQUE is the last-ditch double-spend backstop: one signed cart can
    # produce at most one reservation, regardless of what else goes wrong.
    cart_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    intent_id: Mapped[str] = mapped_column(String(128))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))  # PENDING | COMMITTED | FAILED
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    payment_link_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def load_recent_spend(
    db: Session, user_id: str, now: datetime, window_seconds: int
) -> tuple[SpendRecord, ...]:
    """Prefetches a bit WIDER than the window and orders deterministically;
    the pure velocity rules apply the authoritative, exclusive-at-boundary
    cutoff themselves (policy/rules.py::_spend_in_window). This is not the
    source of truth for the window boundary -- there is only one of those,
    and it lives in the pure rule.
    """
    cutoff = now - timedelta(seconds=window_seconds)
    rows = (
        db.execute(
            select(SpendReservation)
            .where(
                SpendReservation.user_id == user_id,
                SpendReservation.status.in_(("PENDING", "COMMITTED")),
                SpendReservation.created_at >= cutoff,
            )
            .order_by(SpendReservation.created_at.asc(), SpendReservation.id.asc())
        )
        .scalars()
        .all()
    )
    return tuple(SpendRecord(amount_paise=r.amount_paise, created_at=r.created_at) for r in rows)


def reserve_spend(
    db: Session, *, cart_id: str, user_id: str, intent_id: str, amount_paise: int
) -> SpendReservation:
    """Inserts a PENDING reservation and commits on the CALLER's session.
    The caller (executor/gate.py) is expected to be holding a per-user
    Postgres advisory lock on this same session's transaction; this commit
    is the linearization point for that lock -- it ends the transaction and
    releases the lock, which is deliberate: velocity is now correctly
    accounted for, and the (slow) Razorpay call that follows does not need
    to hold any lock at all.
    """
    row = SpendReservation(
        cart_id=cart_id,
        user_id=user_id,
        intent_id=intent_id,
        amount_paise=amount_paise,
        status="PENDING",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_committed(db: Session, reservation: SpendReservation, payment_link_id: str) -> None:
    reservation.status = "COMMITTED"
    reservation.payment_link_id = payment_link_id
    reservation.settled_at = datetime.now(timezone.utc)
    db.commit()


def mark_failed(db: Session, reservation: SpendReservation) -> None:
    reservation.status = "FAILED"
    db.commit()
