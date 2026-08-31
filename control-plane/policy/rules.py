"""The nine pure policy rules. Each is `rule(ctx: PolicyContext) -> Violation
| None` -- returns None when it does not fire. No I/O, no randomness, no
clock reads (ctx.now is the only notion of "now").

Defence in depth, deliberately: merchant_allowlist here checks the GLOBAL
platform allowlist from rules.yaml, separate from (and in addition to) the
INTENT's own allowlist that mandates/scope.py already checked. Both must
pass; neither substitutes for the other. Same relationship for expiry:
mandates/scope.py's check_not_expired is unreachable in the Phase 2 flow
(mandate verification runs first), but a Phase 4 approved STEP_UP executes
LATER, against an intent that may have expired while queued -- this rule is
what catches that. Its comparison is deliberately identical to scope.py's
(`now > expires_at`, inclusive at the boundary) so the two layers cannot
silently diverge.
"""

from collections.abc import Callable
from datetime import timedelta

from policy.context import PolicyContext, SpendRecord
from policy.rule_names import RULE_NAMES
from policy.verdict import Decision, Violation

RuleFn = Callable[[PolicyContext], Violation | None]


def mandate_expiry(ctx: PolicyContext) -> Violation | None:
    if ctx.now > ctx.intent_expires_at:
        return Violation(
            decision=Decision.DENY,
            rule_fired="mandate_expiry",
            reason=f"intent expired at {ctx.intent_expires_at.isoformat()} (now={ctx.now.isoformat()})",
        )
    return None


def per_transaction_cap(ctx: PolicyContext) -> Violation | None:
    cap = ctx.rules.limits.per_transaction_cap_paise
    if ctx.amount_paise > cap:
        return Violation(
            decision=Decision.DENY,
            rule_fired="per_transaction_cap",
            reason=f"amount {ctx.amount_paise} paise exceeds per-transaction cap {cap} paise",
        )
    return None


def _spend_in_window(ctx: PolicyContext) -> tuple[SpendRecord, ...]:
    """Authoritative, exclusive-at-boundary filter. ctx.recent_spend may be a
    wider prefetch than the window; this is the single source of truth for
    what actually counts.
    """
    window = ctx.rules.limits.velocity.window_seconds
    cutoff = ctx.now - timedelta(seconds=window)
    return tuple(r for r in ctx.recent_spend if r.created_at > cutoff)


def velocity_txn_count(ctx: PolicyContext) -> Violation | None:
    in_window = _spend_in_window(ctx)
    max_txns = ctx.rules.limits.velocity.max_txns
    if len(in_window) + 1 > max_txns:
        return Violation(
            decision=Decision.DENY,
            rule_fired="velocity_txn_count",
            reason=(
                f"{len(in_window)} transactions already in the last "
                f"{ctx.rules.limits.velocity.window_seconds}s; adding this one would exceed "
                f"max_txns={max_txns}"
            ),
        )
    return None


def velocity_total_amount(ctx: PolicyContext) -> Violation | None:
    in_window = _spend_in_window(ctx)
    spent = sum(r.amount_paise for r in in_window)
    max_total = ctx.rules.limits.velocity.max_total_paise
    if spent + ctx.amount_paise > max_total:
        return Violation(
            decision=Decision.DENY,
            rule_fired="velocity_total_amount",
            reason=(
                f"spent {spent} paise in the last {ctx.rules.limits.velocity.window_seconds}s; "
                f"adding {ctx.amount_paise} would exceed max_total_paise={max_total}"
            ),
        )
    return None


def max_pending_step_ups(ctx: PolicyContext) -> Violation | None:
    limit = ctx.rules.limits.max_pending_step_ups
    if ctx.pending_step_up_count >= limit:
        return Violation(
            decision=Decision.DENY,
            rule_fired="max_pending_step_ups",
            reason=f"{ctx.pending_step_up_count} STEP_UP requests already pending for this user (limit {limit})",
        )
    return None


def merchant_allowlist(ctx: PolicyContext) -> Violation | None:
    # Exact match, no normalization -- merchant_id is expected to be a
    # canonical machine-generated identifier, not free text. A mismatched
    # case or stray whitespace fails closed (DENY) rather than being
    # silently normalized into a false match.
    if ctx.merchant_id not in ctx.rules.merchant_allowlist_set:
        return Violation(
            decision=Decision.DENY,
            rule_fired="merchant_allowlist",
            reason=f"merchant_id={ctx.merchant_id!r} is not in the platform merchant allowlist",
        )
    return None


def category(ctx: PolicyContext) -> Violation | None:
    # Fails closed: category is a SELF-DECLARED label on the user-signed
    # intent, not a merchant-attested fact (see README). An absent category
    # is treated the same as a disallowed one -- an omitted field must never
    # be a way to skip a control.
    if ctx.category is None or ctx.category not in ctx.rules.allowed_categories_set:
        return Violation(
            decision=Decision.DENY,
            rule_fired="category",
            reason=f"category={ctx.category!r} is not in the platform's allowed_categories",
        )
    return None


def step_up_amount_threshold(ctx: PolicyContext) -> Violation | None:
    threshold = ctx.rules.step_up.amount_threshold_paise
    if ctx.amount_paise >= threshold:
        return Violation(
            decision=Decision.STEP_UP,
            rule_fired="step_up_amount_threshold",
            reason=f"amount {ctx.amount_paise} paise is at/above the step-up threshold {threshold} paise",
        )
    return None


def step_up_delegated_intent(ctx: PolicyContext) -> Violation | None:
    if ctx.rules.step_up.step_up_on_delegated_intent and not ctx.human_present:
        return Violation(
            decision=Decision.STEP_UP,
            rule_fired="step_up_delegated_intent",
            reason="intent was signed with human_present=false (delegated) and step_up_on_delegated_intent is enabled",
        )
    return None


RULE_REGISTRY: dict[str, RuleFn] = {
    "mandate_expiry": mandate_expiry,
    "per_transaction_cap": per_transaction_cap,
    "velocity_txn_count": velocity_txn_count,
    "velocity_total_amount": velocity_total_amount,
    "max_pending_step_ups": max_pending_step_ups,
    "merchant_allowlist": merchant_allowlist,
    "category": category,
    "step_up_amount_threshold": step_up_amount_threshold,
    "step_up_delegated_intent": step_up_delegated_intent,
}
assert set(RULE_REGISTRY.keys()) == set(RULE_NAMES), "RULE_REGISTRY must exactly match RULE_NAMES"
