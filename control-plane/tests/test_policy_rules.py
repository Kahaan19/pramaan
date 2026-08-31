from datetime import datetime, timedelta, timezone

from mandates.verify import verify_mandate_chain
from policy.context import SpendRecord, build_context
from policy.engine import evaluate
from policy.verdict import Decision

from .conftest import MERCHANT_ID, make_policy_context

NOW = datetime.now(timezone.utc).replace(microsecond=0)

# From the real rules.yaml: per_transaction_cap_paise=200000,
# step_up.amount_threshold_paise=100000, velocity.max_txns=5,
# velocity.max_total_paise=500000, velocity.window_seconds=3600,
# max_pending_step_ups=3, allowed_categories=[retail, groceries].


# ---- per_transaction_cap boundaries ----


def test_per_txn_cap_one_under_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=199999)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_per_txn_cap_exactly_at_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=200000)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_per_txn_cap_one_over_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=200001)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "per_transaction_cap"


# ---- expiry boundaries (must agree with mandates/scope.py's inclusive `>`) ----


def test_expiry_one_second_before_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, intent_expires_at=NOW + timedelta(seconds=1))
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_expiry_exactly_now_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, intent_expires_at=NOW)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_expiry_one_second_after_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, intent_expires_at=NOW - timedelta(seconds=1))
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "mandate_expiry"


def test_expiry_boundary_agrees_with_mandate_layer_scope_check(signed_pair, keyring):
    """The policy rule's expiry comparison must be IDENTICAL to
    mandates/scope.py's check_not_expired -- both use `now > expires_at`
    (inclusive at the boundary) -- or the two layers silently diverge.
    """
    from mandates.scope import check_not_expired

    expires_at = NOW
    intent, cart = signed_pair(intent_overrides={"expires_at": expires_at})

    # scope.py: valid at exactly expires_at
    check_not_expired(intent, expires_at)  # must not raise

    # one second later: both layers must reject
    from mandates.errors import MandateError

    try:
        check_not_expired(intent, expires_at + timedelta(seconds=1))
        raised = False
    except MandateError:
        raised = True
    assert raised


# ---- velocity: age boundary ----


def test_velocity_txn_aged_3599s_still_counts(rules_config):
    spend = (SpendRecord(amount_paise=499001, created_at=NOW - timedelta(seconds=3599)),)
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=spend, amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "velocity_total_amount"


def test_velocity_txn_aged_3600s_has_aged_out(rules_config):
    ctx = make_policy_context(
        rules_config,
        now=NOW,
        recent_spend=(SpendRecord(amount_paise=499001, created_at=NOW - timedelta(seconds=3600)),),
        amount_paise=1000,
    )
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY  # aged-out record must not count


def test_velocity_txn_aged_3601s_has_aged_out(rules_config):
    ctx = make_policy_context(
        rules_config,
        now=NOW,
        recent_spend=(SpendRecord(amount_paise=499001, created_at=NOW - timedelta(seconds=3601)),),
        amount_paise=1000,
    )
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


# ---- velocity: count boundary ----


def _spend_n_records(n: int, amount_each: int = 1) -> tuple[SpendRecord, ...]:
    return tuple(SpendRecord(amount_paise=amount_each, created_at=NOW - timedelta(seconds=10)) for _ in range(n))


def test_velocity_count_at_max_minus_one_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=_spend_n_records(4), amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_velocity_count_at_exactly_max_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=_spend_n_records(5), amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "velocity_txn_count"


# ---- velocity: sum boundary ----


def test_velocity_sum_exactly_at_max_total_allows(rules_config):
    spend = (SpendRecord(amount_paise=499000, created_at=NOW - timedelta(seconds=10)),)
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=spend, amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


def test_velocity_sum_one_paise_over_denies(rules_config):
    spend = (SpendRecord(amount_paise=499001, created_at=NOW - timedelta(seconds=10)),)
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=spend, amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "velocity_total_amount"


def test_velocity_empty_history_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, recent_spend=(), amount_paise=1000)
    verdict = evaluate(ctx)
    assert verdict.decision != Decision.DENY


# ---- velocity: per-user isolation is enforced by the LOADER, not the rule.
# The rule only ever sees records the loader already scoped to this user, so
# we prove that scoping happens in the loader/build_context path instead. ----


def test_recent_spend_only_reflects_what_the_context_was_given(rules_config):
    """PolicyContext.recent_spend is what the caller (the impure loader)
    decided to include. This test documents that the pure rule trusts its
    input completely -- per-user/per-merchant scoping is the loader's job,
    not something a rule can or should re-derive.
    """
    other_users_spend = (SpendRecord(amount_paise=499001, created_at=NOW - timedelta(seconds=10)),)
    ctx_with = make_policy_context(rules_config, now=NOW, recent_spend=other_users_spend, amount_paise=1000)
    ctx_without = make_policy_context(rules_config, now=NOW, recent_spend=(), amount_paise=1000)
    assert evaluate(ctx_with).decision == Decision.DENY
    assert evaluate(ctx_without).decision != Decision.DENY


