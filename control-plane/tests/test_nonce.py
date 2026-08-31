import uuid

import pytest
from sqlalchemy import select

from mandates.errors import MandateError, MandateErrorCode
from mandates.nonce import MandateNonce, consume_cart_nonce


def test_first_consumption_succeeds(db_session):
    nonce = uuid.uuid4().hex
    consume_cart_nonce(db_session, cart_id="cart_a", nonce=nonce)  # must not raise


def test_replayed_nonce_rejected(db_session):
    nonce = uuid.uuid4().hex
    consume_cart_nonce(db_session, cart_id="cart_a", nonce=nonce)
    with pytest.raises(MandateError) as exc_info:
        consume_cart_nonce(db_session, cart_id="cart_a", nonce=nonce)
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE


def test_different_nonces_both_succeed(db_session):
    consume_cart_nonce(db_session, cart_id="cart_a", nonce=uuid.uuid4().hex)
    consume_cart_nonce(db_session, cart_id="cart_b", nonce=uuid.uuid4().hex)  # must not raise


def test_nonce_survives_a_later_rollback_in_callers_transaction(db_session):
    """Regression guard for the 'own transaction' requirement: consuming a
    nonce must commit immediately, so a later rollback elsewhere in the same
    session cannot silently un-burn it.
    """
    nonce = uuid.uuid4().hex
    consume_cart_nonce(db_session, cart_id="cart_a", nonce=nonce)

    # Simulate later work in the same session failing and rolling back.
    db_session.add(MandateNonce(scope="cart", nonce=uuid.uuid4().hex, cart_id="cart_unrelated"))
    db_session.rollback()

    row = db_session.execute(
        select(MandateNonce).where(MandateNonce.scope == "cart", MandateNonce.nonce == nonce)
    ).scalar_one_or_none()
    assert row is not None

    # And the nonce is still enforced as consumed.
    with pytest.raises(MandateError) as exc_info:
        consume_cart_nonce(db_session, cart_id="cart_a", nonce=nonce)
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE
