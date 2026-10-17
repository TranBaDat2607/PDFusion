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
machine is local, offline and free; the only paid call is the answer itself,
and it is shown pictures of at most three pages. The design, the alternatives
it was chosen over, and the measurements behind each choice are in
`benchmarks/rag/README.md`. In short:

```
index (once per document, rag/ingest.py)            ask (rag/rag_chain.py)
  PyMuPDF blocks → paragraphs ≤600 chars              dense top-20  ─┐
  headings → chunk metadata                           BM25 top-20   ─┴─ RRF → pages (≤5)
  ruled tables → Markdown (find_tables)                     │
  scanned pages → RapidOCR (rag/ocr.py)               best visual/table/scanned pages
  page_kind: text | visual | scanned                   rendered → images (≤3)
  embed heading+text (MiniLM ONNX) → ChromaDB               │
                                                      answer model: page text + images,
                                                      cites [p. N] → checked, linked
```

**Ingestion** (`rag/ingest.py`, `PdfIngestor`) replaced
`ScientificPDFProcessor`, and each difference fixed a measured failure:

- **Chunks fit the embedding model.** MiniLM reads 128 tokens
  (`onnx_embeddings._MAX_SEQ_LENGTH`), ~500 characters of English; the old
  ~1000-character chunks were half unembedded. Paragraphs are packed up to
  `MAX_CHUNK_CHARS = 600`, split between sentences, never across pages.
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
- **Scanned pages are OCRed** (`rag/ocr.py`) with RapidOCR, which BabelDOC
  already installs; the spec now bundles it. Its recogniser drops the spaces
  between English words; `respace` restores them from the character boxes'
  gaps (word F1 0.33 → 0.64 against the paper's real text). A scanned PDF used
  to be refused outright ("No text could be extracted"). OCR is slow — ~7 s a
  dense page on an i5 — and runs once, with per-page progress on the index job.
- Text is NFKC-normalised (ligatures: "ﬁrmware" never matched "firmware")
  and dehyphenated. `CHUNKER_VERSION` is "2", so every existing index is
  rebuilt the next time chat opens its document.

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
  all of them.
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
  image** — a 400/415/422 mentioning "image", typically a text-only model
  behind OpenRouter or Ollama — is asked again without them, and that
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
- **References are one per evidence page, cited first** in the order the
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
