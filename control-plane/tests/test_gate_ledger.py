"""Stage 3c: the gate's ledger instrumentation. Separate from test_gate.py's
pure gate-logic tests -- these specifically check WHAT gets written to
ledger_rows and WHEN, including the one regression that motivated storing
ledger writes on their own session in the first place.
"""

import asyncio
import uuid

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from executor import gate as gate_module
from executor.gate import DenyResult, StepUpResult, run_gate
from ledger.models import LedgerRow
from ledger.writer import append_event_best_effort
from mandates.keys import Keyring

from .conftest import MERCHANT_ID, sign_cart, sign_intent, unsigned_cart, unsigned_intent


def _fresh_user_and_keyring(user_signing_key, merchant_signing_key):
    user_id = "user_" + uuid.uuid4().hex
    keyring = Keyring(
        user_keys={user_id: user_signing_key.verify_key},
        merchant_keys={MERCHANT_ID: merchant_signing_key.verify_key},
    )
    return user_id, keyring


def _make_pair(user_id, user_signing_key, merchant_signing_key, intent_overrides=None, cart_overrides=None):
    intent_fields = {"user_id": user_id}
    intent_fields.update(intent_overrides or {})
    intent = sign_intent(unsigned_intent(**intent_fields), user_signing_key)
    cart = sign_cart(unsigned_cart(intent.intent_id, **(cart_overrides or {})), merchant_signing_key)
    return intent, cart


async def _fake_run_demo_checkout(db, cart_id, amount_paise, description, transaction_id):
    from executor.checkout import idempotency_key_for_cart
    from models import DemoCheckout

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


def _event_types(clean_ledger, cart_id: str) -> list[str]:
    rows = (
        clean_ledger.execute(select(LedgerRow).where(LedgerRow.cart_id == cart_id).order_by(LedgerRow.seq.asc()))
        .scalars()
        .all()
    )
    return [r.event_type for r in rows]


def test_deny_rows_survive_the_gates_own_rollback(clean_ledger, user_signing_key, merchant_signing_key):
    """THE regression this whole ledger design started from: gate.py's DENY
    branch calls db.rollback() on the request session. Proved empirically
    (before any ledger code existed) that a row written on that session
    would be destroyed by it. append_event uses its own session specifically
    so this cannot happen -- this test is what actually proves that holds.
    """
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        intent_overrides={"max_amount_paise": 300000},
        cart_overrides={"total_paise": 200001, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 200001}]},
    )

    result = asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    assert isinstance(result, DenyResult)
    types = _event_types(clean_ledger, cart.cart_id)
    assert types == ["REQUEST_RECEIVED", "MANDATE_VERIFIED", "NONCE_CONSUMED", "POLICY_VERDICT"]

    deny_row = clean_ledger.execute(
        select(LedgerRow).where(LedgerRow.cart_id == cart.cart_id, LedgerRow.event_type == "POLICY_VERDICT")
    ).scalar_one()
    assert deny_row.decision == "DENY"
    assert deny_row.rule_fired == "per_transaction_cap"


def test_allow_writes_the_expected_event_sequence(clean_ledger, user_signing_key, merchant_signing_key):
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    types = _event_types(clean_ledger, cart.cart_id)
    assert types == [
        "REQUEST_RECEIVED",
        "MANDATE_VERIFIED",
        "NONCE_CONSUMED",
        "POLICY_VERDICT",
        "SPEND_RESERVED",
        "EXECUTION_COMMITTED",
    ]


def test_step_up_writes_queued_event_and_no_execution_rows(clean_ledger, user_signing_key, merchant_signing_key):
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 150000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 150000}]},
    )

    result = asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    assert isinstance(result, StepUpResult)
    types = _event_types(clean_ledger, cart.cart_id)
    assert types == ["REQUEST_RECEIVED", "MANDATE_VERIFIED", "NONCE_CONSUMED", "POLICY_VERDICT", "STEP_UP_QUEUED"]
    assert not any(t.startswith("EXECUTION") for t in types)
    assert not any(t == "SPEND_RESERVED" for t in types)


def test_replayed_nonce_logs_nonce_replay_rejected(clean_ledger, user_signing_key, merchant_signing_key):
    from mandates.errors import MandateError

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
    intent2, cart2 = _make_pair(user_id, user_signing_key, merchant_signing_key, cart_overrides={"nonce": shared_nonce})

    asyncio.run(run_gate(clean_ledger, intent1, cart1, keyring=keyring))
    with pytest.raises(MandateError):
        asyncio.run(run_gate(clean_ledger, intent2, cart2, keyring=keyring))

    types = _event_types(clean_ledger, cart2.cart_id)
    assert types == ["REQUEST_RECEIVED", "MANDATE_VERIFIED", "NONCE_REPLAY_REJECTED"]


def test_pre_money_ledger_failure_aborts_before_executor(
    clean_ledger, user_signing_key, merchant_signing_key, monkeypatch
):
    """Fail-closed: if append_event (the fail-closed helper `_log` wraps)
    cannot durably record a PRE-money event, the request must abort before
    the executor is ever reached -- an unaudited decision must not be made.
    """
    executor_called = False

    async def _should_not_be_called(*args, **kwargs):
        nonlocal executor_called
        executor_called = True

    monkeypatch.setattr(gate_module, "run_demo_checkout", _should_not_be_called)

    call_count = 0
    real_append_event = gate_module.append_event

    def _fail_on_policy_verdict(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("event_type") and kwargs["event_type"].value == "POLICY_VERDICT":
            raise RuntimeError("simulated ledger outage")
        return real_append_event(**kwargs)

    monkeypatch.setattr(gate_module, "append_event", _fail_on_policy_verdict)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    with pytest.raises(RuntimeError, match="simulated ledger outage"):
        asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    assert executor_called is False


def test_post_money_ledger_failure_does_not_fail_the_request(
    clean_ledger, user_signing_key, merchant_signing_key, monkeypatch
):
    """The inverse of the pre-money test: once the executor has actually
    been invoked, a ledger-write failure must NOT prevent the caller from
    getting their (already-real) result back.
    """
    from executor.gate import AllowResult

    def _always_fail(**kwargs):
        raise RuntimeError("simulated ledger outage")

    monkeypatch.setattr(gate_module, "append_event_best_effort", _always_fail)

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    result = asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))
    assert isinstance(result, AllowResult)  # did NOT raise despite the ledger failing


def test_no_ledger_row_contains_short_url_or_a_token(clean_ledger, user_signing_key, merchant_signing_key):
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        cart_overrides={"total_paise": 50000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 50000}]},
    )

    asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    rows = (
        clean_ledger.execute(select(LedgerRow).where(LedgerRow.cart_id == cart.cart_id)).scalars().all()
    )
    assert rows
    for row in rows:
        assert "rzp.io" not in row.payload_canonical  # the fake short_url used in mock_executor
        assert "short_url" not in row.payload_canonical
