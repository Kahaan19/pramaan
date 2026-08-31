"""evaluate(): the deterministic core. First DENY wins (and short-circuits --
evaluating every remaining rule risks a later one raising on data an earlier
rule would already have denied, turning a clean DENY into a 500); else the
first STEP_UP; else ALLOW. A rule that raises is converted to DENY, fail
closed, naming the raising rule -- it never propagates.

The evaluation order is read from context.rules.evaluation_order (validated
by rules_schema.py to be an exact permutation of RULE_REGISTRY's keys), so
rules.yaml is the single source of truth for ordering -- the code cannot
drift from the documentation because there is only one copy of the order.
"""

from policy.context import PolicyContext
from policy.rules import RULE_REGISTRY
from policy.verdict import Decision, Verdict, Violation


def evaluate(context: PolicyContext) -> Verdict:
    violations: list[Violation] = []
    deny: Violation | None = None
    step_up: Violation | None = None

    for rule_name in context.rules.evaluation_order:
        rule_fn = RULE_REGISTRY[rule_name]
        try:
            violation = rule_fn(context)
        except Exception as exc:  # fail closed: a raising rule denies, never propagates
            violation = Violation(
                decision=Decision.DENY,
                rule_fired=rule_name,
                reason=f"rule {rule_name!r} raised {exc.__class__.__name__}: {exc}",
            )

        if violation is None:
            continue

        violations.append(violation)

        if violation.decision is Decision.DENY:
            deny = violation
            break  # first DENY wins; no point evaluating further
        elif step_up is None:
            step_up = violation
            # do not break: a later rule may still DENY, which must win

    if deny is not None:
        decision, rule_fired, reason = Decision.DENY, deny.rule_fired, deny.reason
    elif step_up is not None:
        decision, rule_fired, reason = Decision.STEP_UP, step_up.rule_fired, step_up.reason
    else:
        decision, rule_fired, reason = Decision.ALLOW, None, "no rule fired; transaction is within all limits"

    return Verdict(
        decision=decision,
        rule_fired=rule_fired,
        reason=reason,
        evaluated_at=context.now,
        all_violations=tuple(violations),
        rules_version=context.rules.version,
        rules_sha256=context.rules_sha256,
    )
