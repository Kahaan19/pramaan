import copy

import pytest
import yaml
from pydantic import ValidationError

from policy.rule_names import RULE_NAMES
from policy.rules_schema import DEFAULT_RULES_PATH, load_rules_config

VALID_RULES: dict = {
    "version": 1,
    "limits": {
        "per_transaction_cap_paise": 200000,
        "velocity": {"window_seconds": 3600, "max_txns": 5, "max_total_paise": 500000},
        "max_pending_step_ups": 3,
    },
    "merchant_allowlist": ["merchant_demo_01"],
    "allowed_categories": ["retail", "groceries"],
    "step_up": {"amount_threshold_paise": 100000, "step_up_on_delegated_intent": True},
    "mandate": {"reject_if_expired": True, "reject_replayed_nonce": True},
    "evaluation_order": list(RULE_NAMES),
}


def _write_and_load(tmp_path, data: dict):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_rules_config(path)


def test_the_real_rules_yaml_loads_clean():
    config, digest = load_rules_config(DEFAULT_RULES_PATH)
    assert config.version == 1
    assert len(digest) == 64  # sha256 hex


def test_valid_config_loads(tmp_path):
    config, digest = _write_and_load(tmp_path, VALID_RULES)
    assert config.evaluation_order == tuple(RULE_NAMES)
    assert len(digest) == 64


def test_unknown_top_level_key_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["totally_made_up_key"] = True
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_unknown_nested_key_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["limits"]["typo_cap_paise"] = 100
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_float_amount_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["limits"]["per_transaction_cap_paise"] = 200000.0
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_negative_amount_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["limits"]["per_transaction_cap_paise"] = -1
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_zero_max_pending_step_ups_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["limits"]["max_pending_step_ups"] = 0
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_missing_section_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    del bad["step_up"]
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_wrong_version_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["version"] = 2
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_empty_merchant_allowlist_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["merchant_allowlist"] = []
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_empty_allowed_categories_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["allowed_categories"] = []
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_mandate_reject_if_expired_false_rejected(tmp_path):
    """This knob isn't real -- expiry is always enforced in mandates/. A
    `false` here would be a lie, so it must fail loudly.
    """
    bad = copy.deepcopy(VALID_RULES)
    bad["mandate"]["reject_if_expired"] = False
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_mandate_reject_replayed_nonce_false_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["mandate"]["reject_replayed_nonce"] = False
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_evaluation_order_missing_entry_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["evaluation_order"] = list(RULE_NAMES[:-1])  # drop the last rule
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_evaluation_order_duplicate_entry_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["evaluation_order"] = list(RULE_NAMES) + [RULE_NAMES[0]]
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_evaluation_order_unknown_entry_rejected(tmp_path):
    bad = copy.deepcopy(VALID_RULES)
    bad["evaluation_order"] = list(RULE_NAMES[:-1]) + ["not_a_real_rule"]
    with pytest.raises(ValidationError):
        _write_and_load(tmp_path, bad)


def test_evaluation_order_reordered_is_still_valid(tmp_path):
    """A permutation in a DIFFERENT order is valid config -- rules.yaml, not
    code, is the source of truth for ordering.
    """
    reordered = list(reversed(RULE_NAMES))
    good = copy.deepcopy(VALID_RULES)
    good["evaluation_order"] = reordered
    config, _ = _write_and_load(tmp_path, good)
    assert config.evaluation_order == tuple(reordered)
