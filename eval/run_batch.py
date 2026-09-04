#!/usr/bin/env python3
"""Entrypoint for the Phase 5 eval batch: creates a throwaway `pramaan_eval`
database, runs the ~40-attempt batch (eval/batch.py) through the REAL gate
(executor/gate.py::run_gate, only the Razorpay network call stubbed),
verifies the resulting hash chain, computes metrics, writes the report
artifacts, and (by default) drops the database again.

Run from the repo root:
    python3 eval/run_batch.py [--keep-db]

Isolation matters (see the Phase 5 plan's "Batch DB" decision): this must
NEVER touch the demo database the dashboard reads from during judging. See
config.Settings / db.py -- the control plane resolves its Postgres engine
from DATABASE_URL at IMPORT time, so this script sets DATABASE_URL to the
eval database BEFORE importing anything under control-plane/, and never
imports `config` or `db` for its own admin connection (that connection is
built directly from a parsed URL instead).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_PLANE_DIR = REPO_ROOT / "control-plane"
EVAL_DB_NAME = "pramaan_eval"


def _resolve_base_url() -> str:
    values = dotenv_values(REPO_ROOT / ".env")
    url = values.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not found in .env or the environment -- cannot locate Postgres")
    return url


def _admin_engine(base_url: str):
    admin_url = make_url(base_url).set(database="postgres")
    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


def _recreate_eval_database(base_url: str) -> str:
    # NOTE: str(url) / f"{url}" MASK the password as "***" (SQLAlchemy's
    # display-safe __str__) -- render_as_string(hide_password=False) is the
    # actual DSN. Using str() here would set DATABASE_URL to a connection
    # string with a literal "***" password, breaking auth in a way that has
    # nothing to do with the real credentials (caught live in this session).
    eval_url = make_url(base_url).set(database=EVAL_DB_NAME).render_as_string(hide_password=False)
    engine = _admin_engine(base_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": EVAL_DB_NAME},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{EVAL_DB_NAME}"'))
            conn.execute(text(f'CREATE DATABASE "{EVAL_DB_NAME}"'))
    finally:
        engine.dispose()
    return eval_url


def _drop_eval_database(base_url: str) -> None:
    engine = _admin_engine(base_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": EVAL_DB_NAME},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{EVAL_DB_NAME}"'))
    finally:
        engine.dispose()


async def _run_all(db, attempts, keyring, run_attempt):
    """Sequential, on ONE session -- the same pattern control-plane's own
    tests use (see tests/test_gate.py::test_max_pending_step_ups_enforced,
    which loops asyncio.run(run_gate(...)) on a single shared db_session).
    Order is not incidental: L4/M9 replay earlier attempts' exact cart
    objects, and the L5/L6/M12 velocity groups depend on their own buyer's
    prior attempts landing first.
    """
    results = []
    for attempt in attempts:
        result = await run_attempt(db, attempt, keyring)
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-db", action="store_true", help="don't drop pramaan_eval when done")
    args = parser.parse_args()

    base_url = _resolve_base_url()
    print(f"[eval] creating throwaway database {EVAL_DB_NAME!r}...")
    eval_url = _recreate_eval_database(base_url)

    # MUST happen before the first control-plane import (config.py reads
    # DATABASE_URL from os.environ at import time; python-dotenv's own
    # load_dotenv() inside config.py does not override an already-set var).
    os.environ["DATABASE_URL"] = eval_url
    sys.path.insert(0, str(CONTROL_PLANE_DIR))
    sys.path.insert(0, str(REPO_ROOT))

    from db import Base, SessionLocal, engine  # noqa: E402
    import ledger.models  # noqa: E402,F401 -- registers ledger_rows + triggers on Base.metadata
    import mandates.nonce  # noqa: E402,F401 -- registers mandate_nonces on Base.metadata
    from executor import gate as gate_module  # noqa: E402
    from ledger.verify import verify_chain  # noqa: E402
    from policy.rules_schema import get_rules_config  # noqa: E402

    from eval.buyers import build_keyring  # noqa: E402
    from eval.batch import build_attempts  # noqa: E402
    from eval.harness import fake_run_demo_checkout, run_attempt  # noqa: E402
    from eval.metrics import compute_metrics  # noqa: E402
    from eval.report import print_console_table, write_reports  # noqa: E402

    print("[eval] creating schema (tables + append-only triggers)...")
    Base.metadata.create_all(bind=engine)

    # The ONLY stub: no live Razorpay calls in the batch (see this module's
    # docstring). Same pattern as tests/test_gate.py's monkeypatch, applied
    # directly since there's no pytest fixture here.
    gate_module.run_demo_checkout = fake_run_demo_checkout

    keyring, user_keys, merchant_keys = build_keyring()
    attempts = build_attempts(user_keys, merchant_keys)
    print(f"[eval] running {len(attempts)} attempts through the real gate...")

    db = SessionLocal()
    try:
        results = asyncio.run(_run_all(db, attempts, keyring, run_attempt))
    finally:
        db.close()

    verify_db = SessionLocal()
    try:
        chain = verify_chain(verify_db)
    finally:
        verify_db.close()

    _, rules_sha256 = get_rules_config()
    metrics = compute_metrics(results)

    print_console_table(results, metrics, chain)
    reports_dir = REPO_ROOT / "eval" / "reports"
    write_reports(results, metrics, chain, rules_sha256, reports_dir)
    print(f"[eval] wrote {reports_dir / 'latest.json'}, latest.md, attempts.csv")

    engine.dispose()
    if args.keep_db:
        print(f"[eval] --keep-db: leaving {EVAL_DB_NAME!r} in place")
    else:
        print(f"[eval] dropping {EVAL_DB_NAME!r}...")
        _drop_eval_database(base_url)

    ok = metrics.false_negatives == 0 and chain.ok and metrics.audit_coverage == 1.0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
