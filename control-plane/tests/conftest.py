import uuid
from datetime import datetime, timedelta, timezone

import pytest
from nacl.signing import SigningKey

import mandates.nonce  # noqa: F401 - registers mandate_nonces on Base.metadata
from db import Base, SessionLocal, engine
from mandates.keys import Keyring, sign_mandate
from mandates.schemas import CartMandate, IntentMandate, UnsignedCartMandate, UnsignedIntentMandate

USER_ID = "user_kahaan"
MERCHANT_ID = "merchant_demo_01"

DEFAULT_ITEMS = [{"sku": "SKU-TEA-250", "qty": 1, "unit_price_paise": 129900}]
DEFAULT_TOTAL_PAISE = 129900
DEFAULT_MAX_AMOUNT_PAISE = 200000


@pytest.fixture(scope="session")
def user_signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture(scope="session")
def merchant_signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture(scope="session")
def keyring(user_signing_key, merchant_signing_key) -> Keyring:
    return Keyring(
        user_keys={USER_ID: user_signing_key.verify_key},
        merchant_keys={MERCHANT_ID: merchant_signing_key.verify_key},
    )


def unsigned_intent(**overrides) -> UnsignedIntentMandate:
    fields = dict(
        intent_id="intent_" + uuid.uuid4().hex[:12],
        user_id=USER_ID,
        max_amount_paise=DEFAULT_MAX_AMOUNT_PAISE,
        merchant_allowlist=[MERCHANT_ID],
        category="retail",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        human_present=True,
        nonce=uuid.uuid4().hex,
    )
    fields.update(overrides)
    return UnsignedIntentMandate(**fields)


def unsigned_cart(intent_id: str, **overrides) -> UnsignedCartMandate:
    fields = dict(
        cart_id="cart_" + uuid.uuid4().hex[:12],
        intent_id=intent_id,
        merchant_id=MERCHANT_ID,
        items=list(DEFAULT_ITEMS),
        total_paise=DEFAULT_TOTAL_PAISE,
        nonce=uuid.uuid4().hex,
    )
    fields.update(overrides)
    return UnsignedCartMandate(**fields)


def sign_intent(unsigned: UnsignedIntentMandate, signing_key: SigningKey) -> IntentMandate:
    signature = sign_mandate(unsigned, signing_key)
    return IntentMandate(**unsigned.model_dump(mode="json"), signature=signature)


def sign_cart(unsigned: UnsignedCartMandate, signing_key: SigningKey) -> CartMandate:
    signature = sign_mandate(unsigned, signing_key)
    return CartMandate(**unsigned.model_dump(mode="json"), signature=signature)


@pytest.fixture
def signed_pair(user_signing_key, merchant_signing_key):
    """Factory for a valid, freshly-signed (intent, cart) pair. Each call uses
    fresh random nonces/ids so tests never collide with each other, even
    against the shared mandate_nonces table.
    """

    def _make(intent_overrides=None, cart_overrides=None):
        intent = sign_intent(unsigned_intent(**(intent_overrides or {})), user_signing_key)
        cart = sign_cart(unsigned_cart(intent.intent_id, **(cart_overrides or {})), merchant_signing_key)
        return intent, cart

    return _make


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
