"""Score stored answers again after a change to `run.score_answer`.

    python benchmarks/rag/rescore.py baseline-answers v2-answers

Re-reads `results/<label>.json`, recomputes each question's retrieval and answer scores
and the summary from the answers already there, and writes the file back. No
system is run and no model is asked.
"""

from __future__ import annotations

import json
import sys

from run import HERE, RESULTS, score_answer, score_retrieval, summarize


def main(labels) -> None:
    questions = {
        q["id"]: q
        for q in json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]
    }
    for label in labels:
        path = RESULTS / f"{label}.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in report["questions"]:
            q = questions[row["id"]]
            if "retrieval" in row:
                row["retrieval"] = score_retrieval(row["ranked_pages"], q["pages"])
            if "answer" in row:
                row["answer_score"] = score_answer(q, row["answer"])
        report["summary"] = summarize(report["questions"])
        path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print(label, report["summary"]["all"]["correct"])


if __name__ == "__main__":
    main(sys.argv[1:])
