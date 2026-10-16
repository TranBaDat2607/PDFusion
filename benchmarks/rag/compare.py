"""Print a Markdown comparison of benchmark result files.

    python benchmarks/rag/compare.py baseline-retrieval v2-retrieval
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
GROUPS = ["all", "text", "table", "chart", "figure", "scan", "multilingual", "negative"]


def _fmt(value) -> str:
    return "—" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)


def main(labels) -> None:
    reports = {label: json.loads((RESULTS / f"{label}.json").read_text(encoding="utf-8")) for label in labels}
    metrics = ["hit@1", "hit@3", "mrr@5", "correct", "cites_gold", "citation_precision"]
    for metric in metrics:
        if all(r["summary"]["all"].get(metric) is None for r in reports.values()):
            continue
        print(f"\n**{metric}**\n")
        print("| system | " + " | ".join(GROUPS) + " |")
        print("|---" * (len(GROUPS) + 1) + "|")
        for label, report in reports.items():
            row = [_fmt(report["summary"].get(g, {}).get(metric)) for g in GROUPS]
            print(f"| {label} | " + " | ".join(row) + " |")
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
