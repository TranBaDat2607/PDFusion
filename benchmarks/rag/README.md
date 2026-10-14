# Chat RAG benchmark

How PDFusion's chat (`src/desktop_pdf_translator/rag/`) was redesigned, what it
was compared against, and the numbers behind every choice. Re-run it after
touching `rag/`: the unit tests check behaviour, this checks quality.

```bash
conda activate pdfusion-env
python benchmarks/rag/build_corpus.py                 # once; ~2 MB of public papers downloaded
python benchmarks/rag/run.py --system v2              # retrieval only: local, free
python benchmarks/rag/run.py --system v2 --answers    # + answers from the configured model
git worktree add ../PDFusion-baseline <commit before the redesign, e.g. 453229d>
python benchmarks/rag/run.py --system baseline --src ../PDFusion-baseline/src --answers
git clone https://github.com/qnguyen3/docpixie <dir>
python benchmarks/rag/run.py --system docpixie --docpixie <dir> --answers
python benchmarks/rag/compare.py baseline-answers v2-answers docpixie-answers
python benchmarks/rag/rescore.py <labels…>            # after changing the scoring
```

`--system baseline` needs a tree from before the redesign (it imports the
deleted `rag/document_processor.py`), hence the worktree.

`--answers` spends credits on the model the app is configured with
(`rag.answer_model`, else `translation.model`); every call's tokens are
counted. The runs below used **Gemini 2.5 Flash-Lite**, the configured model,
and cost a few US cents in total.

## The corpus

| document | pages | what it tests |
|---|---|---|
| `report.pdf` | 14 | Synthetic annual report, ground truth by construction: one fact per prose page, two ruled tables, a bar chart and a pie chart whose numbers exist **only as pixels**, two **scanned** pages (a memo, a table), a **Vietnamese** page, and distractor pages sharing the question's words but not its answer |
| `attention.pdf` | 15 | "Attention Is All You Need" (arXiv 1706.03762v7): real tables, vector figures, equations |
| `attention_scanned.pdf` | 15 | The same paper rasterized at 120 dpi, no text layer |
| `long.pdf` | 57 | The paper, the report, BERT and ResNet: how cost grows with length |

`questions.json`: 43 questions (+6 on `long.pdf`) tagged `text`, `table`,
`chart`, `figure`, `scan`, `multilingual` (incl. Vietnamese questions) and
`negative` (not answerable from the document — the right answer says so).
Gold pages are every page that states the answer, checked against the text.

**Scoring.** Retrieval: the system's ranked chunks become ranked pages (first
occurrence wins) — hit@1, hit@3, MRR@5 against the gold pages. Answers: all of
a question's regexes must match the answer with its citations stripped (so a
page number can't pass for a value); a negative question is correct when the
answer says the document doesn't contain it. `cites_gold`: the answer cites a
gold page; `citation_precision`: the share of cited pages that are gold. For
the baseline, which cites nothing, the references it lists are its citations.

## Systems

- **baseline** — PDFusion at `453229d`: `ScientificPDFProcessor` chunks
  (~1000 chars, Camelot/pdfplumber tables), ChromaDB + BM25 blended by score,
  HyDE (one extra LLM call before retrieval), a heuristic re-rank, a text-only
  answer with no citations.
