"""Phase 4: the human-approval flow. These specifically check the property
CLAUDE.md's Prime Directive depends on -- an approved STEP_UP executes
through the EXACT SAME path (mandate re-verification from the stored
snapshot, fresh policy re-evaluation, _execute_allowed) as an automatic
ALLOW, never a shortcut.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from executor import gate as gate_module
from executor.gate import AllowResult, DenyResult, StepUpNotFoundOrResolvedResult, run_gate, run_step_up_denial
from executor.step_up import StepUpRequest, get_by_cart_id
from ledger.models import LedgerRow
from mandates.errors import MandateError
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


def _queue_a_step_up(clean_ledger, user_signing_key, merchant_signing_key, **cart_overrides):
    """Drives a real STEP_UP through run_gate so we get a genuine, properly
    signed, properly queued StepUpRequest to approve/deny against.
    """
    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    fields = {"total_paise": 150000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 150000}]}
    fields.update(cart_overrides)
    intent, cart = _make_pair(user_id, user_signing_key, merchant_signing_key, cart_overrides=fields)
    asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))
    return user_id, keyring, intent, cart


def test_approve_executes_through_execute_allowed_and_updates_status(
    clean_ledger, user_signing_key, merchant_signing_key
):
    from executor.gate import run_step_up_approval

    user_id, keyring, intent, cart = _queue_a_step_up(clean_ledger, user_signing_key, merchant_signing_key)

    result = asyncio.run(run_step_up_approval(clean_ledger, cart.cart_id, keyring=keyring, actor="reviewer_1"))

    assert isinstance(result, AllowResult)
    assert result.checkout["status"] == "COMMITTED"

    row = get_by_cart_id(clean_ledger, cart.cart_id)
    assert row.status == "APPROVED"

    event_types = (
        clean_ledger.execute(
            select(LedgerRow.event_type).where(LedgerRow.cart_id == cart.cart_id).order_by(LedgerRow.seq.asc())
        )
        .scalars()
        .all()
    )
    assert "STEP_UP_QUEUED" in event_types
    assert "STEP_UP_APPROVED" in event_types
    assert "SPEND_RESERVED" in event_types
    assert "EXECUTION_COMMITTED" in event_types
    assert "STEP_UP_REJECTED" not in event_types


def test_deny_writes_rejected_status_and_no_execution(clean_ledger, user_signing_key, merchant_signing_key):
    user_id, keyring, intent, cart = _queue_a_step_up(clean_ledger, user_signing_key, merchant_signing_key)

    result = asyncio.run(run_step_up_denial(clean_ledger, cart.cart_id, actor="reviewer_2"))

    assert isinstance(result, StepUpRequest)
    assert result.status == "REJECTED"

    event_types = (
        clean_ledger.execute(select(LedgerRow.event_type).where(LedgerRow.cart_id == cart.cart_id))
        .scalars()
        .all()
    )
    assert "STEP_UP_REJECTED" in event_types
    assert not any(t.startswith("EXECUTION") for t in event_types)
    assert not any(t == "SPEND_RESERVED" for t in event_types)


def test_approve_vetoed_by_expired_mandate(clean_ledger, user_signing_key, merchant_signing_key):
    from executor.gate import run_step_up_approval

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    # Expires almost immediately -- by the time we call approve() below, it
    # will have expired while "queued" (simulating a slow human reviewer).
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        intent_overrides={"expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(seconds=1)},
        cart_overrides={"total_paise": 150000, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 150000}]},
    )
    asyncio.run(run_gate(clean_ledger, intent, cart, keyring=keyring))

    import time

    time.sleep(1.2)

    with pytest.raises(MandateError):
        asyncio.run(run_step_up_approval(clean_ledger, cart.cart_id, keyring=keyring, actor="reviewer_3"))

    row = get_by_cart_id(clean_ledger, cart.cart_id)
    assert row.status == "REJECTED"

    event_types = (
        clean_ledger.execute(select(LedgerRow.event_type).where(LedgerRow.cart_id == cart.cart_id))
        .scalars()
        .all()
    )
    assert "MANDATE_REJECTED" in event_types
    assert "STEP_UP_REJECTED" in event_types


def test_approve_vetoed_by_policy_reevaluation(clean_ledger, user_signing_key, merchant_signing_key):
    """The human approves, but a fresh policy re-evaluation now DENIES. The
    veto must win over the human's approval -- run_step_up_approval must
    re-run policy on the ACTUAL stored/verified mandate rather than trusting
    the stale rule_fired/reason recorded at queuing time.

    Seeds the StepUpRequest directly (rather than driving it through
    run_gate) with a cart that is over the platform's per_transaction_cap --
    a rule that fires and DENIES on re-evaluation regardless of what verdict
    was stored when it was queued.
    """
    from datetime import datetime, timezone

    from executor.gate import run_step_up_approval
    from executor.step_up import create_step_up_request
    from policy.verdict import Decision, Verdict

    user_id, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    # intent's own cap must exceed the platform cap so mandate verification
    # (check_within_cap) passes; it's the POLICY layer's per_transaction_cap
    # that must catch this on re-evaluation.
    intent, cart = _make_pair(
        user_id,
        user_signing_key,
        merchant_signing_key,
        intent_overrides={"max_amount_paise": 300000},
        cart_overrides={"total_paise": 200001, "items": [{"sku": "X", "qty": 1, "unit_price_paise": 200001}]},
    )
    stale_verdict = Verdict(
        decision=Decision.STEP_UP,
        rule_fired="step_up_amount_threshold",
        reason="stale reason from original queuing",
        evaluated_at=datetime.now(timezone.utc),
        all_violations=(),
        rules_version=1,
        rules_sha256="deadbeef",
    )
    create_step_up_request(clean_ledger, intent=intent, cart=cart, verdict=stale_verdict)

    result = asyncio.run(run_step_up_approval(clean_ledger, cart.cart_id, keyring=keyring, actor="reviewer_4"))

    assert isinstance(result, DenyResult)
    assert result.verdict.rule_fired == "per_transaction_cap"  # NOT the stale rule_fired

    row = get_by_cart_id(clean_ledger, cart.cart_id)
    assert row.status == "REJECTED"


def test_double_approve_second_call_is_rejected(clean_ledger, user_signing_key, merchant_signing_key):
    from executor.gate import run_step_up_approval

    user_id, keyring, intent, cart = _queue_a_step_up(clean_ledger, user_signing_key, merchant_signing_key)

    result1 = asyncio.run(run_step_up_approval(clean_ledger, cart.cart_id, keyring=keyring))
    result2 = asyncio.run(run_step_up_approval(clean_ledger, cart.cart_id, keyring=keyring))

    assert isinstance(result1, AllowResult)
    assert isinstance(result2, StepUpNotFoundOrResolvedResult)


def test_approve_unknown_cart_id_returns_not_found(clean_ledger, user_signing_key, merchant_signing_key):
    from executor.gate import run_step_up_approval

    _, keyring = _fresh_user_and_keyring(user_signing_key, merchant_signing_key)
    result = asyncio.run(run_step_up_approval(clean_ledger, "cart_never_existed", keyring=keyring))
    assert isinstance(result, StepUpNotFoundOrResolvedResult)


def test_deny_unknown_cart_id_returns_not_found(clean_ledger):
    result = asyncio.run(run_step_up_denial(clean_ledger, "cart_never_existed"))
    assert isinstance(result, StepUpNotFoundOrResolvedResult)
