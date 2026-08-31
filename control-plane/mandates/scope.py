"""Business-rule checks that need both mandates together. Each function
returns None on success or raises MandateError. Pure -- no I/O, no clock
reads (expiry takes an injected `now`).

Defence in depth: check_merchant_allowed here checks the INTENT's own
allowlist -- what this specific user authorized. The Phase 2 policy engine
separately checks the GLOBAL allowlist in policies/rules.yaml -- what the
platform permits. Both must pass; neither substitutes for the other.
"""

from datetime import datetime

from mandates.errors import MandateError, MandateErrorCode
from mandates.schemas import CartMandate, IntentMandate


def check_binding(intent: IntentMandate, cart: CartMandate) -> None:
    if cart.intent_id != intent.intent_id:
        raise MandateError(
            MandateErrorCode.INTENT_MISMATCH,
            f"cart.intent_id={cart.intent_id!r} does not match intent.intent_id={intent.intent_id!r}",
        )


def check_not_expired(intent: IntentMandate, now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    # Expiry is inclusive: a mandate is valid at the exact instant it expires,
    # and invalid the instant after.
    if now > intent.expires_at:
        raise MandateError(
            MandateErrorCode.EXPIRED,
            f"intent {intent.intent_id!r} expired at {intent.expires_at.isoformat()} (now={now.isoformat()})",
        )


def check_merchant_allowed(intent: IntentMandate, cart: CartMandate) -> None:
    if cart.merchant_id not in intent.merchant_allowlist:
        raise MandateError(
            MandateErrorCode.MERCHANT_NOT_ALLOWLISTED,
            f"merchant_id={cart.merchant_id!r} is not in intent {intent.intent_id!r}'s allowlist "
            f"{intent.merchant_allowlist}",
        )


def check_cart_total_consistent(cart: CartMandate) -> None:
    computed = sum(item.qty * item.unit_price_paise for item in cart.items)
    if computed != cart.total_paise:
        raise MandateError(
            MandateErrorCode.CART_TOTAL_MISMATCH,
            f"cart {cart.cart_id!r} items sum to {computed} paise but total_paise={cart.total_paise}",
        )


def check_within_cap(intent: IntentMandate, cart: CartMandate) -> None:
    if cart.total_paise > intent.max_amount_paise:
        raise MandateError(
            MandateErrorCode.CART_EXCEEDS_INTENT,
            f"cart total_paise={cart.total_paise} exceeds intent max_amount_paise={intent.max_amount_paise}",
        )
