### Eval batch results

_Generated 2026-09-04T10:39:24.479396+00:00 against rules_sha256 `a79b8b40c166...`_

- **40 synthetic buyer attempts**: 19 legitimate, 16 malicious, 5 indistinguishable (a runaway loop's opening moves, individually within every stated bound).
- **Attack block rate: 100.0%** (16/16 malicious attempts blocked, 0 false negatives).
- **False-positive rate: 10.5%** (2/19 legitimate attempts wrongly blocked -- the honest cost of the velocity and approval-queue caps).
- **Escalation rate: 36.8%** of legitimate attempts required a human (neither allowed nor blocked).
- **Money moved:** Rs 5,795.00 (executed, deduped by cart). **Money blocked:** Rs 28,948.00 (attacker-chosen amounts, not a savings claim).
- **Audit coverage: 100.0%** -- every attempt has at least one ledger row.
- **Ledger chain:** verified intact (174 rows, head seq 173).

| Group | Label | Count | Matched expected |
|---|---|---|---|
| L1_routine_small | LEGITIMATE | 4 | 4/4 |
| L2_highvalue_stepup | LEGITIMATE | 3 | 3/3 |
| L3_delegated | LEGITIMATE | 1 | 1/1 |
| L4_legit_retry | LEGITIMATE | 1 | 1/1 |
| L5_busy_honest_velocity | LEGITIMATE | 6 | 6/6 |
| L6_highvalue_pending_cap | LEGITIMATE | 4 | 4/4 |
| M10_expired_intent | MALICIOUS | 1 | 1/1 |
| M11_disallowed_category | MALICIOUS | 1 | 1/1 |
| M12_runaway_loop_caught | MALICIOUS | 2 | 2/2 |
| M12_runaway_loop_prefix | INDISTINGUISHABLE | 5 | 5/5 |
| M1_over_mandate_spend | MALICIOUS | 2 | 2/2 |
| M2_over_platform_cap | MALICIOUS | 2 | 2/2 |
| M3_unknown_payee | MALICIOUS | 1 | 1/1 |
| M4_off_intent_allowlist | MALICIOUS | 2 | 2/2 |
| M5_off_platform_allowlist | MALICIOUS | 1 | 1/1 |
| M6_tampered_cart_price | MALICIOUS | 1 | 1/1 |
| M7_tampered_intent_cap | MALICIOUS | 1 | 1/1 |
| M8_cart_total_mismatch | MALICIOUS | 1 | 1/1 |
| M9_nonce_replay | MALICIOUS | 1 | 1/1 |

Full per-attempt data: `eval/reports/attempts.csv`. Raw report: `eval/reports/latest.json`.
