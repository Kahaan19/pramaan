"""compute_metrics(): PURE. Attempts (already classified by eval/harness.py)
in, a Metrics value out -- no DB, no clock, no I/O. A metrics harness that
miscounts is worse than no metrics at all, so this is unit-tested
exhaustively in control-plane/tests/test_eval_metrics.py, the same way
policy/ is pure and tested for the same reason.

"Blocked" is the guard's positive class: a false negative is an attack that
EXECUTED, a false positive is a legitimate attempt that was BLOCKED.
INDISTINGUISHABLE attempts are excluded from both -- see eval/batch.py's
module docstring for why a rate limiter's inability to catch the FIRST moves
of a runaway loop must not be scored as a false negative.
"""

from dataclasses import dataclass

from eval.batch import INDISTINGUISHABLE, LEGITIMATE, MALICIOUS
from eval.harness import OUTCOME_BLOCKED, OUTCOME_ESCALATED, OUTCOME_EXECUTED, AttemptResult


@dataclass(frozen=True)
class Metrics:
    total_attempts: int
    legitimate_count: int
    malicious_count: int
    indistinguishable_count: int
    executed_count: int
    blocked_count: int
    escalated_count: int

    attack_block_rate: float | None
    false_negatives: int
    false_negative_ids: tuple[str, ...]
    false_positives: int
    false_positive_ids: tuple[str, ...]
    false_positive_rate: float | None
    escalation_rate: float | None

    money_moved_paise: int
    money_blocked_paise: int

    audit_coverage: float | None
    control_drift: int
    control_drift_ids: tuple[str, ...]


def _dedup_sum_paise(results: list[AttemptResult], outcome: str) -> int:
    """Sums cart.total_paise over attempts with the given outcome, deduped by
    cart_id -- a cart resubmitted more than once (L4's legit retry, M9's
    replay attack) must contribute its amount at most once to either
    money_moved_paise or money_blocked_paise, or a replay would silently
    double-count real money exposure that only ever existed once.
    """
    seen_cart_ids: set[str] = set()
    total = 0
    for r in results:
        if r.outcome != outcome:
            continue
        cart_id = r.attempt.cart.cart_id
        if cart_id in seen_cart_ids:
            continue
        seen_cart_ids.add(cart_id)
        total += r.attempt.cart.total_paise
    return total


def compute_metrics(results: list[AttemptResult]) -> Metrics:
    total = len(results)
    legitimate = [r for r in results if r.attempt.label == LEGITIMATE]
    malicious = [r for r in results if r.attempt.label == MALICIOUS]
    indistinguishable = [r for r in results if r.attempt.label == INDISTINGUISHABLE]

    executed = [r for r in results if r.outcome == OUTCOME_EXECUTED]
    blocked = [r for r in results if r.outcome == OUTCOME_BLOCKED]
    escalated = [r for r in results if r.outcome == OUTCOME_ESCALATED]

    false_negatives = [r for r in malicious if r.outcome == OUTCOME_EXECUTED]
    false_positives = [r for r in legitimate if r.outcome == OUTCOME_BLOCKED]
    blocked_malicious = [r for r in malicious if r.outcome == OUTCOME_BLOCKED]
    escalated_legitimate = [r for r in legitimate if r.outcome == OUTCOME_ESCALATED]

    control_drift_rows = [
        r
        for r in results
        if r.attempt.expected_control is not None and r.actual_control != r.attempt.expected_control
    ]

    audit_covered = sum(1 for r in results if r.transaction_id is not None)

    return Metrics(
        total_attempts=total,
        legitimate_count=len(legitimate),
        malicious_count=len(malicious),
        indistinguishable_count=len(indistinguishable),
        executed_count=len(executed),
        blocked_count=len(blocked),
        escalated_count=len(escalated),
        attack_block_rate=(len(blocked_malicious) / len(malicious)) if malicious else None,
        false_negatives=len(false_negatives),
        false_negative_ids=tuple(r.attempt.attempt_id for r in false_negatives),
        false_positives=len(false_positives),
        false_positive_ids=tuple(r.attempt.attempt_id for r in false_positives),
        false_positive_rate=(len(false_positives) / len(legitimate)) if legitimate else None,
        escalation_rate=(len(escalated_legitimate) / len(legitimate)) if legitimate else None,
        money_moved_paise=_dedup_sum_paise(results, OUTCOME_EXECUTED),
        money_blocked_paise=_dedup_sum_paise(results, OUTCOME_BLOCKED),
        audit_coverage=(audit_covered / total) if total else None,
        control_drift=len(control_drift_rows),
        control_drift_ids=tuple(r.attempt.attempt_id for r in control_drift_rows),
    )
