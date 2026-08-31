from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class DemoCheckout(Base):
    """One row per checkout, keyed on an idempotency key derived from the
    signed cart's cart_id (sha256 hex digest -- fits String(64) exactly and
    is collision-resistant, unlike truncating an arbitrary caller-supplied
    key). Superseded by the hash-chained ledger in Phase 3 -- this table is
    not the audit ledger.

    Two-phase write: a row is inserted with status=IN_FLIGHT and committed
    BEFORE any Razorpay call, so a concurrent duplicate request loses on the
    idempotency_key unique constraint instead of both requests creating a
    real order + payment link. It is then updated to COMMITTED (with the
    Razorpay refs filled in) or FAILED.
    """

    __tablename__ = "demo_checkouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cart_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="IN_FLIGHT")
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_link_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    short_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_link_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