- **DocPixie** — the real library ([qnguyen3/docpixie](https://github.com/qnguyen3/docpixie)),
  unmodified, pointed at Gemini's OpenAI-compatible endpoint. Vision-only: no
  index; per question it classifies the query, plans tasks, sends **every page
  image** to the model to pick pages, analyses the picked pages, and
  synthesises.
- **v2** — this redesign (see `docs/architecture-notes.md` → "Chat answers").

## Results

### Answers (Gemini 2.5 Flash-Lite, 43 questions)

v2 is the branch's final code (`results/v2-final-answers.json`); a run on the code
before the review fixes scored the same. The ablation rows are from that earlier code.

| | correct | text | table | chart | figure | scan | multilingual | negative | cites a gold page | citation precision |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.47 | 0.82 | 0.44 | 0.00 | 0.50 | 0.00 | 1.00 | 0.75 | 0.69 | 0.20 |
| DocPixie | 0.70 | 0.73 | 0.67 | 1.00 | 0.50 | 0.55 | 1.00 | 0.75 | 0.87 | 0.75 |
| **v2** | **0.93** | 1.00 | 1.00 | 1.00 | 0.50 | 0.82 | 1.00 | 1.00 | **0.97** | **0.97** |
| v2, no page images | 0.86 | 1.00 | 0.89 | 0.00 | 0.50 | 0.91 | 1.00 | 1.00 | 0.90 | 0.88 |
| v2 + HyDE | 0.93 | 1.00 | 0.89 | 1.00 | 0.50 | 0.91 | 1.00 | 1.00 | 1.00 | 0.99 |

### Retrieval (39 answerable questions, no LLM)

| | hit@1 | hit@3 | MRR@5 | scan hit@1 | table hit@1 |
|---|---|---|---|---|---|
| baseline | 0.62 | 0.69 | 0.65 | 0.00 | 0.67 |
| DocPixie (vision page selection, LLM) | 0.74 | 0.87 | 0.81 | 0.64 | 0.56 |
| **v2** | **0.85** | 0.95 | **0.90** | 0.91 | 0.67 |
| v2 + cross-encoder replacing the order | 0.82 | 0.97 | 0.89 | 1.00 | 0.44 |
| v2 + cross-encoder fused | 0.85 | 0.95 | 0.90 | 0.91 | 0.67 |
| v2 + HyDE (one more LLM call) | 0.80 | 0.92 | 0.88 | 0.82 | 0.67 |

### Cost and latency per question (i5-12400F, no GPU)

| | LLM calls | input tokens | output tokens | p50 total s | p90 total s |
|---|---|---|---|---|---|
| baseline | 2.4 | 1,003 | 149 | 2.62 | 2.94 |
| DocPixie | 5.5 | 7,834 | 226 | 6.63 | 11.82 |
| **v2** | **1.0** | 2,925 | 25 | **2.18** | 2.78 |
| v2, no page images | 1.0 | 2,225 | 30 | 0.76 | 0.93 |
| v2 + HyDE | 2.9* | 3,130 | 184 | 4.26 | 5.06 |

\* Includes the provider's 429 retries, which `_call_with_backoff` counts as calls.

### How cost grows with length (`long.pdf`, 57 pages, 6 questions)

| | correct | citation precision | input tokens / question | p50 s / question | LLM tokens to index |
|---|---|---|---|---|---|
| **v2** | **6/6** | 1.00 | **2,366** (15 pages: 2,925) | **2.2** | 0 (25 s local, OCR included) |
| DocPixie | 4/6 | 0.58 | 18,026 (15 pages: 7,834) | 9.6 | 14,795 |

v2's per-question cost does not depend on the document's length — retrieval
picks the pages, and at most three are sent as images. DocPixie's grows with
every page: on the long document it twice answered that the analysed pages
held no information about the question (the dividend memo, the revenue
table), having picked pages from the wrong part of the document. Six questions
is a small sample; the token figures are what it is for.

Indexing, once per document: v2 takes 6 s for the 15-page paper (baseline 17 s,
Camelot retrying without Ghostscript) and 4 s for the report; the scanned paper
takes ~110 s, nearly all of it RapidOCR at ~7 s a dense page. The baseline
"indexes" the scanned paper in 1.6 s because it finds no text and refuses it —
in the app, "No text could be extracted from this PDF". DocPixie's indexing is
an LLM call: ~3.8k tokens per document to summarise it from its page images.

## What each design decision rests on

- **Retrieve by text, read by vision** (vs. DocPixie's read-everything-by-vision).
  DocPixie's page selection is good where text retrieval is weakest (charts:
  1.00), but every question pays for every page — 7.8k tokens a question at 15
  pages — and its answers came out less accurate (0.70), partly because its
  analysis prompt labels page images by position ("[Page 1 from document]" is
  the first *selected* page), so its citations can name the wrong page. v2 sends
  at most three page images, chosen by retrieval, labelled with the real page
  number: charts reach 1.00 too, at a third of the tokens and of the latency.
- **Page images at answer time.** Without them v2 answers no chart question
  (0.00 → 1.00 with them) and loses a table; with them it is 1.3 s slower. They
  are rendered when asked, not stored.
- **Local OCR** (RapidOCR, already installed with BabelDOC) for scanned pages:
  scan retrieval 0.00 → 0.91 at hit@1. Its spaces are restored from the
  character boxes (word F1 0.33 → 0.64 against the paper's own text).
- **Reciprocal rank fusion, page grouping, heading-contextual chunks sized for
  the embedding model** — the retrieval gain from 0.62 to 0.85 hit@1 with no
  model call at all.
- **No reranker.** A multilingual cross-encoder (mMARCO MiniLM, ONNX, 113 MB)
  replacing the fused order *lost* (0.82; tables 0.44 — it ranks Markdown tables
  below prose); fused with it, it matched v2 without it, for +0.5 s a question.
  The chain keeps a fused `reranker=` seam (`cross_encoder.py` is the
  experiment).
- **No HyDE by default.** With it, answers were no more often correct (0.93
  both), retrieval got worse (hit@1 0.85 → 0.80) — Vietnamese questions fell
  from 1.00 to 0.33, the hypothetical answer pulling the search away from the
  document's language — and every question waited ~1.1 s more for an extra
  paid call before retrieval could start. `hyde=True` keeps it available.
- **Visual retrievers (ColPali, ColQwen2) were not built** — a judgement,
  not a measurement. They lead visually rich retrieval benchmarks (ViDoRe),
  but run on torch, which the installer leaves out on purpose (466 MB), with
  2-3B-parameter backbones (PaliGemma-3B, Qwen2-VL-2B; the smaller ColSmol
  variants still need torch), and store ~1,000 128-d vectors per page
  (~0.5 MB a page in fp32). Our estimate is seconds per page to index on a
  CPU-only desktop, against v2's ~0.4 s per text page. Text retrieval over
  OCR plus visual reading of the top pages got scans to 0.91 hit@1 and charts
  to 1.00 answer accuracy without them; a visual retriever is the next thing
  to try for charts with no caption (see Limitations).

## Verified outside the benchmark

The frozen sidecar (`scripts/build_sidecar.py`, PyInstaller) was rebuilt with
the spec change — RapidOCR bundled, Camelot and pdfplumber gone; the tree is
834 MB, ~15 MB more than before — and driven over HTTP against a throwaway
data root: `report.pdf` indexed into 15 chunks (13 without OCR, so both scans
were OCRed in the bundle), the dividend question answered from the scanned
memo with `[p. 7](#page=7)` cited, and an Argos translate finished with 0
failed paragraphs (with `CUDA_VISIBLE_DEVICES=-1`: on this machine's RTX 5060
GPU Argos fails a few paragraphs with `CUBLAS_STATUS_NOT_SUPPORTED`, a known
issue unrelated to chat).

## Limitations

- The corpus is small (49 questions, four documents) and partly synthetic; a
  difference of one question is 2-3 points. Treat differences under ~5 points
  as noise.
- One answer model. A weaker vision model reads charts worse; a text-only one
  gets no images at all (v2 then behaves like the "no page images" row).
- OCR is slow on CPU (~7 s a dense page) and reads Vietnamese without
  diacritics; the answer model is shown the scanned page itself, which covers
  the second, not the first.
- Charts with no caption and no text near them are found only if their page
  ranks on other words: retrieval never looks at pixels.
- Follow-up questions are answered on their own: the chat sends no history to
  retrieval, so "and in Q4?" retrieves poorly.
- `figure` is the weakest kind (0.50 for every system): the architecture
  diagram's "Linear" box is read as text by none of them reliably.
