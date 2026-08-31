"""PolicyContext: everything a rule needs, and nothing it can use to cheat.

Deliberately NOT built from a SQLAlchemy ORM row -- an ORM object can
lazy-load related data on attribute access, i.e. perform I/O from inside a
"pure" rule. recent_spend is a tuple of frozen SpendRecord values, mapped
from ORM rows at the boundary, outside this package.

`now` is not read from the clock here: build_context() takes it from the
VerifiedMandate produced by mandates.verify.verify_mandate_chain(), so the
mandate layer and the policy layer evaluate against the exact same instant.
"""

from dataclasses import dataclass
from datetime import datetime

from mandates.verify import VerifiedMandate
from policy.rules_schema import RulesConfig


@dataclass(frozen=True)
class SpendRecord:
    amount_paise: int
    created_at: datetime


@dataclass(frozen=True)
class PolicyContext:
    now: datetime
    user_id: str
    merchant_id: str
    category: str | None
    amount_paise: int
    intent_expires_at: datetime
    human_present: bool
    # Wider-than-window prefetch (see executor/spend.py's loader) -- rules.py
    # applies the authoritative, exclusive-at-boundary cutoff itself. This is
    # the single source of truth for what counts, so SQL and rule logic
    # cannot define the window boundary two different ways.
    recent_spend: tuple[SpendRecord, ...]
    pending_step_up_count: int
    rules: RulesConfig
    rules_sha256: str


def build_context(
    *,
    verified: VerifiedMandate,
    recent_spend: tuple[SpendRecord, ...],
    pending_step_up_count: int,
    rules: RulesConfig,
    rules_sha256: str,
) -> PolicyContext:
    return PolicyContext(
        now=verified.verified_at,
        user_id=verified.intent.user_id,
        merchant_id=verified.cart.merchant_id,
        category=verified.intent.category,
        amount_paise=verified.cart.total_paise,
        intent_expires_at=verified.intent.expires_at,
        human_present=verified.intent.human_present,
        recent_spend=recent_spend,
        pending_step_up_count=pending_step_up_count,
        rules=rules,
        rules_sha256=rules_sha256,
    )
