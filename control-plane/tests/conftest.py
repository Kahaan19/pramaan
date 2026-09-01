import uuid
from datetime import datetime, timedelta, timezone

import pytest
from nacl.signing import SigningKey
from sqlalchemy import text

import mandates.nonce  # noqa: F401 - registers mandate_nonces on Base.metadata
from db import Base, SessionLocal, engine
from mandates.keys import Keyring, sign_mandate
from mandates.schemas import CartMandate, IntentMandate, UnsignedCartMandate, UnsignedIntentMandate
from policy.context import PolicyContext
from policy.rules_schema import RulesConfig, load_rules_config

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


@pytest.fixture
def clean_ledger(db_session):
    """ledger_rows is a single GLOBAL sequential chain, unlike every other
    table in this project -- per-test-unique ids (the strategy used
    elsewhere, e.g. test_gate.py) don't help a test that needs to assert a
    specific seq, a genesis row, or a clean witness-reconciliation result.
    Reset to empty via the DB-side escape hatch (disables the append-only
    triggers just long enough to truncate, then re-enables them) rather than
    ever letting application code do it. Also truncates the tables
    verify_chain()'s witness check reads (spend_reservations,
    step_up_requests, demo_checkouts) -- safe because no test anywhere in
    this suite depends on another test's rows surviving across test
    boundaries; every other test already scopes its own assertions to
    freshly-generated random ids.
    """
    import ledger.models  # noqa: F401 - registers ledger_rows + triggers on Base.metadata

    Base.metadata.create_all(bind=engine)
    db_session.execute(text("SELECT ledger_reset_for_tests()"))
    db_session.execute(text("TRUNCATE spend_reservations, step_up_requests, demo_checkouts, mandate_nonces"))
    db_session.commit()
    yield db_session


@pytest.fixture(scope="session")
def rules_config() -> tuple[RulesConfig, str]:
    """The real policies/rules.yaml, loaded once. Using the real file (rather
    than a synthetic fixture policy) means these tests double as a check
    that the shipped policy file actually loads and behaves as documented.
    """
    return load_rules_config()


def make_policy_context(rules_config: tuple[RulesConfig, str], **overrides) -> PolicyContext:
    """A within-limits, ALLOW-shaped context by default. Override any field
    to push a specific rule to fire.
    """
    config, digest = rules_config
    now = overrides.get("now", datetime.now(timezone.utc))
    fields = dict(
        now=now,
        user_id=USER_ID,
        merchant_id=MERCHANT_ID,
        category="retail",
        amount_paise=50000,
        intent_expires_at=now + timedelta(days=1),
        human_present=True,
        recent_spend=(),
        pending_step_up_count=0,
        rules=config,
        rules_sha256=digest,
    )
    fields.update(overrides)
    return PolicyContext(**fields)
