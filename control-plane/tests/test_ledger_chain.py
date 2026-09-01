import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError

from db import SessionLocal
from executor.spend import reserve_spend
from ledger.events import LedgerEvent
from ledger.models import LedgerRow
from ledger.verify import verify_chain
from ledger.writer import append_event


def _append(cart_id=None, **overrides):
    """append_event manages its own session internally (see ledger/writer.py)
    -- this helper never takes a `db` because there's nothing to pass one to.
    """
    fields = dict(
        event_type=LedgerEvent.REQUEST_RECEIVED,
        now=datetime.now(timezone.utc),
        transaction_id=str(uuid.uuid4()),
        explanation="test row",
        cart_id=cart_id,
    )
    fields.update(overrides)
    return append_event(**fields)


def _disable_triggers(db):
    db.execute(text("ALTER TABLE ledger_rows DISABLE TRIGGER ledger_rows_no_update_delete"))


def _enable_triggers(db):
    db.execute(text("ALTER TABLE ledger_rows ENABLE TRIGGER ledger_rows_no_update_delete"))


def test_clean_chain_verifies(clean_ledger):
    _append()
    _append()
    _append()
    result = verify_chain(clean_ledger)
    assert result.ok is True
    assert result.row_count == 3


def test_genesis_row_verifies(clean_ledger):
    row = _append()
    assert row.seq == 0
    assert row.prev_hash == "0" * 64
    result = verify_chain(clean_ledger)
    assert result.ok is True


def test_mutated_payload_detected_as_hash_mismatch(clean_ledger):
    _append()
    row = _append()
    _append()

    _disable_triggers(clean_ledger)
    clean_ledger.execute(
        text("UPDATE ledger_rows SET payload_canonical = payload_canonical || ' ' WHERE id = :id"),
        {"id": row.id},
    )
    _enable_triggers(clean_ledger)
    clean_ledger.commit()

    result = verify_chain(clean_ledger)
    assert result.ok is False
    assert any(f.kind == "HASH_MISMATCH" and f.seq == row.seq for f in result.findings)


def test_deleted_middle_row_detected_as_seq_gap(clean_ledger):
    _append()
    row = _append()
    _append()

    _disable_triggers(clean_ledger)
    clean_ledger.execute(text("DELETE FROM ledger_rows WHERE id = :id"), {"id": row.id})
    _enable_triggers(clean_ledger)
    clean_ledger.commit()

    result = verify_chain(clean_ledger)
    assert result.ok is False
    assert any(f.kind == "SEQ_GAP" for f in result.findings)


def test_swapped_seq_detected_as_broken_link(clean_ledger):
    row_a = _append()
    row_b = _append()

    _disable_triggers(clean_ledger)
    clean_ledger.execute(text("UPDATE ledger_rows SET seq = -1 WHERE id = :id"), {"id": row_a.id})
    clean_ledger.execute(text("UPDATE ledger_rows SET seq = :s WHERE id = :id"), {"s": row_a.seq, "id": row_b.id})
    clean_ledger.execute(text("UPDATE ledger_rows SET seq = :s WHERE id = :id"), {"s": row_b.seq, "id": row_a.id})
    _enable_triggers(clean_ledger)
    clean_ledger.commit()

    result = verify_chain(clean_ledger)
    assert result.ok is False
    assert any(f.kind == "BROKEN_LINK" for f in result.findings)


def test_edited_typed_column_detected_as_column_payload_mismatch(clean_ledger):
    row = _append(cart_id="cart_original")

    _disable_triggers(clean_ledger)
    clean_ledger.execute(text("UPDATE ledger_rows SET cart_id = 'cart_forged' WHERE id = :id"), {"id": row.id})
    _enable_triggers(clean_ledger)
    clean_ledger.commit()

    result = verify_chain(clean_ledger)
    assert result.ok is False
    assert any(f.kind == "COLUMN_PAYLOAD_MISMATCH" and f.seq == row.seq for f in result.findings)


