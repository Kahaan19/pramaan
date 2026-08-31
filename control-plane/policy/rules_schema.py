"""Pydantic model of policies/rules.yaml. extra="forbid" + StrictInt/StrictBool
everywhere are load-bearing: a typo'd key must fail loudly at startup rather
than silently disabling a control, and a float slipping into a money/count
field must be rejected rather than entering policy math. The app refuses to
boot on an invalid policy file.
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator

from policy.rule_names import RULE_NAMES

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "policies" / "rules.yaml"


def _positive(value: int, field_name: str) -> int:
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


class VelocityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_seconds: StrictInt
    max_txns: StrictInt
    max_total_paise: StrictInt

    @field_validator("window_seconds", "max_txns", "max_total_paise")
    @classmethod
    def _check_positive(cls, v: int, info) -> int:
        return _positive(v, info.field_name)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    per_transaction_cap_paise: StrictInt
    velocity: VelocityConfig
    max_pending_step_ups: StrictInt

    @field_validator("per_transaction_cap_paise", "max_pending_step_ups")
    @classmethod
    def _check_positive(cls, v: int, info) -> int:
        return _positive(v, info.field_name)


class StepUpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_threshold_paise: StrictInt
    step_up_on_delegated_intent: StrictBool

    @field_validator("amount_threshold_paise")
    @classmethod
    def _check_positive(cls, v: int) -> int:
        return _positive(v, "amount_threshold_paise")


class MandateConfig(BaseModel):
    """NOT actually configurable: expiry and replay rejection are always
    enforced in mandates/scope.py and mandates/nonce.py regardless of this
    file. Modelled only so an operator sees the invariant stated, and so
    setting either to false -- which would be a lie -- fails loudly at load
    time instead of reading as a live, honored control.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    reject_if_expired: Literal[True]
    reject_replayed_nonce: Literal[True]


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt
    limits: LimitsConfig
    merchant_allowlist: tuple[str, ...]
    allowed_categories: tuple[str, ...]
    step_up: StepUpConfig
    mandate: MandateConfig
    # The evaluation order documented as a YAML comment is promoted to real,
    # validated data: the single source of truth the engine iterates over,
    # so the policy file and the code cannot silently drift apart.
    evaluation_order: tuple[str, ...]

    @field_validator("version")
    @classmethod
    def _version_supported(cls, v: int) -> int:
        if v != 1:
            raise ValueError("only rules.yaml version 1 is supported")
        return v

    @field_validator("merchant_allowlist")
    @classmethod
    def _merchant_allowlist_nonempty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("merchant_allowlist must not be empty")
        return v

    @field_validator("allowed_categories")
    @classmethod
    def _allowed_categories_nonempty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("allowed_categories must not be empty")
        return v

    @field_validator("evaluation_order")
    @classmethod
    def _evaluation_order_is_exact_permutation(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        expected = set(RULE_NAMES)
        got = set(v)
        problems = []
        if missing := expected - got:
            problems.append(f"missing: {sorted(missing)}")
        if unknown := got - expected:
            problems.append(f"unknown: {sorted(unknown)}")
        if duplicates := {name for name in v if v.count(name) > 1}:
            problems.append(f"duplicated: {sorted(duplicates)}")
        if problems:
            raise ValueError(
                f"evaluation_order must be an exact permutation of {RULE_NAMES} ({'; '.join(problems)})"
            )
        return v

    @property
    def merchant_allowlist_set(self) -> frozenset[str]:
        return frozenset(self.merchant_allowlist)

    @property
    def allowed_categories_set(self) -> frozenset[str]:
        return frozenset(self.allowed_categories)


def load_rules_config(path: Path | str = DEFAULT_RULES_PATH) -> tuple[RulesConfig, str]:
    """Loads + validates rules.yaml. Returns (config, sha256_hex_of_file_bytes)
    so a Verdict can name exactly which policy-file version produced it.
    """
    raw_bytes = Path(path).read_bytes()
    data = yaml.safe_load(raw_bytes)
    config = RulesConfig.model_validate(data)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return config, digest


@lru_cache
def get_rules_config(path: str = str(DEFAULT_RULES_PATH)) -> tuple[RulesConfig, str]:
    return load_rules_config(path)
