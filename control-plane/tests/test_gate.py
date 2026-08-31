import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
from sqlalchemy import select

from executor import gate as gate_module
from executor.gate import AllowResult, DenyResult, StepUpResult, run_gate
from executor.spend import SpendReservation
from executor.step_up import StepUpRequest
from mandates.errors import MandateError, MandateErrorCode
from mandates.keys import Keyring
from models import DemoCheckout

from .conftest import MERCHANT_ID, sign_cart, sign_intent, unsigned_cart, unsigned_intent

# Gate tests write REAL rows to the shared dev Postgres DB (spend_reservations,
# step_up_requests, mandate_nonces) that a velocity/max-pending rule can read
# back. Reusing conftest's fixed USER_ID across tests would make them
# order-dependent -- an earlier test's ALLOW reservations would poison a
# later test's velocity count. Every test here mints its own random user_id
# (and a matching per-test Keyring over the same session-scoped signing
# keys), so no test's data can ever be visible to another's.


def _fresh_user_and_keyring(user_signing_key: SigningKey, merchant_signing_key: SigningKey) -> tuple[str, Keyring]:
    user_id = "user_" + uuid.uuid4().hex
    keyring = Keyring(
        user_keys={user_id: user_signing_key.verify_key},
        merchant_keys={MERCHANT_ID: merchant_signing_key.verify_key},
    )
    return user_id, keyring


def _make_pair(user_id: str, user_signing_key, merchant_signing_key, intent_overrides=None, cart_overrides=None):
    intent_fields = {"user_id": user_id}
    intent_fields.update(intent_overrides or {})
    intent = sign_intent(unsigned_intent(**intent_fields), user_signing_key)
    cart = sign_cart(unsigned_cart(intent.intent_id, **(cart_overrides or {})), merchant_signing_key)
    return intent, cart


async def _fake_run_demo_checkout(db, cart_id, amount_paise, description):
    """Stands in for executor.checkout.run_demo_checkout -- no live Razorpay
    calls in unit tests. Mirrors the real function's DemoCheckout bookkeeping
    closely enough for the gate's idempotency-cache-hit path to still work.
    """
    from executor.checkout import idempotency_key_for_cart

    row = DemoCheckout(
        idempotency_key=idempotency_key_for_cart(cart_id),
        cart_id=cart_id,
        status="COMMITTED",
        amount_paise=amount_paise,
        order_id="order_fake",
        payment_link_id="plink_fake",
        short_url="https://rzp.io/rzp/fake",
        payment_link_status="created",
    )
    db.add(row)
    db.commit()
    return {
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "amount_paise": row.amount_paise,
        "order_id": row.order_id,
        "payment_link_id": row.payment_link_id,
        "short_url": row.short_url,
        "payment_link_status": row.payment_link_status,
        "payment_id": None,
        "payment_status": None,
        "replayed": False,
    }


@pytest.fixture(autouse=True)
def mock_executor(monkeypatch):
    monkeypatch.setattr(gate_module, "run_demo_checkout", _fake_run_demo_checkout)


def test_allow_executes_and_writes_committed_reservation(user_signing_key, merchant_signing_key, db_session):
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    result = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))

    assert isinstance(result, AllowResult)
    reservation = db_session.execute(
        select(SpendReservation).where(SpendReservation.cart_id == cart.cart_id)
    ).scalar_one()
    assert reservation.status == "COMMITTED"
    assert reservation.amount_paise == 50000


def test_deny_calls_no_executor_and_writes_no_reservation(
    user_signing_key, merchant_signing_key, db_session, monkeypatch
):
    called = False

    async def _should_not_be_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(gate_module, "run_demo_checkout", _should_not_be_called)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    # intent's own cap (mandate layer) must be HIGHER than the platform cap
    # (policy layer, 200000 paise) so the mandate layer lets this through and
    # it's genuinely the POLICY rule that denies it, not check_within_cap.
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        intent_overrides={"max_amount_paise": 300000},
        cart_overrides={"total_paise": 200001, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 200001}]},
    )

    result = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))

    assert isinstance(result, DenyResult)
    assert result.verdict.rule_fired == "per_transaction_cap"
    assert not called
    reservation = db_session.execute(
        select(SpendReservation).where(SpendReservation.cart_id == cart.cart_id)
    ).scalar_one_or_none()
    assert reservation is None


