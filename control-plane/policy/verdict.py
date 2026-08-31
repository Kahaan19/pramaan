from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Decision(str, Enum):
    ALLOW = "ALLOW"
    STEP_UP = "STEP_UP"
    DENY = "DENY"


@dataclass(frozen=True)
class Violation:
    """What one rule found. decision is DENY or STEP_UP -- a rule that
    passes returns None instead of a Violation with decision=ALLOW.
    """

    decision: Decision
    rule_fired: str
    reason: str


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    rule_fired: str | None  # None only when decision is ALLOW
    reason: str
    evaluated_at: datetime
    all_violations: tuple[Violation, ...]
    # Names exactly which policy-file version produced this verdict, so
    # "same inputs -> same verdict" is falsifiable even though rules.yaml
    # is itself an input that isn't part of the request.
    rules_version: int
    rules_sha256: str
