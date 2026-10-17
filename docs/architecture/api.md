# The sidecar's HTTP API

The schema itself is `desktop/src/lib/openapi.json`; read it there. This file
holds what the schema can't say: how it is generated, how long jobs stream, and
the parts of the contract that are easy to get wrong.

## Generated API types (OpenAPI)

The HTTP/SSE boundary between the sidecar and the desktop app is described by
two generated, checked-in artifacts rather than hand-copied interfaces —
three of the latter had already drifted from `api/schemas.py` in production
before this existed (language fields silently never sent, `pdf_references`
vs. `pdf_sources` — issue #13, a dead `deep_search`/web-research pair of
fields — issue #14). See issue #27.

1. `python -m desktop_pdf_translator.api.export_openapi --output desktop/src/lib/openapi.json`
   calls `create_app().openapi()` — the same schema FastAPI serves at
   `/openapi.json` — and additionally stitches the SSE routes' real payload
   shapes into it. FastAPI has no idea what `EventSourceResponse` sends
   (every SSE route is a bare `-> EventSourceResponse`, no `response_model`),
   so without this step those three routes' schemas are an empty
   placeholder. The payload shapes live in `api/sse_schemas.py` — mirrors of
   `processors/events.py`'s dataclasses and the ad-hoc dicts in
   `api/jobs.py` / `api/routes/rag.py` / `api/routes/translation.py`. They
   are *not* real `response_model=`s — SSE payloads are never validated at
   runtime (see "Long-running jobs" below) — and the stitching only happens
   in this export script, never in `server.py`, so it carries zero runtime
   risk to the real routes. `tests/test_sse_schemas.py` guards against these
   mirrors drifting from `processors/events.py`.
2. `pnpm run generate:api-types` (in `desktop/`) runs `openapi-typescript`
   over that file to produce `desktop/src/lib/api-types.d.ts`. It's a
   `pnpm build` prestep, so a build always regenerates it from whatever
   `openapi.json` is on disk.

CI enforces both files stay current, split by toolchain since neither
existing job has both Python and Node:
- **`python` job**: regenerates `openapi.json`, then `git diff --exit-code`s it.
- **`desktop` job**: `pnpm run check:api-types` (`openapi-typescript … --check`)
  compares a fresh generation against the checked-in `api-types.d.ts` without
  writing to it. This step runs *before* `pnpm build` — `pnpm build` would
  otherwise regenerate and overwrite the file first, making the check
  trivially pass against its own fresh output.

Frontend code imports these generated types instead of hand-declaring
interfaces that mirror the wire format
(`import type { components } from "@/lib/api-types"`). Two loose ends worth
knowing about:
- `ConfigResponse`'s `translation`/`rag`/`gui`/`processing` fields had to
  become real nested models (`TranslationSettings` etc., `config/models.py`)
  instead of `Dict[str, Any]` — left loose, the generated type would have
  erased exactly the fields this issue was about.
- `streamJobEvents<T>`'s `data as X` casts in the hooks don't disappear —
  `SseEvent.type` is a bare `string`, not a discriminant on `T`, so narrowing
  an SSE union still needs an assertion. What changed is that `X` is
  generated, not hand-maintained.

## Long-running jobs (SSE pattern)

Long-running endpoints (translate, index, ask) follow the same pattern:
1. `POST /resource` returns `{ job_id }` immediately.
2. The actual work runs in a background asyncio task that appends events to
   the job's `history` (`api/jobs.py`) and wakes anyone waiting on it.
3. `GET /resource/{job_id}/events` opens an SSE stream that replays whatever
   in `history` is newer than the `last_seq` query param (default 0), then
   tails live events the same way.
4. A terminal event (`done`, `error`, or `cancelled`) closes the stream.

**A job survives a dropped SSE connection** — a webview reload, a network
blip — instead of being discarded the moment its consumer detaches (issue
#28). State lives entirely in the shared `Job`, not a per-consumer queue, so
a reattaching `GET .../events?last_seq=N` picks up exactly what it missed
and `/cancel` keeps working on a job nobody is currently streaming. Only
`_sweep_stale_locked` ever frees a job — once it has had no live worker and
no fresh terminal event for `_JOB_TTL_SECONDS` (an hour). A finished job's
small event history is left to linger for that whole window by design (a
reattach might still be coming); `finish()` drops the heavy `processor`
handle immediately so that cost stays small regardless. On the frontend,
`lib/sse.ts`'s `streamJobEvents` is what actually reconnects —
`useTranslation`/`useRagIndex`/`useRagAsk` all go through it instead of the
lower-level `streamEvents`, so a transient drop within the same page load is
invisible rather than surfacing as "stream ended unexpectedly". That only
covers *that* window: nothing persists the active job id across a full page
reload (there's no stored handle to reattach with afterward), and there's no
job-history table or "Recent" list yet — both remain open, see issue #28.

**`/setup/engine` is still the one exception**, but no longer for that
reason: it isn't that the registry mishandles a dropped connection (it
doesn't, anymore) — it's that the install has no `job_id` at all to reattach
*to*, being a process-wide singleton rather than a per-request job. See
["First-run engine setup"](startup-and-shell.md#first-run-engine-setup).

## Contract notes

What the old route table recorded that neither `openapi.json` nor CLAUDE.md
does. Everything else about a route is in its schema and, for most write
routes, its docstring (served as the operation's `description`).

- **`POST /translate` refuses before it creates a job**, in this order: an
  unsupported language pair (checked against the *effective* service), then a
  file or page selection over the limits or past the end of the document —
  all **422** — and then a missing engine, **409**. `source_lang`,
  `target_lang` and `service` are `None`-defaulted so config applies (see
  "Language selection and backend capabilities" in
  [translators.md](translators.md)). `bypass_cache: true` is what
  Re-translate and Retry send.
- **`total_chunks` is not a page count** — a chunk can span several pages —
  and `total_pages` is the whole document's; progress is
  `pages_to_translate`.
- **`POST /translate/estimate` ignores the page limit**: over it, Translate
  offers the pages that fit, so the estimate must still answer. A file over
  the size limit is a 422 and is never read.
- **A document's id used to be the file name stem**, so two different
  `paper.pdf`s shared one index (#59); hence the SHA-256. The first
  `progress` event of `/rag/index` carries `document_id`, sent before the
  vector store loads, so the chat panel can show the document's saved
  conversation meanwhile.
- **`POST /rag/ask` requires `document_id`** (422 without it), refuses a
  document with no ready index with **409** before a job exists, and
  `None`-defaults `target_lang` like `/translate`. Each reference is one
  evidence page (see "Chat answers" in [data-and-chat.md](data-and-chat.md));
  its `page` is 1-indexed or `null`. Chunk metadata is 0-indexed and
  `PdfViewer.scrollToPage` counts from 1, so `rag_chain._display_page`
  converts at that one boundary. `error.code: index_unavailable` makes the
  chat panel index the document again.
- **`GET /providers/{id}/models` reports a failed listing as `error` in a
  200**, beside the stale listing when there is one, and lists again when the
  cached one is older than 24 h or on `refresh`.
- **`GET /setup/status` is stat calls only**, because it is polled twice a
  second during an install.
