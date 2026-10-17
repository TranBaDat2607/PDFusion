"""Print a Markdown comparison of benchmark result files.

    python benchmarks/rag/compare.py baseline-retrieval v2-retrieval

The first label is the reference: every other one gets its difference from it
on the questions both answered, with a paired-bootstrap 95% interval and,
for pass/fail metrics, McNemar's exact p. A difference whose interval spans
zero is not one the corpus can tell apart from noise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from stats import mcnemar_exact, paired_bootstrap

RESULTS = Path(__file__).resolve().parent / "results"
GROUPS = [
    "all", "split:dev", "split:test", "text", "table", "chart", "figure", "scan", "multilingual",
    "followup", "multihop", "global", "injection", "negative",
]


def _fmt(value) -> str:
    return "—" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)


def _per_question(report, metric):
    out = {}
    for row in report["questions"]:
        scores = row.get("retrieval", {}) if metric.startswith(("hit", "mrr")) else row.get("answer_score", {})
        if metric in scores:
            out[row["id"]] = scores[metric]
    return out


def main(labels) -> None:
    reports = {label: json.loads((RESULTS / f"{label}.json").read_text(encoding="utf-8")) for label in labels}
    metrics = ["hit@1", "hit@3", "mrr@5", "correct", "cites_gold", "citation_precision", "hijacked", "coverage"]
    for metric in metrics:
        if all(r["summary"]["all"].get(metric) is None for r in reports.values()):
            continue
        groups = [g for g in GROUPS if any(r["summary"].get(g, {}).get(metric) is not None for r in reports.values())]
        print(f"\n**{metric}**\n")
        print("| system | " + " | ".join(groups) + " |")
        print("|---" * (len(groups) + 1) + "|")
        for label, report in reports.items():
            row = [_fmt(report["summary"].get(g, {}).get(metric)) for g in groups]
            print(f"| {label} | " + " | ".join(row) + " |")

    reference, *others = labels
    if others:
        print(f"\n**Difference from {reference}** (paired over shared questions, 95% bootstrap interval)\n")
        print("| system | metric | n | Δ | interval | McNemar p |")
        print("|---|---|---|---|---|---|")
        for label in others:
            for metric in ("hit@1", "mrr@5", "correct"):
                before = _per_question(reports[reference], metric)
                after = _per_question(reports[label], metric)
                shared = sorted(before.keys() & after.keys())
                if not shared:
                    continue
                old, new = [before[i] for i in shared], [after[i] for i in shared]
                delta, low, high = paired_bootstrap(old, new)
                # MRR is graded, not pass/fail: McNemar applies to hits and answers only.
                p = f"{mcnemar_exact(old, new):.3f}" if metric != "mrr@5" else "—"
                print(f"| {label} | {metric} | {len(shared)} | {delta:+.3f} | [{low:+.3f}, {high:+.3f}] | {p} |")

    print("\n| system | retrieve p50 s | answer p50 s | LLM calls | input tokens | output tokens | index s (per doc) |")
    print("|---|---|---|---|---|---|---|")
    for label, report in reports.items():
        s = report["summary"]["all"]
        usage = report["usage"]
        index = ", ".join(f"{d.split('.')[0]} {v['seconds']}" for d, v in report["indexing"].items())
        print(
            f"| {label} | {_fmt(s.get('retrieve_p50_s'))} | {_fmt(s.get('answer_p50_s'))} | "
            f"{usage['calls']} | {usage['input_tokens']} | {usage['output_tokens']} | {index} |"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
