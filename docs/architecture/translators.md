# Translators

The backends BabelDOC hands paragraphs to: which language pairs can run, how
failures are counted, and the interface a backend implements.

## Language selection and backend capabilities

The toolbar's From/To selection reaches the pipeline through the request body,
not through config. `useTranslation.start()` sends `source_lang` / `target_lang`
/ `service` explicitly (built by `lib/translate-request.ts`), because
`ContextBar`'s `update.mutate` is async: a Translate click landing before that
PUT would otherwise run the *previous* selection. `CompletionEvent.target_lang`
still reports the language `process_pdf` actually resolved, and the store's
`translationTargetLang` records it, so the Save dialog names the file after what
was produced rather than what was asked for.

**`None` is the only way to say "unspecified".** `TranslateRequest` /
`PrewarmRequest` default both language fields to `None`; the configured default
is applied in exactly one place,
`translators/capabilities.py:resolve_languages`. Restoring a non-null default
on those fields (`LanguageCode.AUTO` / `VIETNAMESE`) is what caused issue #12 —
both sentinels are truthy, so `process_pdf`'s `source_lang or settings…`
fallback became dead code and every run produced Vietnamese. `AUTO` is a real,
user-selectable source language, never a stand-in for "not chosen".

**`translators/capabilities.py` is the single source of truth** for which
requests can run, and is kept free of heavyweight imports (no BabelDOC, no
torch, no SDK clients) so the API layer and tests can consult it cheaply:

- `SUPPORTED_PAIRS` — built from each `ProviderSpec.supported_pairs`. `None`
  means unrestricted (the LLMs prompt for any target via
  `LANGUAGE_DISPLAY_NAMES`); a `set` is exhaustive. Argos declares
  `{("en","vi")}` and `argos_translator.py` reads its own `_SUPPORTED_PAIRS`
  from here, so a request cannot be accepted as valid and then rejected mid-run.
- `resolve_effective_service` — the "LLM with no API key silently becomes
  Argos" rule, previously duplicated in `PDFProcessor` and the prewarm route.
- `POST /translate` pre-flights the pair and returns **422** before creating the
  job, checked against the *effective* service. Without this, honoring the
  target language would trade a silent wrong-output for a `ValueError` raised
  minutes into a run, from inside `translate()`, with a partial artifact on
  disk.
