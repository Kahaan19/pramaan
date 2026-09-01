"""Stage 3a regression: mark_committed must reject a null/empty
payment_link_id rather than silently writing a COMMITTED row with no proof
anything was actually created.
"""

import uuid

import pytest

from executor.spend import mark_committed, reserve_spend


def _make_reservation(db_session):
    return reserve_spend(
        db_session,
        cart_id="cart_" + uuid.uuid4().hex,
        user_id="user_" + uuid.uuid4().hex,
        intent_id="intent_" + uuid.uuid4().hex,
        amount_paise=5000,
    )


def test_mark_committed_rejects_none_payment_link_id(db_session):
    reservation = _make_reservation(db_session)
    with pytest.raises(ValueError):
        mark_committed(db_session, reservation, None)


def test_mark_committed_rejects_empty_payment_link_id(db_session):
    reservation = _make_reservation(db_session)
    with pytest.raises(ValueError):
        mark_committed(db_session, reservation, "")


def test_mark_committed_accepts_real_payment_link_id(db_session):
    reservation = _make_reservation(db_session)
    mark_committed(db_session, reservation, "plink_real")  # must not raise
    assert reservation.status == "COMMITTED"
    assert reservation.payment_link_id == "plink_real"
