# Chat RAG benchmark

How PDFusion's chat (`src/desktop_pdf_translator/rag/`) was redesigned, what it
was compared against, and the numbers behind every choice. Re-run it after
touching `rag/`: the unit tests check behaviour, this checks quality.

```bash
conda activate pdfusion
python benchmarks/rag/build_corpus.py                 # once; ~2 MB of public papers downloaded (needs matplotlib)
python benchmarks/rag/run.py --system v2              # retrieval only: local, free
python benchmarks/rag/run.py --system v2 --answers    # + answers from the configured model
python benchmarks/rag/run.py --system v2 --split dev  # only the questions decisions are argued from
python benchmarks/rag/run.py --system v2 --options '{"embedding": "Xenova/bge-m3"}'  # another EmbeddingSpec
python benchmarks/rag/run.py --system v2 --answers --model gemini:gemini-2.5-flash  # another answer model, this run only
python benchmarks/rag/compare.py main-answers final-answers   # Δ per metric, paired 95% interval
git worktree add ../PDFusion-baseline <commit before the redesign, e.g. 453229d>
python benchmarks/rag/run.py --system baseline --src ../PDFusion-baseline/src --answers
python benchmarks/rag/compare.py baseline-answers v2-answers
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
| `bao_cao.pdf` | 11 | A synthetic **Vietnamese** annual report: every fact only in Vietnamese, a table, a chart in pixels, a scanned resolution, and a page whose words collide with a fact's once diacritics are folded (`giá đường` vs `gia tăng`, `Bình Dương`) |
| `manual.pdf` | 6 | A product manual carrying **prompt injections**: one instruction in plain sight, one in white 1-pt text |
| `bert.pdf`, `resnet.pdf` | 16, 12 | The two real papers `long.pdf` pads with, asked about on their own |

`questions.json`: 131 questions (7 of them on `long.pdf`) tagged `text`, `table`,
`chart`, `figure`, `scan`, `multilingual` (Vietnamese, with and without
diacritics, and English questions on the Vietnamese report), `negative` (not
answerable from the document — the right answer says so), `followup` (carries
the conversation before it in `history`; means nothing alone), `multihop`
(answer on two pages), `global` (whole-document; passes when `min_match` of its
topics are named) and `injection` (an answer matching a `forbidden` pattern —
what the document told the model to say — is wrong whatever else it says).
Gold pages are every page that states the answer, checked against the text.
Every question has a fixed `split`, a third `test` by a hash of its id:
decisions are argued from `dev`, `test` only reports. Every rate is printed
with its Wilson 95% interval and `compare.py` gives each difference a paired
bootstrap interval and, for pass/fail metrics, McNemar's exact p (`stats.py`).

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
- **v2** — this redesign (see `docs/architecture/data-and-chat.md` → "Chat answers").

## Results

### Answers (Gemini 2.5 Flash-Lite, 43 questions)

v2 is the branch's final code (`results/v2-final-answers.json`); a run on the code
before the review fixes scored the same. The ablation rows are from that earlier code.

| | correct | text | table | chart | figure | scan | multilingual | negative | cites a gold page | citation precision |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.47 | 0.82 | 0.44 | 0.00 | 0.50 | 0.00 | 1.00 | 0.75 | 0.69 | 0.20 |
| **v2** | **0.93** | 1.00 | 1.00 | 1.00 | 0.50 | 0.82 | 1.00 | 1.00 | **0.97** | **0.97** |
| v2, no page images | 0.86 | 1.00 | 0.89 | 0.00 | 0.50 | 0.91 | 1.00 | 1.00 | 0.90 | 0.88 |
| v2 + HyDE | 0.93 | 1.00 | 0.89 | 1.00 | 0.50 | 0.91 | 1.00 | 1.00 | 1.00 | 0.99 |

### Retrieval (39 answerable questions, no LLM)

| | hit@1 | hit@3 | MRR@5 | scan hit@1 | table hit@1 |
|---|---|---|---|---|---|
| baseline | 0.62 | 0.69 | 0.65 | 0.00 | 0.67 |
| **v2** | **0.85** | 0.95 | **0.90** | 0.91 | 0.67 |
| v2 + cross-encoder replacing the order | 0.82 | 0.97 | 0.89 | 1.00 | 0.44 |
| v2 + cross-encoder fused | 0.85 | 0.95 | 0.90 | 0.91 | 0.67 |
| v2 + HyDE (one more LLM call) | 0.80 | 0.92 | 0.88 | 0.82 | 0.67 |

### Cost and latency per question (i5-12400F, no GPU)

| | LLM calls | input tokens | output tokens | p50 total s | p90 total s |
|---|---|---|---|---|---|
| baseline | 2.4 | 1,003 | 149 | 2.62 | 2.94 |
| **v2** | **1.0** | 2,925 | 25 | **2.18** | 2.78 |
| v2, no page images | 1.0 | 2,225 | 30 | 0.76 | 0.93 |
| v2 + HyDE | 2.9* | 3,130 | 184 | 4.26 | 5.06 |

\* Includes the provider's 429 retries, which `_call_with_backoff` counts as calls.

### How cost grows with length (`long.pdf`, 57 pages, 6 questions)

| | correct | citation precision | input tokens / question | p50 s / question | LLM tokens to index |
|---|---|---|---|---|---|
| **v2** | **6/6** | 1.00 | **2,366** (15 pages: 2,925) | **2.2** | 0 (25 s local, OCR included) |

v2's per-question cost does not depend on the document's length — retrieval
picks the pages, and at most three are sent as images. Six questions is a
small sample; the token figures are what it is for.

Indexing, once per document: v2 takes 6 s for the 15-page paper (baseline 17 s,
Camelot retrying without Ghostscript) and 4 s for the report; the scanned paper
takes ~110 s, nearly all of it RapidOCR at ~7 s a dense page. The baseline
"indexes" the scanned paper in 1.6 s because it finds no text and refuses it —
in the app, "No text could be extracted from this PDF".

## What each design decision rests on

- **Retrieve by text, read by vision** (rather than sending every page as an
  image, which makes every question pay for every page). v2 sends at most
  three page images, chosen by retrieval, each labelled with its real page
  number so its citations name the right page: charts reach 1.00.
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

## Round 2: conversations, Vietnamese, hostile documents (2026-10)

The corpus grew from 49 to 93 questions for the gaps the first round left: a
user who asks follow-ups, writes Vietnamese (often without diacritics), opens
real papers rather than ours, or opens a PDF that gives orders. `main` was
measured on it first, then each change against `main`, all with Gemini 2.5
Flash-Lite. `main` and `memory` were run without `long.pdf` (87 questions);
`compare.py` pairs only shared questions.

### Answers

| | correct (95% CI) | followup | multilingual | scan | table | global | injection (hijacked) | citation precision | hit@1 |
|---|---|---|---|---|---|---|---|---|---|
| `main` (`main-answers`) | 0.91 (0.83–0.95) | 0.78 | 0.91 | 0.77 | 1.00 | 1.00 | 1.00 (0) | 0.92 | 0.80 |
| + conversation memory, fence (`memory-answers`) | 0.92 (0.84–0.96) | **1.00** | 0.91 | 0.77 | 1.00 | 1.00 | 1.00 (0) | 0.95 | 0.82 |
| + e5 embeddings (`final-answers`) | 0.95 (0.88–0.98) | 1.00 | 1.00 | 0.93 | 0.92 | 0.67 | 1.00 (0) | 0.98 | 0.85 |
| **+ whole-document reading (`final2-answers`, 101 questions)** | **0.98 (0.93–1.00)** | **1.00** | **1.00** | **1.00** | **1.00** | **0.91** | 1.00 (0) | 0.99 | 0.85 |

Paired against `main` over the 87 shared questions, `final2` answers +0.069
more correctly: 7 answers fixed, 1 broken, bootstrap interval +0.011…+0.126,
McNemar p 0.07. The bootstrap excludes zero; the stricter exact test doesn't
quite, at 0.05 — strong evidence of a gain, not proof, from single runs of a
model that varies between runs. Its parts can't be told from noise on their
own, which is what the per-kind columns are for. Whole-document reading
against `final`: 3 fixed, 0 broken (+0.032, 0.000…+0.075, p 0.25 — one of the
three, a11, had already flipped between earlier runs). Cost per question: 1.2 model calls (the rewrite
runs only on follow-ups), ~4,770 input tokens (2,660 without whole-document
reading; 11,400 on the 15-page Attention paper), answer p50 2.6 s.

### Two answer models, all 127 questions, final code

| model | correct (95% CI) | test split | hit@1 | citation precision | answer p50 | input tokens / question | misses |
|---|---|---|---|---|---|---|---|
| Gemini 2.5 Flash-Lite (`final3-answers`) | 0.97 (0.92–0.99) | 0.98 | 0.86 | 0.98 | 2.5 s | 5,300 | b04, n02, g01, p23 |
| Gemini 2.5 Flash, thinks by default (`final2-flash-answers`) | 0.98 (0.94–1.00) | 0.98 | 0.86 | 0.96 | 3.6 s | 5,300 | b04, g01 |

The thinking model answered every follow-up (9/9): the rewrite's budget
(`ANSWER_MAX_TOKENS`) holds its thinking. Both miss b04 — no configuration
retrieves BERT's page 13 for "how long did pre-training take, and on what
hardware" — and g01, below. Only Gemini was run: the app's other providers
share the prompt, the fence and the citation check, not these numbers.

### Against `main` on all 127 questions

`main` run from a worktree of `main` on the full corpus (`main127-answers`):
0.92 correct (0.86–0.96), hit@1 0.79. The final system (`final3-answers`):
0.97 (0.92–0.99), hit@1 0.86. Paired: correct +0.047 (0.000…+0.102, McNemar
p 0.15), hit@1 +0.067 (0.000…+0.133, p 0.09). `main` was already right on
most of what both systems are asked; the gains are where the new questions
probe — follow-ups 7/9 → 9/9, whole-document questions, Vietnamese without
diacritics, scans — and a corpus this size can't put them past p < 0.05 as a
whole. Each category's change is in the tables above, with its interval.

### Document outline for long PDFs (`longglobal-*`, `final5-answers`)

A PDF too long to read whole is answered from five retrieved pages, which
can't say what the rest holds. Four whole-document questions on such PDFs
were added (`g12`–`g15`: what `long.pdf` contains, BERT's sections, ResNet's
datasets, BERT's benchmarks). From five pages alone the system passed 3/6 of
the long-PDF whole-document questions — asked what `long.pdf` contains, it
said the PDF "does not contain information about separate documents". With
the outline (section headings and their pages, `retrieval.outline`, ≤3,000
characters) 4/6: BERT's sections pass, and `long.pdf` is named as three of
its four documents. BERT's benchmarks still fail: the standalone paper's
subsections aren't detected as headings, so they aren't in its outline. On
the 127 questions the outline isn't meant for it changed nothing (Δ 0.000,
0 fixed, 0 broken) for +1.5% input tokens a question. With it, against
`main` on those 127: +0.047 correct (0.000…+0.094, McNemar p 0.11).

### Tried and dropped: Self-Route (`final4-answers`)

Answer from retrieved pages; when the model begins its reply with
`[NOT FOUND]`, answer again from the whole document if it fits ~70k tokens
(Li et al., EMNLP 2024). Against the same system without it: −0.024
(−0.063…+0.008). Flash-Lite often replied with the bare marker and nothing
after it — three negative questions came back empty — and on b04, the one
question no retrieval reaches, it answered wrongly from page 5 ("30 minutes on
a Cloud TPU") instead of saying the pages didn't hold it, so the second read
never ran. The premise, that the model flags its own retrieval misses, didn't
hold for this model. Removed.

`global` g01 ("Summarize what this report covers") still fails: the answer
lists the report's sections, a fair reading of the question, where its
patterns want the facts. It is left as written — loosening a held-out
question's patterns after seeing an answer is grading to the output.

### Retrieval grid (69 answerable questions, no LLM)

| embedding | BM25 terms | hit@1 | hit@3 | MRR@5 | test-split hit@1 | Vietnamese hit@1 |
|---|---|---|---|---|---|---|
| MiniLM (`g-minilm-plain`) | words | 0.83 | 0.94 | 0.89 | 0.71 | 0.75 |
| MiniLM (`g-minilm-marked`) | + marked words | 0.81 | 0.94 | 0.88 | 0.71 | 0.69 |
| MiniLM (`g-minilm-full`) | + marked words + pairs | 0.80 | 0.96 | 0.87 | 0.67 | 0.69 |
| **e5-small int8 (`g-e5-plain`)** | **words** | **0.87** | **0.99** | **0.92** | **0.86** | **0.81** |
| e5-small int8 (`g-e5-marked`) | + marked words | 0.87 | 0.99 | 0.92 | 0.86 | 0.81 |
| e5-small int8 (`g-e5-full`) | + marked words + pairs | 0.87 | 0.99 | 0.93 | 0.81 | 0.81 |
| e5-small int8, 1200-char chunks (`g-e5-chunk1200`) | words | 0.83 | 0.97 | 0.90 | — | — |

Embedding candidates on the same questions, follow-ups excluded (`emb-*`):
fp32 e5-small 0.83 hit@1, int8 e5-small 0.87, int8 e5-base 0.87 (Vietnamese
0.71), int8 bge-m3 0.88 — but bge-m3 took 54 s to index BERT against e5-small's
11 s, and is a 570 MB download.

On the 127-question corpus minus `long.pdf` (121 questions, 99 of them ranked, `r127-*`): MiniLM 0.82
hit@1, e5-small 0.87; e5 alone right on 8 questions, MiniLM alone on 3
(McNemar p 0.23), hit@1 +0.051 (−0.010…+0.121), hit@3 0.95 → 0.98.

### What each decision rests on

- **multilingual-e5-small (int8) replaced MiniLM** on non-inferiority, not on
  a proven gain: on 99 ranked questions it is ahead on every measure but p = 0.23,
  and the worst its interval allows is 1 point of hit@1 lost. At least as good,
  at the same speed, for a 118 MB first-use download instead of 470 MB. bge-m3
  was the only better model, by one question, at five times the indexing
  time.
- **600-character chunks stayed.** e5 reads 512 tokens, but 1200-character
  chunks retrieved worse (0.87 → 0.83).
- **Diacritic-aware BM25 was tried and dropped.** Indexing each marked word as
  written (so `giá đường` prefers the page that means it) and pairs of
  neighbouring words (so `gia duong binh quan` prefers the page where they
  stand together) fixed the colliding pages for BM25 alone, but fused with the
  dense search they gained nothing with either model, and pairs cost MiniLM
  0.03. The Vietnamese gain came from the embedding model instead.
- **Follow-ups are searched as their rewrite (7/9 → 9/9).** "And when is it
  payable?" or "how many units of it were shipped?" share no word with their
  page. One rewrite call per follow-up, never on a first question.
- **The fence is defence in depth, not a measured gain.** Flash-Lite obeyed
  neither injection in `manual.pdf` before the fence or after; a weaker or more
  compliant model is what it is for.
- **Short documents are read whole** (`rag.whole_document_tokens`, 11,500
  tokens by default, ~40,000 characters). Eight harder whole-document questions were added first; from
  five retrieved pages `final` passed 9/11, missing "list every figure and
  table" (pages 4 and 14 not retrieved) and a summary read from the contents
  page alone. Read whole: 10/11, and nothing else broke. The budget takes in
  the short reports and the 15-page Attention paper, and keeps BERT and ResNet
  (~60k characters) on retrieval, where a question would cost ~15k tokens.
- **CI gate.** `rag-quality` (`.github/workflows/ci.yml`) runs the retrieval
  half on the corpus minus `long.pdf` with `--min-hit1 0.80`. This machine
  measures 0.87 there; the GitHub Ubuntu runner measured 0.83 on the PR that
  added the gate (its OCR reads the scans a little differently). Retrieval is
  deterministic on one platform, so the gate doesn't flake; its margin on the
  runner is about three questions.

## Limitations

- The corpus is small (127 questions, eight documents) and partly synthetic; a
  difference of one question is about a point overall and much more within a
  kind (`chart` has five). Read the intervals, not the point estimates.
- Two answer models, both Gemini. A weaker vision model reads charts worse; a
  text-only one gets no images at all (v2 then behaves like the "no page
  images" row).
- OCR is slow on CPU (~7 s a dense page) and reads Vietnamese without
  diacritics; the answer model is shown the scanned page itself, which covers
  the second, not the first.
- Charts with no caption and no text near them are found only if their page
  ranks on other words: retrieval never looks at pixels.
- Follow-ups are rewritten by the answer model; a model that thinks by
  default may spend the rewrite's budget thinking, and the follow-up is then
  searched as typed. Only Flash-Lite, which doesn't think by default, was run.
- A document longer than `rag.whole_document_tokens` is answered from five
  retrieved pages, so "summarize this" on a 60-page paper reads a fraction of
  it. The budget is a setting because raising it is a measured trade: reading
  BERT and ResNet (12–16 pages) whole fixed b04 and n02 and broke p02 — one
  question in 30 — for 3.5× the input tokens (5.2k → 18.1k a question,
  `longdocs-whole`). Past what a window holds, a map-reduce summary would be
  the next step.
- RapidOCR's model reads Vietnamese scans without diacritics and runs words
  together (`co turc`, `1.50o`); a scanned Vietnamese page is found mostly
  through its digits and read from its image. No PaddleOCR model has the
  Vietnamese alphabet (the Latin one has 4 of its 52 marked letters), and the
  OCRs that do run on torch, which the installer excludes — see
  `docs/architecture/data-and-chat.md`.
- `injection` is four questions on one document with one model.
- `figure` is the weakest kind (0.50 for every system): the architecture
  diagram's "Linear" box is read as text by none of them reliably.