def test_unique_prev_hash_rejects_a_forced_fork(clean_ledger):
    row_a = _append()
    row_b = _append()  # prev_hash = row_a.row_hash
    # Insert a THIRD row that ALSO claims row_a as its predecessor -- exactly
    # what two racing writers with a stale tail read would produce. Both row_b
    # and this forced insert claim the same prev_hash; UNIQUE(prev_hash) must
    # reject the second one outright rather than silently forking the chain.
    with pytest.raises(IntegrityError):
        clean_ledger.execute(
            text(
                "INSERT INTO ledger_rows "
                "(seq, ts, event_type, transaction_id, explanation, payload_canonical, prev_hash, row_hash) "
                "VALUES (:seq, :ts, 'FORK', :txid, 'forked', '{}', :prev_hash, :row_hash)"
            ),
            {
                "seq": row_b.seq + 1,
                "ts": "2026-09-01T12:00:00Z",
                "txid": str(uuid.uuid4()),
                "prev_hash": row_a.row_hash,  # collides with row_b's prev_hash
                "row_hash": "f" * 64,
            },
        )
        clean_ledger.commit()
    clean_ledger.rollback()


def test_update_rejected_by_trigger(clean_ledger):
    row = _append()
    with pytest.raises(InternalError):
        clean_ledger.execute(text("UPDATE ledger_rows SET explanation = 'x' WHERE id = :id"), {"id": row.id})
        clean_ledger.commit()
    clean_ledger.rollback()


def test_delete_rejected_by_trigger(clean_ledger):
    row = _append()
    with pytest.raises(InternalError):
        clean_ledger.execute(text("DELETE FROM ledger_rows WHERE id = :id"), {"id": row.id})
        clean_ledger.commit()
    clean_ledger.rollback()


def test_truncate_rejected_by_statement_trigger(clean_ledger):
    _append()
    with pytest.raises(InternalError):
        clean_ledger.execute(text("TRUNCATE ledger_rows"))
        clean_ledger.commit()
    clean_ledger.rollback()


def test_truncated_tail_with_surviving_reservation_flags_missing_audit(clean_ledger):
    """This is what a hash chain alone CANNOT detect: DELETE the tail rows
    entirely and the chain still verifies internally. The witness check
    (an operational record with zero corresponding ledger rows) is what
    catches it.
    """
    cart_id = "cart_" + uuid.uuid4().hex
    row = _append(cart_id=cart_id)
    reserve_spend(clean_ledger, cart_id=cart_id, user_id="user_x", intent_id="intent_x", amount_paise=1000)

    _disable_triggers(clean_ledger)
    clean_ledger.execute(text("DELETE FROM ledger_rows WHERE id = :id"), {"id": row.id})
    _enable_triggers(clean_ledger)
    clean_ledger.commit()

    result = verify_chain(clean_ledger)
    assert result.ok is False
    assert any(
        f.kind == "MISSING_AUDIT_FOR_KNOWN_TRANSACTION" and cart_id in f.detail for f in result.findings
    )


def test_concurrent_appends_produce_a_gapless_valid_chain(clean_ledger):
    n = 12
    errors: list[Exception] = []

    def _worker():
        try:
            _append()
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"append_event raised under concurrency: {errors}"

    result = verify_chain(clean_ledger)
    assert result.ok is True
    assert result.row_count == n
    seqs = sorted(r.seq for r in clean_ledger.execute(text("SELECT seq FROM ledger_rows")).all())
    assert seqs == list(range(n))  # gapless, no duplicates


def test_single_arg_and_two_arg_advisory_locks_are_separate_keyspaces(clean_ledger):
    """F1 regression, made deterministic rather than searching for a real
    colliding user_id (which would need an infeasible search over ~4 billion
    hashtext outputs to hit reliably). What actually matters is the claim
    Postgres documents: pg_advisory_xact_lock(bigint) and
    pg_advisory_xact_lock(int, int) are separate lock spaces. Simulate a
    user_id whose hashtext() happens to equal 2 (the ledger's own two-key
    lock uses (2, 0)) by holding pg_advisory_xact_lock(2) directly on a
    separate connection, then confirm append_event -- which locks (2, 0) --
    is completely unaffected and returns promptly rather than hanging.
    """
    holder = SessionLocal()
    holder.execute(text("SELECT pg_advisory_xact_lock(2)"))  # the single-arg bigint form
    try:
        row = _append()  # uses pg_advisory_xact_lock(2, 0) -- must not block
        assert row.seq is not None
    finally:
        holder.rollback()
        holder.close()
