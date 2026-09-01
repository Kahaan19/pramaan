"""LedgerRow + the append-only enforcement triggers.

Base.metadata.create_all() creates tables but never fires DDL like CREATE
TRIGGER, so the triggers are attached via event.listen(..., "after_create")
on this table specifically -- that way they exist in test databases too, not
just in a hand-run migration. They fire exactly once, the first time this
table is actually created; a table that already exists (from an earlier
create_all in the same database) does not get re-triggered, matching
Postgres's own "CREATE TRIGGER" not being idempotent.

Honest limit, not hidden: the app connects as the `postgres` superuser
(see .env), which bypasses every grant and can ALTER/DROP the trigger or the
table outright. These triggers guard against THIS CODEBASE's own bugs (an
accidental UPDATE, a stray DELETE) -- they are not a control against anyone
holding DB credentials. The hash chain (ledger/verify.py) is what actually
detects tampering; the trigger just makes the easy, accidental case loud
immediately instead of silent until the next verify_chain() run.
"""

from sqlalchemy import BigInteger, DDL, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class LedgerRow(Base):
    __tablename__ = "ledger_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    transaction_id: Mapped[str] = mapped_column(String(36), index=True)
    cart_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    intent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rule_fired: Mapped[str | None] = mapped_column(String(64), nullable=True)
    razorpay_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str] = mapped_column(Text)
    # The authoritative hashed bytes, verbatim. TEXT, never JSONB -- JSONB
    # reorders keys and normalizes numbers, which would silently change the
    # bytes verify_chain() re-hashes.
    payload_canonical: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("seq", name="uq_ledger_rows_seq"),
        # UNIQUE(prev_hash) is what actually prevents a silent chain fork: a
        # fork requires two rows claiming the same predecessor, which this
        # constraint makes an IntegrityError instead of a silently-accepted
        # write. It demotes the append lock (ledger/writer.py) from "a
        # correctness requirement" to "a performance optimization that avoids
        # retries" -- see ledger/writer.py's docstring.
        UniqueConstraint("prev_hash", name="uq_ledger_rows_prev_hash"),
        UniqueConstraint("row_hash", name="uq_ledger_rows_row_hash"),
    )


_DENY_MUTATION_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION ledger_rows_deny_mutation() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'ledger_rows is append-only: %% is not permitted', TG_OP;
    END;
    $$ LANGUAGE plpgsql;
    """
)

_ROW_TRIGGER = DDL(
    """
    CREATE TRIGGER ledger_rows_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_rows
    FOR EACH ROW EXECUTE FUNCTION ledger_rows_deny_mutation();
    """
)

_STMT_TRIGGER = DDL(
    """
    CREATE TRIGGER ledger_rows_no_truncate
    BEFORE TRUNCATE ON ledger_rows
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_rows_deny_mutation();
    """
)

# Test-only escape hatch: temporarily disable both triggers, wipe the table,
# re-enable. Never called by application code -- only by test fixtures that
# need a clean genesis state between test runs.
_RESET_FN = DDL(
    """
    CREATE OR REPLACE FUNCTION ledger_reset_for_tests() RETURNS void AS $$
    BEGIN
        ALTER TABLE ledger_rows DISABLE TRIGGER ledger_rows_no_update_delete;
        ALTER TABLE ledger_rows DISABLE TRIGGER ledger_rows_no_truncate;
        TRUNCATE ledger_rows;
        ALTER TABLE ledger_rows ENABLE TRIGGER ledger_rows_no_update_delete;
        ALTER TABLE ledger_rows ENABLE TRIGGER ledger_rows_no_truncate;
    END;
    $$ LANGUAGE plpgsql;
    """
)

event.listen(LedgerRow.__table__, "after_create", _DENY_MUTATION_FN)
event.listen(LedgerRow.__table__, "after_create", _ROW_TRIGGER)
event.listen(LedgerRow.__table__, "after_create", _STMT_TRIGGER)
event.listen(LedgerRow.__table__, "after_create", _RESET_FN)
