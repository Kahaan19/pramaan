"""Cart-nonce replay protection -- the one stateful piece of the mandate
layer. A cart mandate's nonce is single-use: it is consumed atomically here,
BEFORE the policy engine runs (Phase 2), so even a policy-DENIED cart burns
its nonce and a retry needs a freshly signed cart. STEP_UP approval later
must NOT call this again.

The Intent mandate is reusable until its own expires_at -- only cart nonces
are tracked here.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from db import Base
from mandates.errors import MandateError, MandateErrorCode


class MandateNonce(Base):
    __tablename__ = "mandate_nonces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16))
    nonce: Mapped[str] = mapped_column(String(128))
    cart_id: Mapped[str] = mapped_column(String(128))
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("scope", "nonce", name="uq_mandate_nonce_scope_nonce"),)


def consume_cart_nonce(db: Session, cart_id: str, nonce: str) -> None:
    """Atomically consumes a cart nonce. Commits immediately in its own
    transaction -- NOT left pending in the caller's request-scoped
    transaction -- so a later policy DENY or an unrelated rollback cannot
    silently un-burn the mandate. Two concurrent requests with the same
    nonce are settled by the unique constraint: exactly one succeeds.
    """
    db.add(MandateNonce(scope="cart", nonce=nonce, cart_id=cart_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise MandateError(
            MandateErrorCode.REPLAYED_NONCE, f"cart nonce {nonce!r} (cart_id={cart_id!r}) already consumed"
        ) from None
