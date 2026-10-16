"""Benchmark chat retrieval and answers over the corpus `build_corpus.py` makes.

    python benchmarks/rag/run.py --system baseline --src ../PDFusion-baseline/src
    python benchmarks/rag/run.py --system v2
    python benchmarks/rag/run.py --system v2 --answers          # also asks the LLM

Each system is imported from the `src` tree given (default: this checkout's),
so the baseline is measured from a worktree of the commit before the redesign
rather than from a copy of it. Results go to `benchmarks/rag/results/`.

Retrieval is scored on pages, the unit a citation names: a system's ranked
chunks become its ranked pages, first occurrence wins. Answers are scored by
the regexes in `questions.json` (all must match), by whether the pages they
cite include a gold page, and, for questions the document can't answer, by
whether they say so.

`--answers` spends API credits on the model the app is configured with
(`translation.model`, or `rag.answer_model`): every LLM call's tokens are
counted and reported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
DATA = HERE / ".data"
RESULTS = HERE / "results"
REPO = HERE.parent.parent

ABSTAIN = re.compile(
    r"(not|n't|no)\b.{0,60}\b(mention|contain|include|provide|say|state|specif|found|find|available|information)"
    r"|does not appear|isn't in|is not in|no information|cannot be determined|can't be determined"
    r"|không (có|đề cập|tìm thấy)",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# token accounting
# ---------------------------------------------------------------------------


class Usage:
    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, prompt: Optional[int], output: Optional[int]) -> None:
        self.calls += 1
        self.input_tokens += prompt or 0
        self.output_tokens += output or 0

    def snapshot(self) -> Dict[str, int]:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


USAGE = Usage()


def _count_gemini(translator: Any) -> None:
    """Wrap a Gemini translator's SDK call so its token counts reach USAGE."""
    models = translator.client.models
    if getattr(models, "_bench_wrapped", False):
        return
    original = models.generate_content

    def counted(*args, **kwargs):
        response = original(*args, **kwargs)
        meta = getattr(response, "usage_metadata", None)
        thoughts = getattr(meta, "thoughts_token_count", 0) or 0
        USAGE.add(
            getattr(meta, "prompt_token_count", 0),
            (getattr(meta, "candidates_token_count", 0) or 0) + thoughts,
        )
        return response

    models.generate_content = counted
    models._bench_wrapped = True


def _count_any(translator: Any) -> None:
    if translator is None:
        return
    client = getattr(translator, "client", None)
    if client is not None and hasattr(client, "models"):
        _count_gemini(translator)


# ---------------------------------------------------------------------------
# systems
# ---------------------------------------------------------------------------


def _import_src(src: Path) -> None:
    sys.path.insert(0, str(src))
    for name in list(sys.modules):
        if name.startswith("desktop_pdf_translator"):
            del sys.modules[name]


class BaselineSystem:
    """The chat pipeline as it was before the redesign: ScientificPDFProcessor
    chunks, ChromaDB + BM25 score blend, HyDE, heuristic re-rank."""

    name = "baseline"

    def __init__(self, workdir: Path, use_model: bool) -> None:
        from desktop_pdf_translator.rag.rag_chain import EnhancedRAGChain
        from desktop_pdf_translator.rag.vector_store import ChromaDBManager

        self.store = ChromaDBManager(persist_directory=workdir / "vectors")
        self.chain = EnhancedRAGChain(self.store)
        self.use_model = use_model
        self.model = self.chain._answer_model() if use_model else None
        if self.model is not None:
            _count_any(self.model.translator)

    async def index(self, pdf: Path, index_id: str) -> Dict[str, Any]:
        from desktop_pdf_translator.rag.document_processor import ScientificPDFProcessor

        processor = ScientificPDFProcessor()
        chunks = await asyncio.to_thread(processor.process_pdf, pdf)
        await self.store.add_chunks(index_id, chunks)
        return {"chunks": len(chunks)}

    async def retrieve(self, index_id: str, pdf: Path, question: str, k: int) -> List[int]:
        sources = await self.chain._retrieve_pdf_knowledge(question, index_id, k, self.model)
        return [s["metadata"].get("page", -1) + 1 for s in sources]

    async def answer(self, index_id: str, pdf: Path, question: str) -> Dict[str, Any]:
        self.chain._answer_model = lambda: self.model  # the one model, for every question
        result = await self.chain.answer_question(
            question=question, index_id=index_id, document_id=index_id,
            document_path=str(pdf), answer_lang="en",
        )
        pages = [r["page"] for r in result["pdf_references"] if r.get("page")]
        # The baseline cites nothing; every reference it lists is what it offers
        # the reader as the answer's source.
        return {"text": result["answer"], "cited_pages": pages}


