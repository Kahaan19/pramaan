"""Pure unit tests for eval/metrics.py::compute_metrics -- no DB, no clock.
A metrics harness that miscounts is worse than no metrics at all (same
reasoning as policy/'s own purity requirement), so every rule in the Phase 5
plan's metric definitions gets its own regression test here.
"""

import uuid

from nacl.signing import SigningKey

from eval.batch import INDISTINGUISHABLE, LEGITIMATE, MALICIOUS, Attempt
from eval.harness import (
    OUTCOME_BLOCKED,
    OUTCOME_ESCALATED,
    OUTCOME_EXECUTED,
    AttemptResult,
)
from eval.metrics import compute_metrics

from .conftest import sign_cart, sign_intent, unsigned_cart, unsigned_intent


def _mandate_pair(cart_id: str | None = None, total_paise: int = 50000):
    user_key = SigningKey.generate()
    merchant_key = SigningKey.generate()
    intent = sign_intent(unsigned_intent(), user_key)
    cart = sign_cart(
        unsigned_cart(
            intent.intent_id,
            cart_id=cart_id or ("cart_" + uuid.uuid4().hex[:8]),
            total_paise=total_paise,
            items=[{"sku": "x", "qty": 1, "unit_price_paise": total_paise}],
        ),
        merchant_key,
    )
    return intent, cart


def _attempt(
    label: str,
    *,
    expected_decision: str = "ALLOW",
    expected_control: str | None = "policy",
    cart_id: str | None = None,
    total_paise: int = 50000,
) -> Attempt:
    intent, cart = _mandate_pair(cart_id=cart_id, total_paise=total_paise)
    return Attempt(
        attempt_id="t_" + uuid.uuid4().hex[:6],
        group="test_group",
        label=label,
        expected_decision=expected_decision,
        expected_control=expected_control,
        expected_rule_fired=None,
        expected_mandate_error_code=None,
        note="test attempt",
        intent=intent,
        cart=cart,
    )


def _result(
    attempt: Attempt,
    outcome: str,
    *,
    actual_decision: str | None = None,
    actual_control: str | None = None,
    transaction_id: str | None = "tx-1",
) -> AttemptResult:
    return AttemptResult(
        attempt=attempt,
        outcome=outcome,
        actual_decision=actual_decision,
        actual_control=actual_control,
        rule_fired=None,
        mandate_error_code=None,
        checkout_status=None,
        transaction_id=transaction_id,
    )


def test_executed_malicious_attempt_is_a_false_negative_only():
    attempt = _attempt(MALICIOUS, expected_decision="DENY")
    result = _result(attempt, OUTCOME_EXECUTED, actual_decision="ALLOW", actual_control="policy")

    metrics = compute_metrics([result])

    assert metrics.false_negatives == 1
    assert metrics.false_negative_ids == (attempt.attempt_id,)
    assert metrics.false_positives == 0
    assert metrics.attack_block_rate == 0.0


def test_blocked_legitimate_attempt_is_a_false_positive():
    attempt = _attempt(LEGITIMATE, expected_decision="ALLOW")
    result = _result(attempt, OUTCOME_BLOCKED, actual_decision="DENY", actual_control="policy")

    metrics = compute_metrics([result])

    assert metrics.false_positives == 1
    assert metrics.false_positive_ids == (attempt.attempt_id,)
    assert metrics.false_negatives == 0
    assert metrics.false_positive_rate == 1.0


def test_escalated_attempt_counts_as_neither_blocked_nor_executed():
    legit_step_up = _attempt(LEGITIMATE, expected_decision="STEP_UP")
    result = _result(legit_step_up, OUTCOME_ESCALATED, actual_decision="STEP_UP", actual_control="policy")

    metrics = compute_metrics([result])

    assert metrics.false_positives == 0  # escalation is not a block
    assert metrics.false_negatives == 0  # (irrelevant here, but confirms no crossover)
    assert metrics.escalated_count == 1
    assert metrics.escalation_rate == 1.0


