"""Stage 3a regression tests for executor/checkout.py.

These pin two fixes: (1) a replay of an existing DemoCheckout row must report
its ACTUAL status, never a blanket "success"; (2) an exception raised inside
the Razorpay call -- even one wrapped in an ExceptionGroup by the mcp SDK's
anyio-based transport -- must still mark the row FAILED and must still be
catchable by callers using a plain `except RazorpayToolError`.
"""

import uuid

import pytest
from sqlalchemy import select

from executor.checkout import idempotency_key_for_cart, run_demo_checkout
from executor.razorpay_mcp import RazorpayToolError
from models import DemoCheckout


def test_replay_of_failed_checkout_reports_failed_status(db_session):
    cart_id = "cart_" + uuid.uuid4().hex
    key = idempotency_key_for_cart(cart_id)
    db_session.add(DemoCheckout(idempotency_key=key, cart_id=cart_id, status="FAILED", amount_paise=5000))
    db_session.commit()

    result = _run(run_demo_checkout(db_session, cart_id=cart_id, amount_paise=5000, description="x", transaction_id="tx-" + cart_id))

    assert result["status"] == "FAILED"
    assert result["replayed"] is True


def test_replay_of_in_flight_checkout_reports_in_flight_status(db_session):
    cart_id = "cart_" + uuid.uuid4().hex
    key = idempotency_key_for_cart(cart_id)
    db_session.add(DemoCheckout(idempotency_key=key, cart_id=cart_id, status="IN_FLIGHT", amount_paise=5000))
    db_session.commit()

    result = _run(run_demo_checkout(db_session, cart_id=cart_id, amount_paise=5000, description="x", transaction_id="tx-" + cart_id))

    assert result["status"] == "IN_FLIGHT"
    assert result["replayed"] is True


def test_wrapped_exception_group_still_marks_row_failed_and_unwraps(db_session, monkeypatch):
    """Simulates the exact failure mode observed live in this session: the
    mcp SDK's anyio task groups wrap a plain exception (here a
    RazorpayToolError) in a BaseExceptionGroup. A plain `except
    RazorpayToolError` around the Razorpay call would miss it entirely.
    """
    from executor import checkout as checkout_module

    async def _raise_wrapped(*args, **kwargs):
        raise ExceptionGroup("simulated anyio task group failure", [RazorpayToolError("create_order", "boom")])

    monkeypatch.setattr(checkout_module, "razorpay_session", _raise_wrapped_context_manager(_raise_wrapped))

    cart_id = "cart_" + uuid.uuid4().hex
    with pytest.raises(RazorpayToolError):
        _run(run_demo_checkout(db_session, cart_id=cart_id, amount_paise=5000, description="x", transaction_id="tx-" + cart_id))

    row = db_session.execute(
        select(DemoCheckout).where(DemoCheckout.idempotency_key == idempotency_key_for_cart(cart_id))
    ).scalar_one()
    assert row.status == "FAILED"


def _raise_wrapped_context_manager(raiser):
    """Builds a fake async-context-manager factory (matching razorpay_session's
    shape) whose __aenter__ raises via `raiser`.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        await raiser()
        yield None  # unreachable

    return _cm


def _run(coro):
    import asyncio

    return asyncio.run(coro)
