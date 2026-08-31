"""Mechanical purity enforcement -- not by convention, by static analysis and
a runtime sandbox. Scope note: rules_schema.py's load_rules_config() is the
one acknowledged I/O boundary in this package (reading rules.yaml off disk),
exactly analogous to mandates/keys.py::Keyring.from_dir() being the one I/O
boundary in the otherwise-pure mandates/ package. It gets its own, narrower
check (no DB/network/randomness) rather than the strict allowlist applied to
the actual evaluation-time modules.
"""

import ast
import random
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import policy
from policy.context import SpendRecord
from policy.engine import evaluate
from policy.verdict import Decision

from .conftest import make_policy_context

POLICY_DIR = Path(policy.__file__).resolve().parent

# The actual evaluation-time call graph: everything evaluate() touches.
PURE_MODULES = ["context.py", "rules.py", "engine.py", "verdict.py", "rule_names.py"]

STRICT_ALLOWED_IMPORTS = {
    "dataclasses",
    "datetime",
    "enum",
    "typing",
    "collections",
    "__future__",
    "policy",
    # Verified (this session) to import no DB/network/randomness -- pure
    # pydantic/dataclass domain types only. context.py needs VerifiedMandate.
    "mandates",
}

# rules_schema.py may do disk I/O (it's the load-time config boundary) but
# must still never touch a database, the network, or a source of randomness.
FORBIDDEN_EVERYWHERE = {"sqlalchemy", "db", "httpx", "requests", "mcp", "random", "secrets", "socket"}

FORBIDDEN_CALL_NAMES = {"now", "utcnow", "today", "random", "uuid4", "time", "monotonic"}


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _forbidden_calls(path: Path) -> list[str]:
    """Flags actual CALLS to now()/utcnow()/today()/random()/uuid4()/time()/
    monotonic() -- not bare attribute reads like `ctx.now`, which is exactly
    how a rule is supposed to consume the injected timestamp.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in FORBIDDEN_CALL_NAMES:
            findings.append(f"{path.name}:{node.lineno}: {name}(...)")
    return findings


@pytest.mark.parametrize("filename", PURE_MODULES)
def test_pure_module_import_allowlist(filename):
    modules = _imported_top_level_modules(POLICY_DIR / filename)
    disallowed = modules - STRICT_ALLOWED_IMPORTS
    assert not disallowed, f"{filename} imports disallowed modules: {disallowed}"


@pytest.mark.parametrize("filename", PURE_MODULES)
def test_pure_module_has_no_forbidden_calls(filename):
    findings = _forbidden_calls(POLICY_DIR / filename)
    assert not findings, f"forbidden clock/random calls found: {findings}"


def test_rules_schema_never_touches_db_network_or_randomness():
    modules = _imported_top_level_modules(POLICY_DIR / "rules_schema.py")
    disallowed = modules & FORBIDDEN_EVERYWHERE
    assert not disallowed, f"rules_schema.py imports forbidden modules: {disallowed}"


def test_evaluate_never_touches_the_network_or_the_clock(rules_config, monkeypatch):
    """Runtime sandbox: patch out every socket/clock primitive evaluate()
    could plausibly reach and confirm it still runs to completion.
    """

    def _boom(*args, **kwargs):
        raise AssertionError("evaluate() touched the network or the clock")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(time, "monotonic", _boom)
    monkeypatch.setattr(random, "random", _boom)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    ctx = make_policy_context(rules_config, now=now)
    verdict = evaluate(ctx)  # must not raise
    assert verdict.decision == Decision.ALLOW


def test_permutation_invariance_of_recent_spend(rules_config):
    """Shuffling the order of recent_spend must not change the verdict --
    INCLUDING the reason string. This is the test that actually catches a
    missing ORDER BY in the impure loader: if the reason string named "the
    oldest transaction" or similar, row order would leak into the verdict.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    records = tuple(
        SpendRecord(amount_paise=amt, created_at=now - timedelta(seconds=sec))
        for amt, sec in [(100000, 10), (200000, 20), (150000, 3500), (49001, 3550)]
    )

    verdicts = set()
    import itertools

    for perm in itertools.permutations(records):
        ctx = make_policy_context(rules_config, now=now, recent_spend=perm, amount_paise=1000)
        v = evaluate(ctx)
        verdicts.add((v.decision, v.rule_fired, v.reason))

    assert len(verdicts) == 1, f"verdict depended on recent_spend order: {verdicts}"


def test_repeatability_100_calls(rules_config):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ctx = make_policy_context(rules_config, now=now, amount_paise=150000)
    verdicts = {evaluate(ctx) for _ in range(100)}
    assert len(verdicts) == 1


def test_context_is_hashable(rules_config):
    """Compile-time-ish proof nothing mutable snuck into PolicyContext: if a
    field were e.g. a plain list or dict, this raises TypeError.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ctx = make_policy_context(rules_config, now=now)
    hash(ctx)  # must not raise


def test_verdict_is_hashable(rules_config):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ctx = make_policy_context(rules_config, now=now)
    verdict = evaluate(ctx)
    hash(verdict)  # must not raise
