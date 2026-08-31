import uuid

import pytest
from sqlalchemy import select

from mandates.errors import MandateError, MandateErrorCode
from mandates.nonce import MandateNonce, consume_cart_nonce


def test_first_consumption_succeeds():
    nonce = uuid.uuid4().hex
    consume_cart_nonce(cart_id="cart_a", nonce=nonce)  # must not raise


def test_replayed_nonce_rejected():
    nonce = uuid.uuid4().hex
    consume_cart_nonce(cart_id="cart_a", nonce=nonce)
    with pytest.raises(MandateError) as exc_info:
        consume_cart_nonce(cart_id="cart_a", nonce=nonce)
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE


def test_different_nonces_both_succeed():
    consume_cart_nonce(cart_id="cart_a", nonce=uuid.uuid4().hex)
    consume_cart_nonce(cart_id="cart_b", nonce=uuid.uuid4().hex)  # must not raise


def test_consume_cart_nonce_does_not_touch_callers_session(db_session):
    """F2 regression. A prior implementation took the caller's Session and
    called db.commit() on it, which commits EVERYTHING pending on that
    session -- not just the nonce row -- and would release any lock the
    caller holds (e.g. a Postgres advisory lock taken before this call).

    Proof: put unrelated work on the caller's session BEFORE consuming a
    nonce, then roll the caller's session back. The unrelated row must be
    gone (consume_cart_nonce never touched/committed it), but the nonce must
    still be enforced as consumed (it was committed on its own session).
    """
    unrelated_nonce = uuid.uuid4().hex
    db_session.add(MandateNonce(scope="cart", nonce=unrelated_nonce, cart_id="unrelated"))
    # NOTE: unrelated_nonce is pending, NOT committed, at this point.

    consumed_nonce = uuid.uuid4().hex
    consume_cart_nonce(cart_id="cart_a", nonce=consumed_nonce)

    db_session.rollback()

    # The caller's own pending row was rolled back -- consume_cart_nonce did
    # not commit it out from under the caller.
    survivor = db_session.execute(
        select(MandateNonce).where(MandateNonce.nonce == unrelated_nonce)
    ).scalar_one_or_none()
    assert survivor is None

    # But the nonce consumed via its own session is still enforced.
    with pytest.raises(MandateError) as exc_info:
        consume_cart_nonce(cart_id="cart_a", nonce=consumed_nonce)
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE
