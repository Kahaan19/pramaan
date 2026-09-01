"""verify_chain(): recomputes every row's hash from its STORED
payload_canonical bytes (never a reconstruction from typed columns -- see
ledger/hashing.py for why), checks seq contiguity and prev_hash linkage, and
cross-checks that the denormalized typed columns still agree with the
payload they were copied from.

Also runs a witness reconciliation: spend_reservations and demo_checkouts
are written on a DIFFERENT transaction path than the ledger. A hash chain
cannot detect its own tail being truncated (`DELETE FROM ledger_rows WHERE
seq > N` leaves a perfectly valid, verifying chain) -- reporting "chain
verified OK" in that state would be actively misleading. But an operational
record with zero corresponding ledger rows is a specific, checkable alarm:
an attacker who truncates the ledger tail to hide a transaction would also
have to delete the matching reservation/checkout row, a second and
differently-shaped deletion.
"""

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger.hashing import GENESIS_PREV_HASH, chain_hash
from ledger.models import LedgerRow

_COLUMN_FIELDS = ("event_type", "transaction_id", "cart_id", "intent_id", "actor", "decision", "rule_fired")


@dataclass(frozen=True)
class ChainFinding:
    kind: str  # HASH_MISMATCH | BROKEN_LINK | SEQ_GAP | COLUMN_PAYLOAD_MISMATCH | MISSING_AUDIT_FOR_KNOWN_TRANSACTION
    seq: int | None
    detail: str


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    row_count: int
    head_seq: int | None
    head_hash: str | None
    findings: tuple[ChainFinding, ...]

    @property
    def first_bad_seq(self) -> int | None:
        seqs = [f.seq for f in self.findings if f.seq is not None]
        return min(seqs) if seqs else None


def verify_chain(db: Session) -> ChainVerification:
    rows = db.execute(select(LedgerRow).order_by(LedgerRow.seq.asc())).scalars().all()

    findings: list[ChainFinding] = []
    expected_prev = GENESIS_PREV_HASH
    for i, row in enumerate(rows):
        if row.seq != i:
            findings.append(ChainFinding("SEQ_GAP", row.seq, f"expected seq={i} at this position, found seq={row.seq}"))

        if row.prev_hash != expected_prev:
            findings.append(
                ChainFinding("BROKEN_LINK", row.seq, "prev_hash does not match the preceding row's row_hash")
            )

        recomputed = chain_hash(row.prev_hash, row.payload_canonical.encode("utf-8"))
        if recomputed != row.row_hash:
            findings.append(
                ChainFinding("HASH_MISMATCH", row.seq, "stored row_hash does not match the recomputed hash")
            )

        try:
            payload = json.loads(row.payload_canonical)
        except json.JSONDecodeError:
            payload = {}
        for field in _COLUMN_FIELDS:
            if payload.get(field) != getattr(row, field):
                findings.append(
                    ChainFinding(
                        "COLUMN_PAYLOAD_MISMATCH",
                        row.seq,
                        f"{field}: column={getattr(row, field)!r} payload={payload.get(field)!r}",
                    )
                )

        # Chain continues from this row's ACTUAL row_hash regardless of
        # whether its seq was contiguous -- this is what lets a swapped-seq
        # tamper surface as BROKEN_LINK independently of the SEQ_GAP check.
        expected_prev = row.row_hash

    findings.extend(_witness_reconciliation(db, rows))

    head_seq = rows[-1].seq if rows else None
    head_hash = rows[-1].row_hash if rows else None
    return ChainVerification(
        ok=not findings, row_count=len(rows), head_seq=head_seq, head_hash=head_hash, findings=tuple(findings)
    )


def _witness_reconciliation(db: Session, rows: list[LedgerRow]) -> list[ChainFinding]:
    from executor.spend import SpendReservation
    from models import DemoCheckout

    ledger_cart_ids = {r.cart_id for r in rows if r.cart_id is not None}

    witness_cart_ids: set[str] = set()
    for (cart_id,) in db.execute(select(SpendReservation.cart_id)).all():
        witness_cart_ids.add(cart_id)
    for (cart_id,) in db.execute(select(DemoCheckout.cart_id)).all():
        if cart_id is not None:
            witness_cart_ids.add(cart_id)

    return [
        ChainFinding(
            "MISSING_AUDIT_FOR_KNOWN_TRANSACTION",
            None,
            f"cart_id={cart_id!r} has operational records but zero ledger rows -- the audit trail may have been tampered with",
        )
        for cart_id in sorted(witness_cart_ids - ledger_cart_ids)
    ]
