"""Top-level mandate chain verification: signatures + scope, nothing else.

Deliberately does NOT consume the cart nonce or run policy -- see
mandates/nonce.py and (Phase 2) policy/. The intended request-flow order is:

    idempotency check -> verify_mandate_chain() -> consume_cart_nonce() -> policy -> executor

Checking idempotency before touching the nonce matters: a client legitimately
retrying with the same idempotency_key AND the same cart mandate must get the
cached result, not a spurious REPLAYED_NONCE.
"""

from dataclasses import dataclass
from datetime import datetime

from mandates.errors import MandateErrorCode
from mandates.keys import Keyring, verify_signature
from mandates.schemas import CartMandate, IntentMandate
from mandates.scope import (
    check_binding,
    check_cart_total_consistent,
    check_merchant_allowed,
    check_not_expired,
    check_within_cap,
)


@dataclass(frozen=True)
class VerifiedMandate:
    intent: IntentMandate
    cart: CartMandate
    verified_at: datetime


def verify_mandate_chain(
    intent: IntentMandate,
    cart: CartMandate,
    keyring: Keyring,
    now: datetime,
) -> VerifiedMandate:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    user_key = keyring.user_key(intent.user_id)
    verify_signature(intent, intent.signature, user_key, MandateErrorCode.BAD_SIGNATURE_INTENT)

    merchant_key = keyring.merchant_key(cart.merchant_id)
    verify_signature(cart, cart.signature, merchant_key, MandateErrorCode.BAD_SIGNATURE_CART)

    check_binding(intent, cart)
    check_not_expired(intent, now)
    check_merchant_allowed(intent, cart)
    check_cart_total_consistent(cart)
    check_within_cap(intent, cart)

    return VerifiedMandate(intent=intent, cart=cart, verified_at=now)