- `GET /config/options` exposes `supported_pairs` per service, with auto-source
  aliases already expanded, so the toolbar greys out unreachable sources and
  targets without reimplementing the matrix in TypeScript
  (`lib/translate-request.ts`: `isSourceSupported`, `isPairSupported`). When a
  service change leaves an unreachable pair selected, Translate is disabled
  and says why (#33); a disabled button gets no pointer events, so that
  tooltip hangs off a wrapping `<span>`.

Two consequences worth remembering: the frontend sends the **requested** service
(not the effective one) so the sidecar still emits its "falling back to Argos"
notice; and a disabled Radix `SelectItem` sets `pointer-events: none`, so the
"why" is rendered as inline text plus a footer note, never a hover tooltip.

Broadening Argos beyond en→vi means shipping more language packs, not editing
its spec's `supported_pairs` alone.

## Translator failures are counted, not swallowed

`BaseTranslator._handle_translation_error` is the funnel every backend's
`except` clause routes into, and it still returns the **source text** — raising
would change nothing, because BabelDOC's `ILTranslator` catches whatever
`translate()` raises and continues ("ignore error and continue"). So a bad key,
a 429 or a network blip cannot fail a job on its own; the document just comes
out partly untranslated while the pipeline reports success.

**Every path that hands back source text goes through that funnel — including
the ones that never raise.** That is the invariant a new backend has to
preserve, and the one that was missed first time round: a bare `return text`
is invisible to the counter, so the run still reports "complete" and still gets
cached. The non-exception cases are "the provider returned nothing usable"
(a Gemini safety filter drops `candidates` entirely; Anthropic/OpenAI can
return empty content), and, in Argos, a failed `_ensure_en_vi_installed()`
(the ~80 MB pack download — it raises for *every* paragraph, so an unguarded
call meant a first run with no network produced a whole untranslated document
that looked finished), a batch-wait timeout, and an empty result slot.

Three things close that gap:

- **Counting.** `BaseTranslator.failed_translations`, plus an
  `on_translation_failed(error, fatal)` kwarg threaded through the same way as
  `on_paragraph_translated`. `CompletionEvent` carries `failed_paragraphs` /
  `total_paragraphs`, and the overlay says "N of M paragraphs could not be
  translated" with a Retry (which is Re-translate — `bypass_cache=true`).
  The **M** is `translator.translate_call_count`, not the processor's
  `_paragraphs_seen`: the latter is the live-ticker's counter and skips any
  paragraph whose preview came out empty, which understates the denominator.
  Both counters increment under `BaseTranslator._counter_lock` — one instance
  serves BabelDOC's whole worker pool, and `+= 1` is a read-modify-write.
- **Not caching such a run** (see `is_cacheable_artifact` in
  ["Two-tier translation
  caching"](translation-pipeline.md#two-tier-translation-caching)).
- **Aborting on a fatal error.**
  `translators/base.py:is_fatal_translation_error`
  classifies 401/403 (duck-typed on `status_code`/`code`/`.response`, with a
  message fallback for google-genai, which carries the code only in its text).
  On the first fatal failure `PDFProcessor._handle_translation_failure` pushes a
  wake-up item onto the multiplexer queue — the loop is parked on
  `events_queue.get()`, so without it the job would sit there while every
  remaining paragraph made the same doomed call — and the loop raises
  `TranslationProcessError`, which `_process_with_babeldoc` re-raises unwrapped
  so its user-facing sentence survives.

  The message-marker fallback names an **API key** specifically. Generic
  phrases (`unauthorized`, `permission denied`) were tried and removed: a
  provider that means them also sends 401/403, which the status check already
  catches, while `PermissionError`/`OSError` carry "Permission denied" in their
  text and would abort a run whose credentials were fine. Argos reaches this
  path too (a 403 from the package index is fatal, and correctly so), which is
  why the sentence comes from `base.py:describe_fatal_error` — it has no API
  key, so the LLM wording would send the user to check a credential they never
  set and to "switch to Argos" while already on it.

Relatedly, **translation only moves off Argos onto a key that listed its
models**. `PUT /config` did this itself until #88, when a key arrived in one
of its blocks; now keys arrive only through `PUT /providers/{id}`, which never
promotes, and the Models page makes the choice (`provider-draft.ts:
selectsProvider`): only after the key's check succeeded, never on "Save anyway".
Promoting on a typo'd key used to move the user from a working offline
translator onto one that fails every paragraph silently.

The check that decides runs under `catalog.PROBE_TIMEOUT_S`, because Gemini's
client honours its own timeout loosely, and a timeout is a `ListingFailed`
like any other failure — so it never promotes either.

## Translator plug-in interface (BabelDOC integration)

BabelDOC drives chunking, layout, and PDF reassembly; it delegates the actual
text translation to a translator object passed into
`BabelDOCConfig(translator=...)` (see
`processors/processor.py:_create_babeldoc_config`). Four things about
this seam:

1. **Chunking unit = paragraph**, not page. BabelDOC's `ParagraphFinder` groups
   characters into `PdfParagraph` objects (one body paragraph, heading,
   caption, list item, etc.), then `ILTranslator.translate_paragraph` issues
   **one `translate()` call per paragraph** in a thread pool. A typical 10-page
   paper → dozens to hundreds of small calls, parallelized across up to
   `max_parallel_chunks` BabelDOC sub-jobs × `pool_max_workers` threads each —
   32-64 at the defaults. `_create_babeldoc_config`'s `qps=4` argument doesn't
   gate any of that: BabelDOC only applies it through a rate limiter wired up
   from its own CLI entry point (`babeldoc/main.py`), which this project never
   calls. The real, process-wide throttle across every sub-job and every
   concurrent job is `translators/rate_limiter.py`, applied inside
   `translate()` itself (see point 4 below).

2. **The interface is duck-typed, not nominal.** The project's
   `OpenAITranslator` / `GeminiTranslator` / `AnthropicTranslator` /
   `ArgosTranslator` (`translators/*.py`) inherit from the project's *own*
   `translators/base.py:BaseTranslator`, **not** from
   `babeldoc.translator.translator.BaseTranslator`. BabelDOC accepts any object
   that implements:

   - `translate(text: str) -> str` — main entrypoint
   - `get_formular_placeholder(id) -> (placeholder, regex)` — formula
     preservation
   - `get_rich_text_left_placeholder(id)` /
     `get_rich_text_right_placeholder(id)` — rich-text span tags, each a
     `(placeholder, regex)` tuple. BabelDOC compiles `[1]` as the shape of
     placeholders an LLM may have invented and deletes every match a paragraph
     doesn't own; while these returned bare strings, `[1]` of `"<b\d+>"` was
     `"b"`, and every `b`, `B` and `/` left those paragraphs' translations
   - `restore_formular_placeholder(text, id, original)` — post-processing
   - attributes `lang_in`, `lang_out`

   None of them implements `do_llm_translate`, so BabelDOC never sends its own
   LLM prompt — the one that explains `{v1}` and `<b0>` — and hands
   `translate()` paragraphs with those placeholders already in them.
   `BaseTranslator._translation_instructions` is therefore the one prompt every
   LLM backend sends, and it names the placeholders;
   `tests/test_translation_prompts.py` holds it to what
   `get_formular_placeholder` and the rich-text pair actually produce.

   To add another backend (Google Translate, Helsinki opus-mt, NLLB, …), follow
   the same shape as `translators/openai_translator.py` and add a
   `ProviderSpec` for it (see ["Provider
   registry"](providers.md#provider-registry)). The bundled BabelDOC ships only
   an OpenAI-compatible translator — no built-in Google/DeepL.

3. **Argos is the default offline backend.** `translators/argos_translator.py`
   is a free, no-API-key NMT translator used when no LLM key is configured.
   Important quirks:
   - **MVP supports en→vi only.** Other language pairs raise `ValueError`
     directing the user to switch source language or use an LLM.
     `_SUPPORTED_PAIRS` is read from `capabilities.SUPPORTED_PAIRS`, so
     broadening it means shipping packs (see "Language selection and backend
     capabilities" above).
   - **Lazy install.** The `argostranslate` package is imported lazily and the
     ~80 MB en→vi language pack is downloaded on first `translate()` call,
     guarded by a `threading.Lock`. Sidecar startup is unaffected.
   - **Caching.** Argos is deterministic, so it benefits from both the
     persistent paragraph cache (`translation_cache.py`) and the in-process
     batch coalescing added in `3356f30` (concurrent `translate()` calls are
     coalesced into batches of 4). Its `model` field is the fixed string
     `"argostranslate"`.

4. **Every LLM backend's SDK call goes through
   `BaseTranslator._call_with_backoff(request)`**, never a bare
   `self.client...create(...)`. OpenAI and Anthropic reach it through
   `_call_adapting`, which reshapes a request a model refused (see ["LLM
   endpoints and models"](providers.md#llm-endpoints-and-models)). It acquires
   a token from `rate_limiter.get_rate_limiter(self._SERVICE_NAME)` — which the
   factory's `provider_id` overrides per instance (the class attribute only
   says whether a backend is rate-limited at all), so it names one process-wide
   limiter singleton per provider, shared across every job, sub-job and worker
   thread — then retries on 429/5xx/408/409 *and* on transport failures, with
   jittered exponential backoff (`_MAX_RETRIES`, fatal 401/403 excluded). That
   classifier exists because the SDK clients are constructed with
   `max_retries=0`: the OpenAI/Anthropic SDKs default to retrying twice on
   their own — including on connection and timeout errors — and those inner
   attempts don't take a token, so leaving the default in place lets a 429
   storm blow well past the configured QPS. Disabling it without widening the
   classifier would have just moved those errors from "retried by the SDK" to
   "an immediately lost paragraph" — a real regression caught in review of the
   original PR.

   Transport failures carry no status code, so `is_retryable_translation_error`
   matches them by class **name along the MRO** (`_RETRYABLE_ERROR_NAMES`) —
   never `isinstance`, because `base.py` has to import with none of the
   provider SDKs or httpx installed. Two names cover everything, which is the
   whole reason it walks the MRO instead of testing the concrete class:
   `APIConnectionError`, because openai and anthropic funnel every transport
   failure into it via their `except Exception` fallback and both SDKs'
   `APITimeoutError` subclasses it; and `TransportError`, because google-genai
   does *not* wrap, so raw httpx exceptions arrive here and all of them derive
   from it. Listing leaf names instead (`ReadTimeout`, `ConnectError`, …) is
   the version that shipped first, and it silently missed `ReadError`,
   `WriteError`, `NetworkError`, `ProxyError` and `CloseError` — on Gemini, a
   socket-level read failure stayed an immediately-lost paragraph.

   `max_qps` (an `Optional[float]` on each provider's settings, `None` = the
   built-in per-service default in `_DEFAULT_QPS_BY_SERVICE`) is popped in
   `BaseTranslator.__init__` like the other cross-cutting kwargs and applied to
   the shared limiter **once, at construction** — not per-call, since a
   translator's `max_qps` never changes over its lifetime and re-applying it on
   every paragraph would take the limiter's lock to write back the same value.
   It *is* applied unconditionally, resolving `None` through
   `default_qps_for()`, because the limiter outlives the translator: skipping
   the call when no override is set would let a cleared one survive — raise the
   rate once, delete the line from `config.toml`, and the sidecar keeps running
   at the raised rate until it restarts. There's no Settings-sheet control for
   it; it's set per provider by `PUT /providers/{id}` or as `max_qps` under
   `[providers.<id>]` in `config.toml`, most relevant for Anthropic's default
   of 1.0 QPS (sized for its ~50 RPM entry tier — a higher tier gets nowhere
   near its real limit without overriding this).

   A new backend must do the same three things every existing one does: check
   `self.is_cancelled()` at the very top of `translate()` (before the cache
   lookup — a cancelled job shouldn't even pay for a cache read); add `except
   TranslationCancelled: return text` **before** `except Exception` — reversed,
   a cancelled paragraph gets funnelled into `_handle_translation_error` and
   counted as a failure it wasn't; and set `_SERVICE_NAME` if it wants rate
   limiting at all — `None` genuinely opts out, handled by an explicit branch
   in `_call_with_backoff` rather than passed through, since
   `get_rate_limiter(None)` would otherwise build a real `None`-keyed bucket at
   the fallback rate and share it between every backend that never named a
   service. (That branch re-checks the cancel flag itself, so opting out of the
   limiter doesn't also opt out of the cancel check that rode on its
   `acquire()`.) `cancel_event` is threaded in by `PDFProcessor` (one
   `threading.Event` per job), so a Cancel click stops new LLM calls within one
   in-flight paragraph per worker thread — the asyncio-level `task.cancel()`
   alone never reached code already running synchronously on a BabelDOC worker
   thread. Argos gets the same top-of-`translate()` cancel check but no rate
   limiter or backoff (`_SERVICE_NAME` stays `None`) — it's local, and the
   existing "a late `event.set()` on an abandoned batch entry is harmless"
   behavior already covers it.

   The one deliberate exception to "[Translator failures are counted, not
   swallowed](#translator-failures-are-counted-not-swallowed)": a cancelled
   `translate()` call returns source text **without** touching
   `failed_translations` — it's an intentional stop, not a failure, and
   counting it would make a cancelled run look like a partial one if anything
   ever inspected the counters after cancellation. Total retry attempts (not
   distinct paragraphs) land in `retry_count`, which `CompletionEvent` and the
   `/translate` SSE `done` payload also carry as `retry_count` — issue #22
   asked to "surface" it; nothing in the UI reads it today, so consider that
   half-done, not wired to a banner.
