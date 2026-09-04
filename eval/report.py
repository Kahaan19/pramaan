"""Three report artifacts from one run: a console table, eval/reports/latest.json
(full structured report), eval/reports/latest.md (pasteable into the README's
"Metrics (honest)" section), and eval/reports/attempts.csv (one row per
attempt, so a judge can recompute every metric independently). All three are
committed to git -- the numbers are reviewable without running anything.
"""

import csv
import json
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path

from eval.harness import AttemptResult
from eval.metrics import Metrics
from ledger.verify import ChainVerification


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _rupees(paise: int) -> str:
    r, p = divmod(paise, 100)
    return f"Rs {r:,}.{p:02d}"


def print_console_table(results: list[AttemptResult], metrics: Metrics, chain: ChainVerification) -> None:
    print()
    print("=" * 78)
    print("PRAMAAN EVAL BATCH")
    print("=" * 78)
    print(f"{'id':<10} {'group':<28} {'label':<16} {'expected':<10} {'actual':<10} {'outcome'}")
    print("-" * 78)
    for r in results:
        a = r.attempt
        actual = r.actual_decision or r.mandate_error_code or "REPLAY"
        print(f"{a.attempt_id:<10} {a.group:<28} {a.label:<16} {a.expected_decision:<10} {actual:<10} {r.outcome}")
    print("-" * 78)
    print(f"Total attempts:        {metrics.total_attempts}")
    print(f"  legitimate:          {metrics.legitimate_count}")
    print(f"  malicious:           {metrics.malicious_count}")
    print(f"  indistinguishable:   {metrics.indistinguishable_count}")
    print()
    print(f"Attack block rate:     {_pct(metrics.attack_block_rate)}  ({metrics.malicious_count - metrics.false_negatives}/{metrics.malicious_count} malicious attempts blocked)")
    print(f"False negatives:       {metrics.false_negatives}  {metrics.false_negative_ids or ''}")
    print(f"False positives:       {metrics.false_positives}  {metrics.false_positive_ids or ''}")
    print(f"False-positive rate:   {_pct(metrics.false_positive_rate)}  (of {metrics.legitimate_count} legitimate attempts)")
    print(f"Escalation rate:       {_pct(metrics.escalation_rate)}  (legitimate attempts sent to a human, neither allowed nor blocked)")
    print()
    print(f"Money moved:           {_rupees(metrics.money_moved_paise)}  (executed, deduped by cart_id)")
    print(f"Money blocked:         {_rupees(metrics.money_blocked_paise)}  (attacker-chosen amounts -- not a savings claim, see report)")
    print()
    print(f"Audit coverage:        {_pct(metrics.audit_coverage)}  ({metrics.total_attempts} attempts, every one with >=1 ledger row)")
    print(f"Ledger chain verified: {'OK' if chain.ok else 'BROKEN'}  (row_count={chain.row_count}, head_seq={chain.head_seq})")
    if metrics.control_drift:
        print(f"Control drift:         {metrics.control_drift} attempt(s) blocked by a different layer than predicted: {metrics.control_drift_ids}")
    print("=" * 78)
    verdict = "PASS" if metrics.false_negatives == 0 and chain.ok else "FAIL"
    print(f"RESULT: {verdict}  (pass = zero false negatives AND an intact ledger chain)")
    print("=" * 78)
    print()


def _group_summary(results: list[AttemptResult]) -> list[dict]:
    rows = []
    keyed = sorted(results, key=lambda r: r.attempt.group)
    for group, members in groupby(keyed, key=lambda r: r.attempt.group):
        members = list(members)
        label = members[0].attempt.label
        matched = sum(
            1
            for r in members
            if r.actual_decision == r.attempt.expected_decision
            or (r.attempt.expected_decision == "REPLAY" and r.actual_control == "idempotency")
            or (r.attempt.expected_decision == "MANDATE_ERROR" and r.mandate_error_code == r.attempt.expected_mandate_error_code)
        )
        rows.append({"group": group, "label": label, "count": len(members), "matched_expected": matched})
    return rows


