from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mandates.schemas import CartItem, UnsignedCartMandate, UnsignedIntentMandate

from .conftest import DEFAULT_ITEMS, DEFAULT_TOTAL_PAISE, MERCHANT_ID, USER_ID

VALID_INTENT_FIELDS = dict(
    intent_id="intent_1",
    user_id=USER_ID,
    max_amount_paise=200000,
    merchant_allowlist=[MERCHANT_ID],
    category="retail",
    expires_at="2026-09-05T23:59:59Z",
    human_present=True,
    nonce="n1",
)

VALID_CART_FIELDS = dict(
    cart_id="cart_1",
    intent_id="intent_1",
    merchant_id=MERCHANT_ID,
    items=DEFAULT_ITEMS,
    total_paise=DEFAULT_TOTAL_PAISE,
    nonce="n2",
)


def test_valid_intent_and_cart_construct():
    UnsignedIntentMandate(**VALID_INTENT_FIELDS)
    UnsignedCartMandate(**VALID_CART_FIELDS)


@pytest.mark.parametrize("field", ["max_amount_paise"])
def test_float_money_rejected_on_intent(field):
    fields = {**VALID_INTENT_FIELDS, field: 129900.0}
    with pytest.raises(ValidationError):
        UnsignedIntentMandate(**fields)


@pytest.mark.parametrize("field", ["total_paise"])
def test_float_money_rejected_on_cart(field):
    fields = {**VALID_CART_FIELDS, field: 129900.5}
    with pytest.raises(ValidationError):
        UnsignedCartMandate(**fields)


def test_float_unit_price_rejected_on_cart_item():
    with pytest.raises(ValidationError):
        CartItem(sku="x", qty=1, unit_price_paise=100.0)


def test_string_money_rejected_by_strict_int():
    fields = {**VALID_INTENT_FIELDS, "max_amount_paise": "200000"}
    with pytest.raises(ValidationError):
        UnsignedIntentMandate(**fields)


def test_zero_amount_rejected():
    fields = {**VALID_INTENT_FIELDS, "max_amount_paise": 0}
    with pytest.raises(ValidationError):
        UnsignedIntentMandate(**fields)


def test_negative_amount_rejected():
    fields = {**VALID_CART_FIELDS, "total_paise": -1}
    with pytest.raises(ValidationError):
        UnsignedCartMandate(**fields)


def test_empty_items_rejected():
    fields = {**VALID_CART_FIELDS, "items": []}
    with pytest.raises(ValidationError):
        UnsignedCartMandate(**fields)


def test_empty_merchant_allowlist_rejected():
    fields = {**VALID_INTENT_FIELDS, "merchant_allowlist": []}
    with pytest.raises(ValidationError):
        UnsignedIntentMandate(**fields)


def test_naive_expires_at_rejected():
    fields = {**VALID_INTENT_FIELDS, "expires_at": datetime(2026, 9, 5, 23, 59, 59)}
    with pytest.raises(ValidationError):
        UnsignedIntentMandate(**fields)


def test_tz_aware_expires_at_normalizes_to_utc():
    fields = {**VALID_INTENT_FIELDS, "expires_at": datetime(2026, 9, 6, 5, 29, 59, tzinfo=timezone.utc)}
    mandate = UnsignedIntentMandate(**fields)
    assert mandate.expires_at.tzinfo == timezone.utc
