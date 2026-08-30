from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class DemoCheckout(Base):
    """Phase 0 spine record: one row per idempotency key, proving Postgres + the
    Razorpay MCP money path work end to end. Superseded by the hash-chained
    ledger in Phase 3 — this table is not the audit ledger.
    """

    __tablename__ = "demo_checkouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    order_id: Mapped[str] = mapped_column(String(64))
    payment_link_id: Mapped[str] = mapped_column(String(64))
    short_url: Mapped[str] = mapped_column(String(255))
    payment_link_status: Mapped[str] = mapped_column(String(32))
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
