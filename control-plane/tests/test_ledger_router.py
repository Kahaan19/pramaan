"""routers/ledger.py::recent() -- specifically the decision-badge
classification for a transaction whose only rows are a mandate/step-up
rejection (no policy DENY verdict was ever produced). Calls the route
function directly with an explicit `db`, bypassing FastAPI's dependency
injection -- a standard way to unit-test a route function without spinning
up a TestClient for something this targeted.
"""

import uuid
from datetime import datetime, timezone

from ledger.events import LedgerEvent
from ledger.writer import append_event
from routers.ledger import recent


def _append(**overrides):
    fields = dict(now=datetime.now(timezone.utc), transaction_id=str(uuid.uuid4()), explanation="test row")
    fields.update(overrides)
    return append_event(**fields)


def test_recent_classifies_mandate_rejection_as_deny_for_the_badge(clean_ledger):
    txid = str(uuid.uuid4())
    cart_id = "cart_" + uuid.uuid4().hex
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, cart_id=cart_id, explanation="received")
    _append(
        event_type=LedgerEvent.MANDATE_REJECTED,
        transaction_id=txid,
        cart_id=cart_id,
        mandate_error_code="expired",
        explanation="(expired) intent expired",
    )

    result = recent(limit=25, db=clean_ledger)
    tx = next(t for t in result["transactions"] if t["transaction_id"] == txid)

    assert tx["decision"] == "DENY"
    assert "BLOCKED" in tx["headline"]


def test_recent_leaves_decision_none_when_nothing_terminal_happened(clean_ledger):
    txid = str(uuid.uuid4())
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, explanation="received")

    result = recent(limit=25, db=clean_ledger)
    tx = next(t for t in result["transactions"] if t["transaction_id"] == txid)

    assert tx["decision"] is None
