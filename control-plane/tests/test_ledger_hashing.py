from datetime import datetime, timedelta, timezone

import pytest

from ledger.hashing import GENESIS_PREV_HASH, chain_hash, render_ts
from ledger.payload import LedgerPayload
from mandates.canonical import canonical_json


def _payload(**overrides) -> LedgerPayload:
    fields = dict(
        event_type="REQUEST_RECEIVED",
        ts="2026-09-01T12:00:00Z",
        transaction_id="tx-1",
        explanation="a request was received",
    )
    fields.update(overrides)
    return LedgerPayload(**fields)


def test_canonical_bytes_stable_across_field_order():
    a = LedgerPayload.model_validate(
        {
            "event_type": "REQUEST_RECEIVED",
            "ts": "2026-09-01T12:00:00Z",
            "transaction_id": "tx-1",
            "explanation": "x",
        }
    )
    b = LedgerPayload.model_validate(
        {
            "transaction_id": "tx-1",
            "explanation": "x",
            "ts": "2026-09-01T12:00:00Z",
            "event_type": "REQUEST_RECEIVED",
        }
    )
    assert canonical_json(a.model_dump(mode="json")) == canonical_json(b.model_dump(mode="json"))


def test_non_ascii_sku_in_explanation_round_trips():
    p = _payload(explanation="1x SKU-चाय (₹129.00)")
    encoded = canonical_json(p.model_dump(mode="json"))
    assert "चाय".encode("utf-8") in encoded
    assert b"\\u" not in encoded  # ensure_ascii=False is pinned


def test_omitted_optional_field_equals_explicit_none():
    with_default = _payload()
    with_explicit_none = _payload(cart_id=None)
    assert canonical_json(with_default.model_dump(mode="json")) == canonical_json(
        with_explicit_none.model_dump(mode="json")
    )


def test_integer_paise_rejects_float():
    with pytest.raises(Exception):
        _payload(amount_paise=129900.0)


def test_bool_field_serializes_as_true_false_not_int():
    p = _payload(human_present=True)
    dumped = p.model_dump(mode="json")
    assert dumped["human_present"] is True
    encoded = canonical_json(dumped)
    assert b'"human_present":true' in encoded


def test_extra_field_rejected():
    with pytest.raises(Exception):
        LedgerPayload.model_validate(
            {
                "event_type": "REQUEST_RECEIVED",
                "ts": "2026-09-01T12:00:00Z",
                "transaction_id": "tx-1",
                "explanation": "x",
                "not_a_real_field": True,
            }
        )


def test_render_ts_normalizes_non_utc_offset_to_utc():
    utc_dt = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    ist_dt = utc_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert render_ts(utc_dt) == render_ts(ist_dt) == "2026-09-01T12:00:00Z"


def test_render_ts_rejects_naive_datetime():
    with pytest.raises(ValueError):
        render_ts(datetime(2026, 9, 1, 12, 0, 0))


def test_chain_hash_is_deterministic():
    payload_bytes = canonical_json(_payload().model_dump(mode="json"))
    assert chain_hash(GENESIS_PREV_HASH, payload_bytes) == chain_hash(GENESIS_PREV_HASH, payload_bytes)


def test_chain_hash_changes_with_prev_hash():
    payload_bytes = canonical_json(_payload().model_dump(mode="json"))
    h1 = chain_hash(GENESIS_PREV_HASH, payload_bytes)
    h2 = chain_hash("1" * 64, payload_bytes)
    assert h1 != h2


def test_chain_hash_changes_with_payload():
    h1 = chain_hash(GENESIS_PREV_HASH, canonical_json(_payload(explanation="a").model_dump(mode="json")))
    h2 = chain_hash(GENESIS_PREV_HASH, canonical_json(_payload(explanation="b").model_dump(mode="json")))
    assert h1 != h2