def test_indistinguishable_excluded_from_confusion_matrix_but_counted_in_money_moved():
    attempt = _attempt(INDISTINGUISHABLE, expected_decision="ALLOW", total_paise=35000)
    result = _result(attempt, OUTCOME_EXECUTED, actual_decision="ALLOW", actual_control="policy")

    metrics = compute_metrics([result])

    assert metrics.false_negatives == 0
    assert metrics.false_positives == 0
    assert metrics.money_moved_paise == 35000


def test_money_moved_paise_deduplicates_by_cart_id():
    """An executed cart plus its idempotent replay must contribute its
    amount ONCE, not twice -- the exact miscount an unguarded sum invites
    (see eval/metrics.py::_dedup_sum_paise's docstring).
    """
    cart_id = "cart_shared_" + uuid.uuid4().hex[:8]
    original = _attempt(LEGITIMATE, expected_decision="ALLOW", cart_id=cart_id, total_paise=79900)
    # The replay re-attempt is a SEPARATE Attempt object carrying the exact
    # same signed cart (same cart_id, same amount) -- mirrors L4 in the real
    # batch, where the resubmission is a fresh Attempt wrapping the earlier
    # attempt's intent/cart pair.
    replay = Attempt(
        attempt_id="t_replay",
        group="test_group",
        label=LEGITIMATE,
        expected_decision="REPLAY",
        expected_control="idempotency",
        expected_rule_fired=None,
        expected_mandate_error_code=None,
        note="replay of the original",
        intent=original.intent,
        cart=original.cart,
    )

    results = [
        _result(original, OUTCOME_EXECUTED, actual_decision="ALLOW", actual_control="policy"),
        _result(replay, OUTCOME_EXECUTED, actual_control="idempotency"),
    ]

    metrics = compute_metrics(results)

    assert metrics.money_moved_paise == 79900


def test_money_blocked_paise_also_deduplicates_by_cart_id():
    cart_id = "cart_blocked_" + uuid.uuid4().hex[:8]
    first = _attempt(MALICIOUS, expected_decision="DENY", cart_id=cart_id, total_paise=350000)
    replay = Attempt(
        attempt_id="t_replay_blocked",
        group="test_group",
        label=MALICIOUS,
        expected_decision="MANDATE_ERROR",
        expected_control="mandate",
        expected_rule_fired=None,
        expected_mandate_error_code="replayed_nonce",
        note="nonce-replay of the earlier DENYed cart",
        intent=first.intent,
        cart=first.cart,
    )

    results = [
        _result(first, OUTCOME_BLOCKED, actual_decision="DENY", actual_control="policy"),
        _result(replay, OUTCOME_BLOCKED, actual_control="mandate"),
    ]

    metrics = compute_metrics(results)

    assert metrics.money_blocked_paise == 350000


def test_control_drift_counts_a_mandate_layer_block_predicted_at_policy_layer():
    attempt = _attempt(MALICIOUS, expected_decision="DENY", expected_control="policy")
    result = _result(attempt, OUTCOME_BLOCKED, actual_decision=None, actual_control="mandate")

    metrics = compute_metrics([result])

    assert metrics.control_drift == 1
    assert metrics.control_drift_ids == (attempt.attempt_id,)


def test_control_drift_ignores_attempts_with_no_expected_control():
    attempt = _attempt(LEGITIMATE, expected_decision="ALLOW", expected_control=None)
    result = _result(attempt, OUTCOME_EXECUTED, actual_decision="ALLOW", actual_control="policy")

    metrics = compute_metrics([result])

    assert metrics.control_drift == 0


def test_empty_batch_yields_none_rates_never_a_division_error():
    metrics = compute_metrics([])

    assert metrics.total_attempts == 0
    assert metrics.attack_block_rate is None
    assert metrics.false_positive_rate is None
    assert metrics.escalation_rate is None
    assert metrics.audit_coverage is None
    assert metrics.money_moved_paise == 0
    assert metrics.money_blocked_paise == 0
    assert metrics.false_negatives == 0
    assert metrics.false_positives == 0