def test_step_up_does_not_execute_but_burns_nonce_and_persists_request(
    user_signing_key, merchant_signing_key, db_session, monkeypatch
):
    called = False

    async def _should_not_be_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(gate_module, "run_demo_checkout", _should_not_be_called)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 150000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 150000}]},
    )

    result = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))

    assert isinstance(result, StepUpResult)
    assert result.verdict.decision.value == "STEP_UP"
    assert not called

    step_up_row = db_session.execute(
        select(StepUpRequest).where(StepUpRequest.cart_id == cart.cart_id)
    ).scalar_one()
    assert step_up_row.status == "PENDING"

    # The nonce was burned -- a second attempt reusing the SAME cart nonce
    # (even under a different cart_id) must be rejected as a replay.
    intent2, cart2 = _make_pair(
        user_id, user_signing_key, merchant_signing_key, cart_overrides={"nonce": cart.nonce}
    )
    with pytest.raises(MandateError) as exc_info:
        asyncio.run(run_gate(db_session, intent2, cart2, keyring=keyring))
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE


def test_replayed_nonce_across_different_carts_raises(user_signing_key, merchant_signing_key, db_session):
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    shared_nonce = "shared-" + uuid.uuid4().hex
    intent1, cart1 = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={
            "nonce": shared_nonce,
            "total_paise": 50000,
            "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}],
        },
    )
    intent2, cart2 = _make_pair(
        user_id, user_signing_key, merchant_signing_key, cart_overrides={"nonce": shared_nonce}
    )

    result1 = asyncio.run(run_gate(db_session, intent1, cart1, keyring=keyring))
    assert isinstance(result1, AllowResult)

    with pytest.raises(MandateError) as exc_info:
        asyncio.run(run_gate(db_session, intent2, cart2, keyring=keyring))
    assert exc_info.value.code == MandateErrorCode.REPLAYED_NONCE


def test_same_cart_twice_hits_idempotency_cache_executor_called_once(
    user_signing_key, merchant_signing_key, db_session, monkeypatch
):
    call_count = 0
    real_fake = _fake_run_demo_checkout

    async def _counting_fake(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await real_fake(*args, **kwargs)

    monkeypatch.setattr(gate_module, "run_demo_checkout", _counting_fake)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    result1 = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))
    result2 = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))

    assert isinstance(result1, AllowResult)
    assert isinstance(result2, AllowResult)
    assert result2.checkout["replayed"] is True
    assert call_count == 1

    reservations = (
        db_session.execute(select(SpendReservation).where(SpendReservation.cart_id == cart.cart_id))
        .scalars()
        .all()
    )
    assert len(reservations) == 1  # cart_id UNIQUE backstop: exactly one reservation ever


def test_max_pending_step_ups_enforced(user_signing_key, merchant_signing_key, db_session, monkeypatch):
    """Closes the approval-queue-flooding hole: a user with
    max_pending_step_ups (3) already-pending STEP_UP requests gets DENIED,
    not queued for a 4th.
    """

    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("executor must not be called")

    monkeypatch.setattr(gate_module, "run_demo_checkout", _should_not_be_called)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)

    def _step_up_cart():
        return _make_pair(
            user_id,
            user_signing_key,
            merchant_signing_key,
            cart_overrides={"total_paise": 150000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 150000}]},
        )

    for _ in range(3):
        intent, cart = _step_up_cart()
        result = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))
        assert isinstance(result, StepUpResult)

    intent, cart = _step_up_cart()
    result = asyncio.run(run_gate(db_session, intent, cart, keyring=keyring))
    assert isinstance(result, DenyResult)
    assert result.verdict.rule_fired == "max_pending_step_ups"


def test_request_body_rejects_client_supplied_amount_paise():
    """extra="forbid" on the request model: amount_paise must not be
    accepted back from the client under any name, even alongside a
    well-formed intent+cart. This is a pure request-validation check --
    Pydantic rejects it before the handler (and therefore get_keyring())
    ever runs, so it needs no real keys on disk.
    """
    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "intent": {
            "intent_id": "intent_x",
            "user_id": "user_x",
            "max_amount_paise": 200000,
            "merchant_allowlist": ["merchant_demo_01"],
            "category": "retail",
            "expires_at": "2099-01-01T00:00:00Z",
            "human_present": True,
            "nonce": "n1",
            "signature": "invalid-but-well-formed==",
        },
        "cart": {
            "cart_id": "cart_x",
            "intent_id": "intent_x",
            "merchant_id": "merchant_demo_01",
            "items": [{"sku": "X", "qty": 1, "unit_price_paise": 100}],
            "total_paise": 100,
            "nonce": "n2",
            "signature": "invalid-but-well-formed==",
        },
        "amount_paise": 1,  # must be rejected, not silently ignored
    }
    response = client.post("/demo/checkout", json=body)
    assert response.status_code == 422