def _report_dict(results: list[AttemptResult], metrics: Metrics, chain: ChainVerification, rules_sha256: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules_sha256": rules_sha256,
        "chain": {
            "ok": chain.ok,
            "row_count": chain.row_count,
            "head_seq": chain.head_seq,
            "head_hash": chain.head_hash,
            "findings": [{"kind": f.kind, "seq": f.seq, "detail": f.detail} for f in chain.findings],
        },
        "metrics": {
            "total_attempts": metrics.total_attempts,
            "legitimate_count": metrics.legitimate_count,
            "malicious_count": metrics.malicious_count,
            "indistinguishable_count": metrics.indistinguishable_count,
            "executed_count": metrics.executed_count,
            "blocked_count": metrics.blocked_count,
            "escalated_count": metrics.escalated_count,
            "attack_block_rate": metrics.attack_block_rate,
            "false_negatives": metrics.false_negatives,
            "false_negative_ids": list(metrics.false_negative_ids),
            "false_positives": metrics.false_positives,
            "false_positive_ids": list(metrics.false_positive_ids),
            "false_positive_rate": metrics.false_positive_rate,
            "escalation_rate": metrics.escalation_rate,
            "money_moved_paise": metrics.money_moved_paise,
            "money_blocked_paise": metrics.money_blocked_paise,
            "audit_coverage": metrics.audit_coverage,
            "control_drift": metrics.control_drift,
            "control_drift_ids": list(metrics.control_drift_ids),
        },
        "groups": _group_summary(results),
        "notes": [
            "money_blocked_paise is attacker-chosen and is NOT a savings claim -- it is reported "
            "alongside the count of attacks blocked, which is the meaningful number.",
            "'money moved' means an authorized Razorpay payment link was created under a passing "
            "verdict, not a settled payment -- the executor never observes a completed payment "
            "(see README's honest scoping note).",
            "INDISTINGUISHABLE attempts (the runaway loop's opening moves) are excluded from the "
            "false-negative count: they violate no stated bound, so scoring them as missed attacks "
            "would misrepresent a rate limiter as a preventer of first spend.",
        ],
    }


def write_reports(
    results: list[AttemptResult],
    metrics: Metrics,
    chain: ChainVerification,
    rules_sha256: str,
    reports_dir: Path,
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = _report_dict(results, metrics, chain, rules_sha256)

    (reports_dir / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    (reports_dir / "latest.md").write_text(_render_markdown(report))

    with (reports_dir / "attempts.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "attempt_id",
                "group",
                "label",
                "expected_decision",
                "expected_control",
                "expected_rule_fired",
                "expected_mandate_error_code",
                "actual_decision",
                "actual_control",
                "rule_fired",
                "mandate_error_code",
                "amount_paise",
                "outcome",
                "transaction_id",
                "cart_id",
            ]
        )
        for r in results:
            a = r.attempt
            writer.writerow(
                [
                    a.attempt_id,
                    a.group,
                    a.label,
                    a.expected_decision,
                    a.expected_control,
                    a.expected_rule_fired,
                    a.expected_mandate_error_code,
                    r.actual_decision,
                    r.actual_control,
                    r.rule_fired,
                    r.mandate_error_code,
                    a.cart.total_paise,
                    r.outcome,
                    r.transaction_id,
                    a.cart.cart_id,
                ]
            )


def _render_markdown(report: dict) -> str:
    m = report["metrics"]
    c = report["chain"]
    lines = [
        "### Eval batch results",
        "",
        f"_Generated {report['generated_at']} against rules_sha256 `{report['rules_sha256'][:12]}...`_",
        "",
        f"- **{m['total_attempts']} synthetic buyer attempts**: {m['legitimate_count']} legitimate, "
        f"{m['malicious_count']} malicious, {m['indistinguishable_count']} indistinguishable "
        "(a runaway loop's opening moves, individually within every stated bound).",
        f"- **Attack block rate: {_pct(m['attack_block_rate'])}** "
        f"({m['malicious_count'] - m['false_negatives']}/{m['malicious_count']} malicious attempts blocked, "
        f"{m['false_negatives']} false negatives).",
        f"- **False-positive rate: {_pct(m['false_positive_rate'])}** "
        f"({m['false_positives']}/{m['legitimate_count']} legitimate attempts wrongly blocked -- "
        "the honest cost of the velocity and approval-queue caps).",
        f"- **Escalation rate: {_pct(m['escalation_rate'])}** of legitimate attempts required a human "
        "(neither allowed nor blocked).",
        f"- **Money moved:** {_rupees(m['money_moved_paise'])} (executed, deduped by cart). "
        f"**Money blocked:** {_rupees(m['money_blocked_paise'])} (attacker-chosen amounts, not a savings claim).",
        f"- **Audit coverage: {_pct(m['audit_coverage'])}** -- every attempt has at least one ledger row.",
        f"- **Ledger chain:** {'verified intact' if c['ok'] else 'BROKEN -- see findings'} "
        f"({c['row_count']} rows, head seq {c['head_seq']}).",
        "",
        "| Group | Label | Count | Matched expected |",
        "|---|---|---|---|",
    ]
    for g in report["groups"]:
        lines.append(f"| {g['group']} | {g['label']} | {g['count']} | {g['matched_expected']}/{g['count']} |")
    lines.append("")
    lines.append("Full per-attempt data: `eval/reports/attempts.csv`. Raw report: `eval/reports/latest.json`.")
    return "\n".join(lines) + "\n"
