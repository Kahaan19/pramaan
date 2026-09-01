"""Stage 3c: the two app-level exception handlers in main.py must each
produce exactly one ledger row, even though neither has a verified mandate,
a transaction that reached run_gate, or any money-moving context at all.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from ledger.models import LedgerRow


def test_malformed_request_logs_rejected_row(clean_ledger):
    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/demo/checkout", json={"intent": "not even an object", "cart": {}})

    assert response.status_code == 422
    rows = clean_ledger.execute(
        select(LedgerRow).where(LedgerRow.event_type == "REQUEST_REJECTED_MALFORMED")
    ).scalars().all()
    assert len(rows) == 1
    # Never the raw body -- only a digest and the pydantic error locations.
    assert "not even an object" not in rows[0].explanation
    assert "not even an object" not in rows[0].payload_canonical


def test_unhandled_exception_logs_errored_row_and_returns_500(clean_ledger, monkeypatch):
    from main import app
    import routers.demo as demo_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("something genuinely unexpected")

    monkeypatch.setattr(demo_module, "run_gate", _boom)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/demo/checkout",
        json={
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
        },
    )

    assert response.status_code == 500
    rows = clean_ledger.execute(
        select(LedgerRow).where(LedgerRow.event_type == "REQUEST_ERRORED")
    ).scalars().all()
    assert len(rows) == 1
