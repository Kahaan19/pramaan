import pytest
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

from mandates.canonical import signing_bytes
from mandates.errors import MandateError, MandateErrorCode
from mandates.keys import Keyring, verify_signature
from mandates.schemas import IntentMandate

from .conftest import MERCHANT_ID, USER_ID, sign_cart, sign_intent, unsigned_cart, unsigned_intent


def test_valid_intent_signature_verifies(user_signing_key):
    intent = sign_intent(unsigned_intent(), user_signing_key)
    verify_signature(intent, intent.signature, user_signing_key.verify_key, MandateErrorCode.BAD_SIGNATURE_INTENT)


def test_valid_cart_signature_verifies(merchant_signing_key):
    cart = sign_cart(unsigned_cart("intent_1"), merchant_signing_key)
    verify_signature(cart, cart.signature, merchant_signing_key.verify_key, MandateErrorCode.BAD_SIGNATURE_CART)


def test_one_paise_tamper_fails_signature(user_signing_key):
    intent = sign_intent(unsigned_intent(), user_signing_key)
    tampered = intent.model_copy(update={"max_amount_paise": intent.max_amount_paise + 1})
    with pytest.raises(MandateError) as exc_info:
        verify_signature(
            tampered, tampered.signature, user_signing_key.verify_key, MandateErrorCode.BAD_SIGNATURE_INTENT
        )
    assert exc_info.value.code == MandateErrorCode.BAD_SIGNATURE_INTENT


def test_reordered_wire_keys_still_verify(user_signing_key):
    intent = sign_intent(unsigned_intent(), user_signing_key)
    wire = intent.model_dump(mode="json")
    # Rebuild from a dict constructed in a different key order -- Python dicts
    # preserve insertion order, so this is a real reordering, not a no-op.
    reordered = {k: wire[k] for k in reversed(list(wire.keys()))}
    rebuilt = IntentMandate(**reordered)
    verify_signature(
        rebuilt, rebuilt.signature, user_signing_key.verify_key, MandateErrorCode.BAD_SIGNATURE_INTENT
    )


def test_cross_type_domain_tag_prevents_signature_confusion(user_signing_key):
    """A signature computed over a cart's canonical bytes must not verify
    against the same field values canonicalized with the intent domain tag.
    Tests the tag mechanism directly, independent of the type system that
    also happens to prevent this at a higher level.
    """
    cart = unsigned_cart("intent_1")
    cart_bytes = signing_bytes(cart)
    signature = user_signing_key.sign(cart_bytes).signature

    intent_tagged_bytes = b"pramaan.mandate.intent.v1\n" + cart_bytes.split(b"\n", 1)[1]
    with pytest.raises(BadSignatureError):
        user_signing_key.verify_key.verify(intent_tagged_bytes, signature)


def test_malformed_base64_signature_raises_malformed(user_signing_key):
    intent = sign_intent(unsigned_intent(), user_signing_key)
    with pytest.raises(MandateError) as exc_info:
        verify_signature(
            intent, "not valid base64!!", user_signing_key.verify_key, MandateErrorCode.BAD_SIGNATURE_INTENT
        )
    assert exc_info.value.code == MandateErrorCode.MALFORMED


def test_unknown_user_signer_raises_unknown_signer(merchant_signing_key):
    keyring = Keyring(user_keys={}, merchant_keys={MERCHANT_ID: merchant_signing_key.verify_key})
    with pytest.raises(MandateError) as exc_info:
        keyring.user_key(USER_ID)
    assert exc_info.value.code == MandateErrorCode.UNKNOWN_SIGNER


def test_unknown_merchant_signer_raises_unknown_signer(user_signing_key):
    keyring = Keyring(user_keys={USER_ID: user_signing_key.verify_key}, merchant_keys={})
    with pytest.raises(MandateError) as exc_info:
        keyring.merchant_key(MERCHANT_ID)
    assert exc_info.value.code == MandateErrorCode.UNKNOWN_SIGNER


def test_wrong_signing_key_fails_verification():
    wrong_key = SigningKey.generate()
    intent = sign_intent(unsigned_intent(), wrong_key)
    right_key = SigningKey.generate()
    with pytest.raises(MandateError) as exc_info:
        verify_signature(intent, intent.signature, right_key.verify_key, MandateErrorCode.BAD_SIGNATURE_INTENT)
    assert exc_info.value.code == MandateErrorCode.BAD_SIGNATURE_INTENT
