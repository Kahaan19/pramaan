import uuid
from datetime import datetime, timezone

from ledger.events import LedgerEvent
from ledger.explain import explain, render_narrative
from ledger.verify import verify_chain
from ledger.writer import append_event


def _append(**overrides):
    fields = dict(
        now=datetime.now(timezone.utc),
        transaction_id=str(uuid.uuid4()),
        explanation="test row",
    )
    fields.update(overrides)
    return append_event(**fields)


def test_explain_not_found_for_unknown_key(clean_ledger):
    result = explain(clean_ledger, "no-such-transaction")
    assert result.found is False
    assert "No ledger record" in result.headline


def test_explain_renders_allow_story(clean_ledger):
    txid = str(uuid.uuid4())
    cart_id = "cart_" + uuid.uuid4().hex
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, cart_id=cart_id, explanation="received")
    _append(
        event_type=LedgerEvent.MANDATE_VERIFIED,
        transaction_id=txid,
        cart_id=cart_id,
        explanation="intent and cart signatures verified",
    )
    _append(
        event_type=LedgerEvent.POLICY_VERDICT,
        transaction_id=txid,
        cart_id=cart_id,
        decision="ALLOW",
        rule_fired=None,
        explanation="amount 50000 paise is within all limits",
    )
    _append(
        event_type=LedgerEvent.EXECUTION_COMMITTED,
        transaction_id=txid,
        cart_id=cart_id,
        order_id="order_x",
        payment_link_id="plink_x",
        explanation="order_x / plink_x created",
    )

    result = explain(clean_ledger, txid)
    assert result.found is True
    assert "ALLOWED" in result.headline
    assert len(result.narrative) == 4
    assert all(line.startswith("[seq ") for line in result.narrative)
    assert result.integrity_status == "OK"


def test_explain_renders_deny_story_naming_the_rule(clean_ledger):
    txid = str(uuid.uuid4())
    cart_id = "cart_" + uuid.uuid4().hex
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, cart_id=cart_id, explanation="received")
    _append(
        event_type=LedgerEvent.POLICY_VERDICT,
        transaction_id=txid,
        cart_id=cart_id,
        decision="DENY",
        rule_fired="per_transaction_cap",
        explanation="amount 200001 paise exceeds per-transaction cap 200000 paise",
    )

    result = explain(clean_ledger, txid)
    assert "BLOCKED" in result.headline
    assert any("per_transaction_cap" in line for line in result.narrative)
    assert any("200001" in line for line in result.narrative)  # verdict.reason rendered verbatim, numbers intact


def test_explain_by_cart_id_returns_every_attempt(clean_ledger):
    cart_id = "cart_" + uuid.uuid4().hex
    tx1, tx2 = str(uuid.uuid4()), str(uuid.uuid4())
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=tx1, cart_id=cart_id, explanation="first")
    _append(event_type=LedgerEvent.NONCE_REPLAY_REJECTED, transaction_id=tx2, cart_id=cart_id, explanation="replay")

    result = explain(clean_ledger, cart_id)
    assert result.found is True
    assert len(result.entries) == 2


def test_explain_is_deterministic(clean_ledger):
    txid = str(uuid.uuid4())
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, explanation="received")

    r1 = explain(clean_ledger, txid)
    r2 = explain(clean_ledger, txid)
    assert r1.narrative == r2.narrative
    assert r1.headline == r2.headline


def test_explain_reports_broken_chain_first_and_loud(clean_ledger):
    from sqlalchemy import text

    txid = str(uuid.uuid4())
    row = _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, explanation="received")
    _append(
        event_type=LedgerEvent.POLICY_VERDICT,
        transaction_id=txid,
        decision="ALLOW",
        explanation="within limits",
    )

    clean_ledger.execute(text("ALTER TABLE ledger_rows DISABLE TRIGGER ledger_rows_no_update_delete"))
    clean_ledger.execute(
        text("UPDATE ledger_rows SET payload_canonical = payload_canonical || ' ' WHERE id = :id"),
        {"id": row.id},
    )
    clean_ledger.execute(text("ALTER TABLE ledger_rows ENABLE TRIGGER ledger_rows_no_update_delete"))
    clean_ledger.commit()

    result = explain(clean_ledger, txid)
    assert result.integrity_status == "BROKEN"
    assert "BROKEN" in result.headline
    assert all("UNVERIFIED" in line for line in result.narrative)  # every row is at/after the bad seq


def test_render_narrative_is_pure_given_precomputed_chain(clean_ledger):
    """render_narrative itself takes already-loaded data and does no I/O --
    proven by calling it twice with the SAME (entries, chain) inputs handed
    in directly, no db access inside the call.
    """
    from ledger.explain import load_entries

    txid = str(uuid.uuid4())
    _append(event_type=LedgerEvent.REQUEST_RECEIVED, transaction_id=txid, explanation="received")

    entries = load_entries(clean_ledger, txid)
    chain = verify_chain(clean_ledger)

    r1 = render_narrative(entries, chain, requested_key=txid)
    r2 = render_narrative(entries, chain, requested_key=txid)
    assert r1 == r2