class V2System:
    """The redesigned pipeline: `rag/ingest.py`, `rag/retrieval.py`,
    `rag/rag_chain.py` as they are in the given src tree."""

    name = "v2"

    def __init__(self, workdir: Path, use_model: bool, options: Dict[str, Any]) -> None:
        from desktop_pdf_translator.rag.rag_chain import EnhancedRAGChain
        from desktop_pdf_translator.rag.vector_store import ChromaDBManager

        self.store = ChromaDBManager(persist_directory=workdir / "vectors")
        if options.pop("rerank", False):
            from cross_encoder import OnnxCrossEncoder

            options["reranker"] = OnnxCrossEncoder()
        self.chain = EnhancedRAGChain(self.store, **options)
        self.model = self.chain._answer_model() if use_model else None
        if self.model is not None:
            _count_any(self.model.translator)

    async def index(self, pdf: Path, index_id: str) -> Dict[str, Any]:
        from desktop_pdf_translator.rag.ingest import PdfIngestor
        from desktop_pdf_translator.rag.ocr import load_ocr_engine

        # As the app's index route builds it (`api/routes/rag.py`).
        ingestor = PdfIngestor(ocr=load_ocr_engine())
        chunks = await asyncio.to_thread(ingestor.process_pdf, pdf)
        await self.store.add_chunks(index_id, chunks)
        return {"chunks": len(chunks), **ingestor.stats()}

    async def retrieve(self, index_id: str, pdf: Path, question: str, k: int) -> List[int]:
        evidence = await self.chain.retrieve(question, index_id, k, self.model)
        return [e.page + 1 for e in evidence]

    async def answer(self, index_id: str, pdf: Path, question: str) -> Dict[str, Any]:
        self.chain._answer_model = lambda: self.model
        result = await self.chain.answer_question(
            question=question, index_id=index_id, document_id=index_id,
            document_path=str(pdf), answer_lang="en",
        )
        cited = [r["page"] for r in result["pdf_references"] if r.get("cited") and r.get("page")]
        return {
            "text": result["answer"],
            "cited_pages": cited,
            "images_sent": result.get("quality_metrics", {}).get("images_sent", 0),
        }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _unique(pages: List[int]) -> List[int]:
    return list(dict.fromkeys(p for p in pages if p and p > 0))


def score_retrieval(ranked: List[int], gold: List[int]) -> Dict[str, float]:
    ranked = _unique(ranked)
    hit_rank = next((i + 1 for i, p in enumerate(ranked) if p in gold), None)
    return {
        "hit@1": float(hit_rank == 1),
        "hit@3": float(hit_rank is not None and hit_rank <= 3),
        "hit@5": float(hit_rank is not None and hit_rank <= 5),
        "mrr@5": 1.0 / hit_rank if hit_rank and hit_rank <= 5 else 0.0,
    }


# Citations, in every system's format, so a page number can't pass for an
# answer: `8` would otherwise match "[p. 8](#page=8)".
_CITATION = re.compile(
    r"\[p\. \d+\]\(#page=\d+\)"              # v2's checked links
    r"|\[[^\]]*(?:page|pages|p\.|pp\.)\s*\d[^\]]*\]"  # [Document 1, Page 8], [p. 8]
    r"|\(\s*(?:page|p\.)\s*\d+[^)]*\)",           # (page 8)
    re.IGNORECASE,
)


def strip_citations(text: str) -> str:
    return _CITATION.sub(" ", text)


def score_answer(q: Dict[str, Any], answer: Dict[str, Any]) -> Dict[str, float]:
    text = strip_citations(answer["text"] or "")
    if q["kind"] == "negative":
        abstained = bool(ABSTAIN.search(text))
        return {"correct": float(abstained), "abstained": float(abstained)}
    correct = all(re.search(p, text, re.IGNORECASE) for p in q["answer"])
    cited = _unique(answer.get("cited_pages") or [])
    gold = set(q["pages"])
    return {
        "correct": float(correct),
        "cites_gold": float(bool(gold & set(cited))),
        "citation_precision": (len(gold & set(cited)) / len(cited)) if cited else 0.0,
        "pages_cited": float(len(cited)),
    }


