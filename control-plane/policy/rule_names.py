"""The canonical set of rule names, shared by rules_schema.py (to validate
rules.yaml's evaluation_order) and rules.py (to build RULE_REGISTRY),
without those two modules importing each other.
"""

RULE_NAMES: tuple[str, ...] = (
    "mandate_expiry",
    "per_transaction_cap",
    "velocity_txn_count",
    "velocity_total_amount",
    "max_pending_step_ups",
    "merchant_allowlist",
    "category",
    "step_up_amount_threshold",
    "step_up_delegated_intent",
)
