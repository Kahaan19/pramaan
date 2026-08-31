from datetime import datetime, timedelta, timezone

import pytest

from mandates.errors import MandateError, MandateErrorCode
from mandates.verify import verify_mandate_chain


# Truncated to whole seconds because the schema layer normalizes expires_at
# the same way (see UnsignedIntentMandate._expires_at_utc) -- without this,
# the "expiry exactly at now" boundary test would be comparing a microsecond-
# precise `now` against a mandate's second-truncated expires_at and treat
# the mandate as already-expired.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def test_happy_chain_verifies(signed_pair, keyring):
    intent, cart = signed_pair()
    verified = verify_mandate_chain(intent, cart, keyring, NOW)
    assert verified.intent.intent_id == intent.intent_id
    assert verified.cart.cart_id == cart.cart_id


def test_cart_intent_mismatch_rejected(signed_pair, keyring):
    intent, cart = signed_pair()
    other_intent, _ = signed_pair()
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(other_intent, cart, keyring, NOW)
    assert exc_info.value.code == MandateErrorCode.INTENT_MISMATCH


def test_merchant_not_in_intent_allowlist_rejected(signed_pair, keyring):
    intent, cart = signed_pair(intent_overrides={"merchant_allowlist": ["merchant_other"]})
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(intent, cart, keyring, NOW)
    assert exc_info.value.code == MandateErrorCode.MERCHANT_NOT_ALLOWLISTED


def test_cart_items_sum_mismatch_rejected(signed_pair, keyring):
    intent, cart = signed_pair(
        cart_overrides={"items": [{"sku": "X", "qty": 1, "unit_price_paise": 100}], "total_paise": 999}
    )
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(intent, cart, keyring, NOW)
    assert exc_info.value.code == MandateErrorCode.CART_TOTAL_MISMATCH


def test_cart_total_exactly_at_cap_allows(signed_pair, keyring):
    intent, cart = signed_pair(
        intent_overrides={"max_amount_paise": 129900},
        cart_overrides={"items": [{"sku": "X", "qty": 1, "unit_price_paise": 129900}], "total_paise": 129900},
    )
    verify_mandate_chain(intent, cart, keyring, NOW)  # must not raise


def test_cart_total_one_paise_over_cap_rejected(signed_pair, keyring):
    intent, cart = signed_pair(
        intent_overrides={"max_amount_paise": 129899},
        cart_overrides={"items": [{"sku": "X", "qty": 1, "unit_price_paise": 129900}], "total_paise": 129900},
    )
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(intent, cart, keyring, NOW)
    assert exc_info.value.code == MandateErrorCode.CART_EXCEEDS_INTENT


def test_expiry_exactly_now_is_still_valid(signed_pair, keyring):
    expires_at = NOW
    intent, cart = signed_pair(intent_overrides={"expires_at": expires_at})
    verify_mandate_chain(intent, cart, keyring, expires_at)  # inclusive -- must not raise


def test_expiry_one_second_past_rejected(signed_pair, keyring):
    expires_at = NOW
    intent, cart = signed_pair(intent_overrides={"expires_at": expires_at})
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(intent, cart, keyring, expires_at + timedelta(seconds=1))
    assert exc_info.value.code == MandateErrorCode.EXPIRED


def test_bad_signature_rejected_before_scope_checks(signed_pair, keyring):
    intent, cart = signed_pair()
    tampered_cart = cart.model_copy(update={"total_paise": cart.total_paise + 1})
    with pytest.raises(MandateError) as exc_info:
        verify_mandate_chain(intent, tampered_cart, keyring, NOW)
    assert exc_info.value.code == MandateErrorCode.BAD_SIGNATURE_CART
