# Data and chat

The stores under the data root, and how chat answers from one document.

## Local data layer

Everything the sidecar persists lives under one root, `utils/paths.appdata_dir()`
— `%LOCALAPPDATA%\PDFusion` on Windows, the platform's equivalent elsewhere,
resolved exactly as `sidecar.rs:appdata_dir` does (see [cross-platform.md](cross-platform.md)) — in
three kinds of store with different rules (#59):

| Store | Holds | Rules |
|---|---|---|
| `pdfusion.db` (`storage/records.py`) | **Records.** `documents` (id = SHA-256 of the file's bytes), `document_locations` (every path a document was opened from), `rag_indexes` (one row per chat index: embedding model and dimension, chunker version, `indexing` → `ready` \| `failed`), `chat_messages` (a document's saved chat, schema v2) | System of record. Foreign keys with `ON DELETE CASCADE`; at most one `ready` index per document, model and chunker, enforced by a partial unique index. A file from a newer build refuses to open (`on_too_new="raise"`) |
| `vectors/` (`rag/vector_store.py`) | **Derived.** One ChromaDB collection per `rag_indexes` row, named `rag_<id>` | Rebuildable. Chunks carry only what retrieval reads; a document's id and path live in the records |
| `translation_cache/`, `translated_pdf_cache/`, `model_catalog/` | **Caches** — see ["Two-tier translation caching"](translation-pipeline.md#two-tier-translation-caching) and ["Verifying keys and discovering models"](providers.md#verifying-keys-and-discovering-models) | Disposable: clearing them never touches a record. `pdf_translations.file_hash` equals `documents.id` by value |

All SQLite goes through `storage/sqlite.py` and `storage/migrations.py`. To
change a schema, append a `Migration` to that store's `_MIGRATIONS`.

**Why chat can only answer from the open PDF.** A question names a document;
`POST /rag/ask` looks up that document's ready index in the records (409 if it
has none), and retrieval reads that index's collection and nothing else. There
is no metadata filter to forget and no "search every document" mode. What keeps
that true:

- **An index is `ready` only once it is whole.** `_run_index` records it as
  `indexing` before writing a chunk, and `complete_index` marks it ready and
  replaces the document's other indexes in one transaction. A failed or
  cancelled job fails the row and drops the collection (`_abandon`). Whatever
  that misses, `_recover` catches when a process first opens the vector store:
  `indexing` rows a dead process left are failed and their collections dropped,
  any collection no row accounts for is dropped, and a `ready` row whose
  collection is gone is deleted.
- **Indexing and deleting are serialized per document** (`_document_lock`). The
  chat panel starts an index job whenever a PDF opens, so one document can be
  requested twice at once; the second job finds the first one's index ready.
- **Changing the embedding model or the chunker means editing
  `rag/index_spec.py`.** A ready index is looked up by both, so every document is
  indexed again on its next open instead of mixing vectors in one index.
- **A question reads its index's chunks once** (`vector_store.get_chunks`);
  both keyword passes and the surrounding-context step work from that list.
- **The lifespan deletes `chroma_db/` and `chroma_db_v2/`**
  (`server.py:_remove_legacy_vector_stores`), the stores older builds used.
  `sidecar.rs:ensure_appdata_layout` creates `vectors/`, and must never create
  `chroma_db_v2` again.

## Chat answers

Chat is multimodal RAG built on one rule: **retrieve by text, read by
vision.** Everything that runs per document or per question on the user's
machine is local, offline and free; the paid calls are the answer itself —
shown pictures of at most three pages, and the whole text of a document
short enough — and, for a follow-up only, one short rewrite of it into a
question that stands alone (below). The design, the alternatives
it was chosen over, and the measurements behind each choice are in
`benchmarks/rag/README.md`. In short:

```
index (once per document, rag/ingest.py)            ask (rag/rag_chain.py)
  PyMuPDF blocks → paragraphs ≤600 chars              a follow-up? → rewritten to stand alone
  headings → chunk metadata                           dense top-20  ─┐
  ruled tables → Markdown (find_tables)               BM25 top-20   ─┴─ RRF → pages (≤5)
  scanned pages → RapidOCR (rag/ocr.py)                     │
  page_kind: text | visual | scanned                  best visual/table/scanned pages
  embed "passage: " + heading + text                   rendered → images (≤3)
    (multilingual-e5-small int8, ONNX) → ChromaDB           │
                                                      answer model: fenced page text + images
                                                      + recent turns, streamed as written;
                                                      cites [p. N] → checked, linked
```

**Ingestion** (`rag/ingest.py`, `PdfIngestor`) replaced
`ScientificPDFProcessor`, and each difference fixed a measured failure:

- **Chunks fit the embedding model.** MiniLM, the first model, read 128
  tokens, ~500 characters of English; the old ~1000-character chunks were half
  unembedded. Paragraphs are packed up to `MAX_CHUNK_CHARS = 600`, split
  between sentences, never across pages. multilingual-e5-small, the model now
  (below), reads 512 tokens, but 1200-character chunks retrieved worse with it,
  so 600 stayed.
- **The embedding model is measured, not assumed.** `rag/index_spec.py` names
  it, and `EMBEDDING_SPECS` says how each candidate is run — its ONNX file,
  pooling, and the `query: ` / `passage: ` prefixes the e5 family was trained
  with (`OnnxEmbeddingFunction.embed_query` is what ChromaDB calls for a
  question). int8 multilingual-e5-small replaced MiniLM as at least as good —
  ahead on every measure (99 ranked questions: hit@1 0.82 → 0.87, hit@3 0.95 →
  0.98) but not significantly (McNemar p 0.23) — at the same speed, for a
  118 MB download instead of 470 MB. bge-m3 was
  marginally better and five times slower to index; e5-base no better than
  e5-small (`benchmarks/rag/README.md`).
- **Headings are context, not chunks.** A line ≥1.15× the document's body
  size is a heading; it becomes the `heading` of the chunks under it, and
  `retrieval.contextual_text` puts it in front of the chunk for embedding and
  BM25 — so a table row under "Products" matches a question about products.
  The stored and quoted text is the chunk's own. Body size ignores table
  cells, which papers often set smaller.
- **Tables are Markdown from PyMuPDF's `find_tables`**, built from
  `Table.extract()` (`to_markdown()` HTML-escapes `-` as `&#45;`), caption
  first, cells not repeated as text. A grid that is mostly empty — the
  Attention paper's attention maps — is a figure drawn with lines, not a
  table. Camelot (which needs Ghostscript, so mostly failed) and pdfplumber
  are gone, and with them the "Column 1 | Column 2 / Table data | not
  extracted" placeholder the old chunker wrote into chunks and chat quoted.
- **Every page has a `page_kind`**: `scanned` (no text layer, mostly image),
  `visual` (an image ≥10% of the page, a table, or a `Figure N:`/`Table N:`
  caption — which also catches vector figures and unruled tables), `text`.
  This is what decides which pages the answer model sees as images.
- **Vietnamese scans are read by the answer model, not the OCR.** RapidOCR's
  recogniser is PaddleOCR's Chinese/English model: it drops Vietnamese marks
  and runs words together (`co turc`, `1.50o`). No PaddleOCR model replaces it
  — the Latin one has 4 of the 52 Vietnamese letters it would need, and the
  others fewer (checked against every dictionary of the ONNX conversions,
  2026-10). VietOCR and EasyOCR read Vietnamese but run on torch, which the
  installer leaves out on purpose ([packaging.md](packaging.md)); Tesseract's
  `vie` is a system install. So a Vietnamese scan is found by its digits and
  whatever survives, and read from its image (`select_image_pages` always
  offers a scanned page) — which is why the benchmark's Vietnamese scan
  questions pass while its OCR text is poor.
- **Scanned pages are OCRed** (`rag/ocr.py`) with RapidOCR, which BabelDOC
  already installs; the spec now bundles it. Its recogniser drops the spaces
  between English words; `respace` restores them from the character boxes'
  gaps (word F1 0.33 → 0.64 against the paper's real text). A scanned PDF used
  to be refused outright ("No text could be extracted"). OCR is slow — ~7 s a
  dense page on an i5 — and runs once, with per-page progress on the index job.
- Text is NFKC-normalised (ligatures: "ﬁrmware" never matched "firmware")
  and dehyphenated. Changing how a PDF becomes chunks means bumping
  `CHUNKER_VERSION` (now "3"), which rebuilds every existing index the next
  time chat opens its document.
- **Line-break hyphens are joined by `ingest._join_broken_words`, not by
  PyMuPDF.** `TEXT_DEHYPHENATE` stopped working in PyMuPDF 1.27, which the
  translation memory fix requires (translation-pipeline.md, "PyMuPDF is at
  least 1.27.2"). Without a replacement, 1,129 words on 278 of a 346-page
  book's pages were indexed as halves ("trans- action"), which neither search
  matches. The helper is the flag's rule reproduced exactly: a line ending in
  "-" loses it and runs into the next, unless the line is only the dash.
  Checked chunk for chunk, the ingestor's output on 1.25.5 is identical
  before and after the change on all seven text documents in the benchmark,
  so `CHUNKER_VERSION` stays and no index is rebuilt.
  - The rule glues compounds ("Bose-" / "Einstein" → "BoseEinstein"). A
    variant that joined only lowercase continuations was measured and lost two
    questions on BERT, whose tables use "-" for an empty cell, so the exact
    rule stayed.
  - Retrieval-only hit@1, PyMuPDF 1.25.5 → 1.27: 0.857 → 0.838 with no join,
    0.848 with the helper. The one question still lost (`p10`, ResNet) comes
    from 1.27's table finder, not from the join.

**Retrieval** (`EnhancedRAGChain.retrieve`) is local and costs one read of the
index's chunks:

- **Two searches, fused by rank.** ChromaDB's top 20 by cosine and BM25's top
  20 (`keyword_search.py`, over the contextual text) are combined by
  reciprocal rank fusion (`retrieval.reciprocal_rank_fusion`, k = 60). The old
  blend added cosine to BM25 scaled so its best hit was 1.0 — two scales with
  nothing in common, so one rare word outranked every semantic hit.
- **Evidence is pages, not chunks** (`retrieval.group_by_page`). A page ranks
  by its best hit (a long page matches common words many times over, which
  says little), brings its hits and their neighbours *on that page*, and is
  what a citation names, the viewer jumps to, and the model can be shown.
  `max_pdf_sources` is now a number of pages.
- **No re-rank by heuristics, no HyDE by default.** The old re-rank scored
  pages by how early they came, so page 1 won ties. HyDE put a model round
  trip in front of every search; with `hyde=True` it still runs (its prompt
  still carries no language instruction). A local multilingual cross-encoder
  was measured too: it ranked Markdown tables below the prose around them,
  and fused with the first stage gained one question in 39 for +0.5 s a
  question and a 113 MB download, so the app ships without one. The chain
  keeps a `reranker=` seam that always fuses; the experiment lives in
  `benchmarks/rag/cross_encoder.py`.

**The answer** (`_generate_answer`, `_create_answer_prompt`):

- **The model reads every evidence page under its 1-indexed page number**:
  each page's hits and their neighbours on it (`NEIGHBOURS = 1`), every chunk
  whole. The chat lists each page as a source, so the answer must have read
  all of them. (A short document is read whole instead; see "A short document
  is read whole" below.)
- **The best-ranked pages whose content the text loses are shown as images**
  (`retrieval.select_image_pages`, at most `DEFAULT_MAX_IMAGES = 3`): visual
  and scanned pages, and pages with a table chunk. They are rendered when the
  question is asked (`rag/page_images.py`, JPEG, long side 1600 px, ~1.5k
  tokens each), not stored at index time, and each goes after a text label,
  `[Image of page N]`, so the model can cite what it saw. A file moved since
  indexing costs the images, not the answer.
- **`generate(images=...)`** (`translators/base.PageImage`) is implemented
  for OpenAI (content parts), Gemini (`Part.from_bytes`) and Anthropic
  (base64 blocks); with no images the request is byte-for-byte what it was.
  `images` is passed only when there are some. `ProviderSpec.accepts_images`
  is False for DeepSeek, whose API takes text only. **A model that refuses an
  image** — a 400/404/415/422 mentioning "image", typically a text-only model
  behind OpenRouter — is asked again without them, and that
  (provider, model) is sent text only for the rest of the process
  (`EnhancedRAGChain._text_only`). A 401 is not a refusal: it is the usual
  error, never retried.
- **Citations are checked, not trusted** (`rag/citations.py`). The prompt asks
  for `[p. N]`; `link_citations` turns each cited page the model was shown
  into `[p. N](#page=N)` and removes the rest, counting them in
  `quality_metrics.invalid_citations`. Only bracketed page words count —
  `[1]` is a bibliography reference and `[0, 1]` an interval. The chat renders
  those links as jumps (`desktop/src/lib/citation-links.ts`), never as
  navigation of the webview.
- **References are one per evidence page, cited first** (plus any page read
  whole that the answer cites) in the order the
  answer cites them, then the rest in retrieval order, each with `cited`,
  `page_kind` and `shown_as_image` (`sse_schemas.PdfReferencePayload`). They
  used to be every retrieved chunk, unranked by use, so the list said nothing
  about where the answer came from.
- With no key, the answer is extractive — the best passages, each with its
  page link — and says so; it used to read like a synthesis.

What it still has to keep (#31), each with a test in `test_rag_isolation.py`
or `test_rag_answers.py`:

- **A failure is an `error` event, never an answer.** `answer_question`
  raises, and `_run_ask` ends the job with `error`. The chain used to catch
  everything and return "Sorry, I cannot answer…" as the answer, and a failing
  LLM fell back to the template answer, which hid a rejected key behind
  excerpts that looked like one. `generate()` on every LLM backend goes through
  `_call_with_backoff` and raises, the same as `translate()`'s SDK calls.
- **Retrieval that finds nothing never reaches the model**
  (`NOTHING_FOUND_ANSWER`). Given an empty context, a model answers from its
  own knowledge, as if the document had said it. The prompt also tells it to
  say so when the pages don't hold the answer.
- **Each answer records the model that wrote it** (#87). `answer_question`
  returns its `provider` and `model` (`None` for both when no model wrote it:
  the extractive answer, or nothing found), `_run_ask` saves them in
  `chat_messages`' own `provider` / `model` columns (records migration 3,
  appended; older rows read `NULL`), and the chat panel prints them under the
  answer (`model-choice.ts:answeredBy`). The model is chosen once, when the
  question starts, and HyDE and the answer both use it.
- **The answer model is looked up for every question**
  (`EnhancedRAGChain._answer_model`), from `get_settings()` as it is at that
  moment, and rebuilt only when the service, key, model or endpoint changed —
  which is also how a new `rag.answer_model` applies. `PUT /config`
  replaces the settings object, so a chain that kept the one it started with
  ignored a key saved later. **Never rebuild the chain or rerun `_recover` to
  pick up settings**: `_recover` fails every `indexing` row, so an index being
  built right then would be failed and its collection dropped mid-write.
- **Keyword scores are BM25** (`rag/keyword_search.py`). `rank-bm25` is not a
  dependency and is excluded from the bundle. The index is built once per
  question from the list `get_chunks` returned, so it only ever scores that
  document. It runs off the event loop, and so does page grouping. It folds
  case, diacritics and `đ` the way the viewer's find does — which also makes
  OCR text that lost its Vietnamese diacritics findable.
- **The answer language goes in the answer prompt only.**
  `AskRequest.target_lang` is `None`-defaulted to `default_target_lang` and
  resolved when the question is accepted, as `/translate` does. The HyDE prompt
  gets no language instruction: its text is a search query, and in the answer's
  language it would miss the words of a document written in another.
- **A ready index whose collection is gone is repaired in three places.**
  `_recover` deletes the row. `_run_index` rebuilds the index instead of
  reporting "Already indexed". `_run_ask` clears the row and ends with `error`
  `code: index_unavailable`, which makes the chat panel index the document
  again, as a 409 from `/rag/ask` does.
- **A conversation belongs to a document, not to an index.** `chat_messages`
  references `documents(id)`. `complete_index` deletes a document's other
  indexes, so history keyed by index would vanish on every `index_spec.py`
  change — the `CHUNKER_VERSION` bump to "2" among them. Saved page numbers
  stay right because the id is the hash of the bytes; saved `chunk_id`s don't,
  and nothing may rely on them. `_run_ask` saves the exchange under the
  document the question was *accepted* for, and does so before it sends
  `answer`, so a late answer is filed under the right document and a panel
  that refetches on `answer` finds it. Saving is best-effort, and a failed
  question is never saved. Remove forgets a document outright; Reset deletes
  only indexes, and neither ever needs the vector store open.

### Conversations, hostile documents, streaming, ratings

Added together after the benchmark grew to 93 questions — Vietnamese, real
papers, follow-ups, a document that gives orders — and each measured or tested
there:

- **A follow-up is searched for as the question it stands for.** `_run_ask`
  reads the document's last `MAX_HISTORY_TURNS` (3) saved exchanges
  (`rag/conversation.py`, stdlib-only because the route imports it on the boot
  path) and hands them to the chain, which cuts to the window itself at each
  public entry (`answer_question`, `retrieve`) — the one place to change it. With a conversation, one extra model call rewrites the latest question
  to stand alone (`_standalone_question`), in the question's own language — no
  answer-language instruction, for HyDE's reason — and retrieval searches with
  the rewrite **instead of** the typed question: a follow-up's own words ("and
  what about that one?") match whatever page happens to share them, and fused
  in they outvote the rewrite. A failed or empty rewrite falls back to the
  question as typed; with no model, the previous question joins the new one.
  The answer prompt shows the turns (answers cut to `HISTORY_ANSWER_CHARS`) as
  context, never as evidence, before the question. Follow-ups went 7/9 → 9/9.
  The rewrite comes back as the answer's `search_query`, shown under the
  answer: when the sources look wrong, it is the first suspect.
- **The pages are fenced in.** A PDF's text — white 1-pt text included —
  reaches the model verbatim, so a page saying "ignore your instructions" must
  read as something the page says. The pages go between `<document-…>` tags
  whose suffix is random per question, and the system prompt says everything
  inside is data, never instructions ("spotlighting" by delimiting, Hines et
  al., 2024). A fixed tag could be closed by a document that knows it. The
  benchmark's `manual.pdf` carries a visible and a hidden injection; no
  answer obeyed either, before or after — the fence is defence in depth, not a
  measured gain with this model.
- **Earlier answers are fenced in too** (#111). The saved conversation was
  first left outside the fence as "the user's own words", but an answer is not:
  it quotes the document, so an instruction a PDF hid in white text came back
  with the next question, outside the fence, and the defence lapsed after one
  turn. Each earlier answer now sits between `<earlier-answer-…>` tags (clipped
  *before* it is wrapped so a long answer keeps its closing tag), and the
  system prompt of **both** the answer and the follow-up rewrite names the tag
  and says it is data. The suffix is random and new for the rewrite and for the
  answer, so a tag is never one an earlier request has shown; a refusal retry
  of the same question reuses its answer's tag, since the refused attempt
  produced nothing that could have learned it. There is no default tag: a
  caller that passes a conversation without one gets a `ValueError`, not a
  guessable fence.
  A request with no conversation is unchanged — no tag, no extra sentence
  (pinned by a test on the whole system prompt). The benchmark's follow-up
  questions do send one, so *their* prompts changed in both calls, and their
  9/9 above has not been re-measured with an answer model (`--answers`).
  Earlier **questions** stay outside every fence: they are what the user typed.
  The one residual: text the user copies out of the viewer carries any hidden
  white text with it, and that question is outside the fence from then on.
- **The answer streams.** `generate(on_text=...)` on every LLM backend asks
  its SDK for a stream and calls `on_text(offset, piece)`; `_run_ask` turns
  each into an `answer_delta` event, and waits for them all before sending
  `answer`. The stream is read to its end *inside* the request
  `_call_with_backoff` retries, so a dropped stream is asked for again from
  the start, and its offsets restart at 0: the client places each piece at its
  offset (`rag-ask.ts`), so a retry overwrites rather than duplicates. A stream
  that fails partway is closed before that retry: OpenAI's `Stream` releases its
  pooled connection only when read to the end, so each abandoned one held a slot
  until GC (#112); Anthropic's closes itself on a failure inside it, but not
  when `on_text` raises. Gemini's SDK exposes no handle to close. Only the
  answer streams — never HyDE or the rewrite — and only to a backend whose
  `generate` takes `on_text` (asked of its signature, as `images` is avoided
  when empty). The streamed draft has unchecked citations, so it renders none
  as a link; the `answer` event's checked text replaces it. A model the
  provider refuses to stream (`is_stream_refusal`) is asked again whole and
  remembered for the process; the message must name "stream" as a word,
  because a proxy's "upstream provider error" is not a refusal and counting it
  cost a second paid request and the model's streaming (#108).
- **An answer's request timeout scales with its prompt.** `generate()` waits
  `base.generate_timeout_s(prompt, system, images)`: 60 s up to ~10,000 prompt
  tokens (an ordinary five-page answer), then 1.5 s per further 1,000 tokens,
  capped at 300 s, so the largest whole read (80,000 tokens) waits 165 s. Tokens
  are counted as `usage_estimate` does: a CJK character is a token, the rest
  four characters; a page image is 1,500. A fixed 60 s timed out on such a
  read and `_call_with_backoff` then retried it, paying for the full prompt
  each time (#110). The same number is used streamed (it bounds each gap
  between reads, the first being the long one) and on Gemini, where it
  overrides the client-wide 30 s per request (`http_options`, milliseconds,
  and the `X-Server-Timeout` header the SDK would otherwise keep from an
  earlier 30 s request). Paragraph translation keeps its short timeouts. A
  timeout is still retried; the growth is in the wait, not a cap on retries,
  and the constants are room for a long prompt, not a measured rate. A
  non-streamed wait also covers the output, which the prompt size does not
  predict.
- **A short document is read whole.** When the index's text is at most
  `rag.whole_document_tokens` (Settings → Chat → "Read a PDF whole"; 11,500
  by default, ~40,000 characters: the 15-page Attention paper fits), the answer reads every page in page order
  (`retrieval.whole_document`) instead of the retrieved five — "summarize
  this" and "list every table" need all of it. Retrieval still runs and still
  decides what is offered as a source and which pages are shown as images: a
  page read only because the document is short becomes a reference when the
  answer cites it, never for having been shown. Long context beats retrieval
  on quality when it fits and loses on cost (Li et al., EMNLP 2024). Measured
  here on one run each: 3 answers fixed and none broken (McNemar p 0.25 — a
  direction, not proof; one of the three had flipped between earlier runs),
  for +79% input tokens a question (11k on the Attention paper) and +0.25 s at
  p50. The budget is in English-equivalent characters (`retrieval.reading_length`: a
  CJK character counts as a token's worth), and Vietnamese still runs ~1.4× it.
  Hence the **80,000 maximum** (`config.models.WHOLE_DOCUMENT_MAX_TOKENS`): ×1.4 is
  112k, plus 4k of answer, ~3k of page images, ~1.5k of conversation and the
  prompt, ~121k, inside a 128k window (#106). A saved 90,000 or 100,000 — earlier
  versions offered and allowed them — loads as 80,000, not as the default.
  Deciding costs: `reading_length` walks every character in Python, so
  `_reads_whole` returns at once for a budget of 0, stops summing the moment
  the total passes the budget, and runs under `asyncio.to_thread` — on the loop
  it froze every stream for ~100 ms a question on a 300-page PDF (#107).
  The estimate is still an estimate: a provider that refuses a whole read as
  too long (`rag_chain.is_context_overflow`: 413, or a 400/422 naming the
  context length) is asked again, once, with the retrieved pages and the
  outline. That is still the model answering, from pages it was shown (citations
  are checked against what the second request carried), so it is not the
  template answer "a failure is an `error`" forbids; a retrieved read that
  overflows is an error as before. A rate limit's "request too large" (429) is not
  an overflow. `rag_answer` records `whole_read_refused`; the refused request is
  made again for each question on that document. No model, no whole document: the extractive answer quotes retrieved
  passages. `quality_metrics.pages_read` says how many pages the answer read.
- **A longer document comes with its outline.** Read from retrieved pages,
  the answer also gets the document's section headings with the page each
  starts on (`retrieval.outline`, at most `OUTLINE_CHARS`, ~1k tokens): what
  the five pages can't say about the rest — what papers a PDF holds, its
  sections, where each topic is. Ingest keeps those headings already, so it
  costs no model call. It goes inside the fence (headings are the document's
  words), and a page known only from the outline can't be cited: the model
  saw a heading there, not the text; the prompt says so, and says to name
  what the outline lists without citing it. Over three runs each, the
  whole-document questions on PDFs too long to read whole went 9/18 → 10/18:
  a section list 0/3 → 3/3, a "main ideas" summary 3/3 → 1/3. A heading that
  starts a later section again is listed again, so a multi-paper PDF keeps
  each paper's "Abstract". Subsections are in it too: a block wholly in bold
  at the body's size ("4.1 GLUE") is recorded as the chunk's `subheading`
  (`ingest.PdfIngestor._is_subheading`, `CHUNKER_VERSION` "3") — recorded and
  nothing else. Taken as headings, they were embedded with every chunk under
  them and retrieval lost three questions in 99; splitting chunks at them
  still lost two. Left in the text, chunks are byte-for-byte what they were,
  retrieval is unchanged, and the long-PDF whole-document questions went from
  10/18 to 12/18 (9/18 with no outline).
- **A longer document is not given a second, whole read.** "Self-Route"
  (Li et al.) — answer from retrieved pages, and re-answer from the whole
  document when the model says they miss the answer — was built and measured,
  and removed: Flash-Lite often replied with the bare "not found" marker
  (three empty answers), and on the one question retrieval truly misses (b04)
  it answered wrongly instead of saying so, so the second read never ran.
  Net −0.024 on 127 questions (`benchmarks/rag/README.md`).
- **Each answer can be rated.** Records migration 4 adds `chat_messages.rating`
  (1, -1 or NULL); `PUT /rag/document/{id}/messages/{message_id}/rating` sets
  or clears it, and 404s anything that is not an answer of that document. A
  thumbs-down from a real document and question is where the next benchmark
  question comes from.
- **The log says what happened, never what was asked.** One `rag_answer`
  JSON line per answer in `app.log` — pages retrieved, cited and refused,
  images sent, pages read, whether a follow-up was rewritten, model, timings — and
  every line that would carry the question (the chain's, the rewrite's, the
  vector store's) is DEBUG, so a log a user shares holds none of their words.
- **Each answer says how long its halves took** — `quality_metrics`'s
  `retrieve_seconds` and `answer_seconds` — so a slow answer can be told apart
  from a slow search.

## Chat state in the frontend

- **The chat panel keeps no copy of a conversation.** It reads the saved one
  by document id (`useChatHistory`), so what it shows is what `pdfusion.db`
  holds.
- **Chat on/off is server state, not the panel's.** `config.rag.chat_enabled`
  (Settings → Chat, read through `useChatEnabled`) decides whether the toolbar
  has a Chat button and whether the panel can mount at all, and the panel only
  mounts to index. The toolbar button and the panel's X change `chatOpen` and
  nothing else. They used to be one switch that also wrote `rag.enabled`,
  which nothing read; `chat_enabled` is a new name so that a stale `false`
  isn't taken for "chat off" (#32).
- **So is the chat model.** `config.rag.answer_model` is picked in the chat
  header and saved with `PUT /config` (#87); nothing keeps a copy of it in
  Zustand.