# ---- category ----


def test_category_allowed_passes(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, category="retail")
    assert evaluate(ctx).decision != Decision.DENY


def test_category_not_allowed_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, category="electronics")
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "category"


def test_category_absent_denies_fail_closed(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, category=None)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "category"


def test_category_case_mismatch_denies_no_normalization(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, category="Retail")
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "category"


def test_category_whitespace_mismatch_denies_no_normalization(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, category=" retail")
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "category"


# ---- merchant allowlist ----


def test_merchant_allowlisted_passes(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, merchant_id=MERCHANT_ID)
    assert evaluate(ctx).decision != Decision.DENY


def test_merchant_not_allowlisted_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, merchant_id="merchant_unknown")
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "merchant_allowlist"


def test_merchant_in_intents_own_allowlist_but_not_global_denies(
    signed_pair, user_signing_key, merchant_signing_key, rules_config
):
    """Defence in depth: the intent's OWN merchant_allowlist (checked by
    mandates/scope.py) is a different, weaker guarantee than the platform's
    GLOBAL allowlist in rules.yaml (checked here). A merchant a user
    authorized but the platform never onboarded must still be denied.
    """
    from mandates.keys import Keyring

    sneaky_merchant = "merchant_sneaky"
    # A keyring where merchant_sneaky IS a registered, valid signer (using
    # the same test key) -- this test is about the ALLOWLIST, not signature
    # validity, so the signature must genuinely verify.
    sneaky_keyring = Keyring(
        user_keys={"user_kahaan": user_signing_key.verify_key},
        merchant_keys={sneaky_merchant: merchant_signing_key.verify_key},
    )
    intent, cart = signed_pair(
        intent_overrides={"merchant_allowlist": [sneaky_merchant]},
        cart_overrides={"merchant_id": sneaky_merchant},
    )
    # Passes the mandate layer -- sneaky_merchant IS in the intent's own allowlist.
    verified = verify_mandate_chain(intent, cart, sneaky_keyring, NOW)

    ctx = build_context(
        verified=verified,
        recent_spend=(),
        pending_step_up_count=0,
        rules=rules_config[0],
        rules_sha256=rules_config[1],
    )
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "merchant_allowlist"


# ---- max_pending_step_ups ----


def test_pending_step_ups_under_limit_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, pending_step_up_count=2)
    assert evaluate(ctx).decision != Decision.DENY


def test_pending_step_ups_at_limit_denies(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, pending_step_up_count=3)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "max_pending_step_ups"


# ---- step-up ----


def test_step_up_one_paise_under_threshold_allows(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=99999, human_present=True)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.ALLOW
    assert verdict.rule_fired is None


def test_step_up_exactly_at_threshold_steps_up(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=100000, human_present=True)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.STEP_UP
    assert verdict.rule_fired == "step_up_amount_threshold"


def test_delegated_intent_under_threshold_still_steps_up(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=1000, human_present=False)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.STEP_UP
    assert verdict.rule_fired == "step_up_delegated_intent"


# ---- precedence: pins the evaluation_order ----


def test_over_cap_and_bad_merchant_reports_cap_first(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=200001, merchant_id="merchant_unknown")
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "per_transaction_cap"


def test_expired_and_over_cap_reports_expiry_first(rules_config):
    ctx = make_policy_context(
        rules_config, now=NOW, amount_paise=200001, intent_expires_at=NOW - timedelta(seconds=1)
    )
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "mandate_expiry"


def test_velocity_full_and_step_up_eligible_denies_not_steps_up(rules_config):
    ctx = make_policy_context(
        rules_config, now=NOW, recent_spend=_spend_n_records(5), amount_paise=150000
    )
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "velocity_txn_count"


def test_both_step_up_rules_firing_reports_amount_threshold_first(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=150000, human_present=False)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.STEP_UP
    assert verdict.rule_fired == "step_up_amount_threshold"
    assert len(verdict.all_violations) == 2  # both fired; the first supplies rule_fired


def test_over_cap_and_delegated_denies_not_steps_up(rules_config):
    ctx = make_policy_context(rules_config, now=NOW, amount_paise=200001, human_present=False)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.DENY
    assert verdict.rule_fired == "per_transaction_cap"


# ---- ALLOW shape ----


def test_no_violations_allows_with_no_rule_fired(rules_config):
    ctx = make_policy_context(rules_config, now=NOW)
    verdict = evaluate(ctx)
    assert verdict.decision == Decision.ALLOW
    assert verdict.rule_fired is None
    assert verdict.all_violations == ()
    assert verdict.reason


def test_verdict_evaluated_at_matches_context_now(rules_config):
    ctx = make_policy_context(rules_config, now=NOW)
    verdict = evaluate(ctx)
    assert verdict.evaluated_at == NOW


def test_verdict_carries_rules_version_and_sha(rules_config):
    ctx = make_policy_context(rules_config, now=NOW)
    verdict = evaluate(ctx)
    assert verdict.rules_version == rules_config[0].version
    assert verdict.rules_sha256 == rules_config[1]