def _mean(rows: List[Dict[str, float]], key: str) -> Optional[float]:
    values = [r[key] for r in rows if key in r]
    return round(statistics.mean(values), 3) if values else None


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    groups: Dict[str, List[Dict[str, Any]]] = {"all": rows}
    for row in rows:
        groups.setdefault(row["kind"], []).append(row)
    for group, items in groups.items():
        retrieval = [r["retrieval"] for r in items if "retrieval" in r]
        answers = [r["answer_score"] for r in items if "answer_score" in r]
        entry: Dict[str, Any] = {"n": len(items)}
        for key in ("hit@1", "hit@3", "hit@5", "mrr@5"):
            entry[key] = _mean(retrieval, key)
        for key in ("correct", "cites_gold", "citation_precision", "pages_cited"):
            entry[key] = _mean(answers, key)
        latencies = [r["retrieve_seconds"] for r in items if "retrieve_seconds" in r]
        if latencies:
            entry["retrieve_p50_s"] = round(statistics.median(latencies), 3)
        answer_latencies = [r["answer_seconds"] for r in items if "answer_seconds" in r]
        if answer_latencies:
            entry["answer_p50_s"] = round(statistics.median(answer_latencies), 3)
        out[group] = entry
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _model_settings():
    from desktop_pdf_translator.config import get_settings

    settings = get_settings()
    ref = settings.rag.answer_model or settings.translation.model
    return settings, ref


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]
    if args.docs:
        questions = [q for q in questions if q["doc"] in args.docs]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    retrieval_only_kinds = {"negative"}
    workdir = Path(tempfile.mkdtemp(prefix="pdfusion-bench-"))

    _import_src(Path(args.src).resolve())
    _, ref = _model_settings()
    if args.system == "baseline":
        system = BaselineSystem(workdir, use_model=args.answers or args.hyde)
    else:
        options = json.loads(args.options) if args.options else {}
        system = V2System(workdir, use_model=args.answers or args.query_model, options=options)

    report: Dict[str, Any] = {
        "system": system.name, "src": str(args.src), "answers": args.answers,
        "model": f"{ref.provider.value}:{ref.model}" if args.answers else None,
        "options": args.options, "indexing": {}, "questions": [],
    }

    indexes: Dict[str, str] = {}
    for doc in sorted({q["doc"] for q in questions}):
        index_id = re.sub(r"\W", "_", doc)
        before = USAGE.snapshot()
        started = time.perf_counter()
        try:
            stats = await system.index(DATA / doc, index_id)
        except Exception as exc:  # noqa: BLE001 — the app would refuse this document
            stats = {"error": str(exc)}
        elapsed = time.perf_counter() - started
        after = USAGE.snapshot()
        report["indexing"][doc] = {
            "seconds": round(elapsed, 2), **stats,
            "llm_input_tokens": after["input_tokens"] - before["input_tokens"],
            "llm_output_tokens": after["output_tokens"] - before["output_tokens"],
        }
        indexes[doc] = index_id
        print(f"indexed {doc} in {elapsed:.1f}s {stats}", flush=True)

    for q in questions:
        row: Dict[str, Any] = {"id": q["id"], "doc": q["doc"], "kind": q["kind"], "q": q["q"]}
        pdf = DATA / q["doc"]
        before = USAGE.snapshot()
        if q["kind"] not in retrieval_only_kinds:
            started = time.perf_counter()
            try:
                ranked = await system.retrieve(indexes[q["doc"]], pdf, q["q"], 5)
            except Exception as exc:  # noqa: BLE001 — e.g. an index with no chunks
                print(f"  retrieval failed: {exc}", flush=True)
                ranked = []
            row["retrieve_seconds"] = round(time.perf_counter() - started, 3)
            row["ranked_pages"] = _unique(ranked)
            row["retrieval"] = score_retrieval(ranked, q["pages"])
        if args.answers:
            started = time.perf_counter()
            try:
                answer = await system.answer(indexes[q["doc"]], pdf, q["q"])
            except Exception as exc:  # noqa: BLE001 — a failed answer is a wrong answer
                answer = {"text": f"<error: {exc}>", "cited_pages": []}
            row["answer_seconds"] = round(time.perf_counter() - started, 3)
            row["answer"] = answer
            row["answer_score"] = score_answer(q, answer)
        after = USAGE.snapshot()
        row["llm_input_tokens"] = after["input_tokens"] - before["input_tokens"]
        row["llm_output_tokens"] = after["output_tokens"] - before["output_tokens"]
        row["llm_calls"] = after["calls"] - before["calls"]
        report["questions"].append(row)
        mark = row.get("retrieval", {}).get("hit@1")
        ok = row.get("answer_score", {}).get("correct")
        print(f"{q['id']:4} {q['kind']:12} hit@1={mark} correct={ok} pages={row.get('ranked_pages')}", flush=True)

    report["summary"] = summarize(report["questions"])
    report["usage"] = USAGE.snapshot()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", choices=["baseline", "v2"], required=True)
    parser.add_argument("--src", default=str(REPO / "src"))
    parser.add_argument("--answers", action="store_true", help="ask the configured LLM too")
    parser.add_argument("--hyde", action="store_true", help="baseline: retrieve with HyDE (uses the LLM)")
    parser.add_argument("--query-model", action="store_true", help="v2: let retrieval use the LLM")
    parser.add_argument("--options", help="v2: JSON of EnhancedRAGChain keyword options")
    parser.add_argument("--docs", nargs="*")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    report = asyncio.run(run(args))
    RESULTS.mkdir(parents=True, exist_ok=True)
    label = args.label or args.system + ("-answers" if args.answers else "")
    out = RESULTS / f"{label}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=1))
    print("usage", report["usage"], "->", out)


if __name__ == "__main__":
    main()
