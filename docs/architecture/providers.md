# Providers and models

The provider registry, how a key is verified and its models discovered, and how
a model is chosen.

## Provider registry

`providers/registry.py` is the one place that says which providers exist and
how each differs (#83). A frozen `ProviderSpec` per provider carries its
label, default and suggested models, whether it takes a key or an endpoint,
the environment variable prefix, the default QPS, its place in the "any LLM
with a key" fallback order (`priority`), supported language pairs, retired
model ids, and two lazy callables: the translator class and the model lister
(`providers/listing.py`), which every keyed provider has, since listing is how
its key is verified (#84). Everything
that used to branch per provider reads it: `TranslationService` itself is
built from `PROVIDERS`, and `SERVICE_LABELS`, `SUPPORTED_PAIRS`,
`RETIRED_MODELS`, `KEYED_SERVICES`, the rate limiter's defaults, the
factory, chat's fallback order and `/config/options` are all derived from it,
and `GET /providers` hands it to the frontend, which lists no provider of its
own (#86).

Four things about it are deliberate:

- **Stdlib only.** `config.models` imports it to build the enum, and
  `rate_limiter` (imported by `translators.base`) reads its defaults, so it is
  on the boot path of nearly everything. It must not import `config` (a
  cycle), pydantic or an SDK; `test_provider_registry.py` checks that in a
  fresh interpreter, and `test_sidecar_boot.py` lists it.
- **Callables, not "module:attr" strings.** Both keep the SDKs off the boot
  path, but PyInstaller finds an import inside a function by static analysis
  and cannot see one named in a string. The translator modules are imported
  by nothing else, so strings would have left them out of the frozen sidecar.
  `TranslatorFactory` no longer imports all four backends at the top either;
  a backend's SDK loads the first time one is built.
- **Ids are frozen.** Both translation caches key on the service id, and so do
  `config.toml`'s sections. Renaming one drops every cached paragraph and
  saved key.
- **Order is the registry's.** `PROVIDERS` is the order of `GET /providers`
  (so of the Models page and the pickers) and of `/config/options`; append new
  providers to keep it stable. The fallback order is `priority`, not position.
  Since #88 the OpenAPI schema lists no provider at all: an id is a plain
  string on the wire (`config/models.py:ProviderId`), so a new provider
  changes neither `openapi.json` nor the generated types.

A provider's settings are exactly the keyword arguments its translator
takes, less `enabled_models` and plus the model it runs, so
`TranslatorFactory._get_service_config` passes them whole. Since #85 every
provider's settings are the one `ProviderSettings` class, keyed by id under
`AppSettings.providers`, so a new provider needs no settings class and no
`AppSettings` field — only its spec. The two per-provider bounds that used to
live on separate classes are spec fields too: `max_temperature` (OpenAI's scale
runs to 2, the others' to 1) and `default_max_tokens` (Anthropic requires one).

## Adding a provider

Phases 1–5 of the provider work (#83–#87) were meant to make a new provider a
small, predictable change; #88 proved it with OpenRouter, DeepSeek and Ollama,
each added in a commit touching only the registry and one test fixture. For a
provider speaking an API PDFusion already speaks (OpenAI's, Anthropic's,
Gemini's):

1. **The registry entry**, appended to `PROVIDERS` in `providers/registry.py`
   (never inserted: ids are frozen, and order is the Models page's). Reuse the
   protocol's `translator` and `lister`. Set `default_base_url` — it is *sent*
   when no endpoint is saved (`endpoint_for`), which is how OpenRouter reaches
   its own URL through OpenAI's SDK. A keyed provider takes `env_prefix`,
   `signup_url` and a `priority` (its place in chat's fallback); a keyless
   local server takes `requires_key=False`, a `placeholder_key` if its SDK
   insists on one, `priority=None`, and usually `takes_endpoint` with an
   `endpoint_hint`. Only a **new protocol** needs code: a `BaseTranslator`
   subclass and a lister in `providers/listing.py`.
2. **A recorded list response**, `tests/fixtures/model_lists/<id>.json`, in the
   provider's documented shape. `tests/test_provider_listings.py` lists every
   provider that borrows OpenAI's API from its fixture and checks the request
   went to its own endpoint and nothing was hidden; the registry contract tests
   (`test_provider_registry.py`) check the entry itself.
3. **`retired_models`** on the entry whenever the provider shuts a model down.
4. **Nothing in the frontend, nothing in `schemas.py`.** `GET /providers`
   carries every fact the UI shows, and a provider id is a plain string on the
   wire (`config/models.py:ProviderId`), so `openapi.json` doesn't change
   either.

If a provider needs more than that, fix the design, not the checklist.

Two things that make that hold, both easy to undo: a translator class serves
several providers (OpenRouter, DeepSeek and Ollama all build
`OpenAITranslator`), so the factory passes `provider_id` and the rate limiter
is named after the provider, not the class — keyed on the class, all four
drew on OpenAI's budget and the others' `default_qps` was never read. And a
keyless provider never holds a key: `ProviderSettings` drops one hand-edited
into its table, which would otherwise go out in place of the placeholder and
could never be cleared, since the API refuses a key for it both ways. Argos
is pre-warmed only when it is what a translation would run
(`resolve_effective_service`), not whenever no key is saved: Ollama needs
none.

What listing can and can't tell, per provider: OpenRouter serves its model list
without a key, so Verify can't tell a bad OpenRouter key from a good one — the
first paragraph's 401 aborts the job instead. DeepSeek answers a bad key with
401, like OpenAI. Ollama has no key; listing tells only whether it is running.

## Verifying keys and discovering models

**Verifying a key and discovering models never generate text** (#84). Each
translator's `validate_configuration` used to send a "Hello" completion, and
Settings → Save, `POST /config/validate` and the Argos → LLM promotion all went
through it. Every provider serves the list of models a key can use for free,
so listing does both jobs at once: a 401/403 means the key is wrong, and the
list is what the model picker offers. Nothing in the verify, save or model-list
paths may call `validate_configuration` or build a translator;
`tests/test_providers_api.py` fails if one does. (Argos keeps its
`validate_configuration`, which `POST /providers/argos/verify` runs: it checks
the installed pack and sends nothing anywhere.)

The old rule said a check must send what `translate` sends, "since a check that
sent less would pass a model that fails every paragraph". Two things have since
covered most of that: `param_compat` drops a parameter a model refuses on the
first paragraph that meets it, and a fatal 401/403 aborts the job instead of
producing a document of source text (["Translator failures are counted, not
swallowed"](translators.md#translator-failures-are-counted-not-swallowed)). A mistyped model is caught by not being typed: since #86 the
pickers offer only what Settings → Models switched on from the key's own
listing. A name added there by hand, for a model no list has, is the user's to
vouch for. `verify` still looks a named model up in the list
(`listing.model_matches`, `model_found`), allowing Anthropic's undated aliases
(`claude-haiku-4-5` for `claude-haiku-4-5-20251001`) and Ollama's implicit tag
(`llama3.2` for `llama3.2:latest`).

**What listing alone can't catch**, and a translation will report as failed
paragraphs (or, for a 401/403, abort on):

- a key with no credit or over its quota — OpenAI lists models for a key with
  a zero balance, and the 429 `insufficient_quota` only comes on a completion;
- a model the account may see but not run — OpenAI's organisation-verified
  models, for one;
- a region the provider won't serve;
- a model that is listed but refuses a parameter `param_compat` doesn't know.

An opt-in "Test model" button that sends one tiny request — clicked, never run
automatically — is the planned escape hatch for those. #86 built the Models
page without it; it is still deferred.

**One lister per protocol** (`providers/listing.py`), each importing its SDK
inside the function and reached through `ProviderSpec.lister`:

- **OpenAI** (`GET /v1/models`) returns everything the key reaches, with
  nothing but the id to say which models `chat.completions` — the only call
  the translator makes — will serve. A regex on the id (`_OPENAI_NON_CHAT`)
  moves the rest into `hidden` rather than dropping them, since a heuristic can
  be wrong: embeddings, speech, images and video, and also the completions-only
  `-instruct` models and the Responses-only ones (`-pro`, `codex`,
  `computer-use`, `deep-research`). The issue's prefix list let those through,
  and the toolbar saves a listed model unchecked, so each would have 404ed on
  every paragraph of a job that then ran to the end on source text. On a
  custom endpoint nothing is filtered: Ollama or LM Studio can call a chat
  model anything.
- **Anthropic** (`GET /v1/models`) is walked page by page on
  `has_more`/`last_id`. The old code asked for 100 and never fetched a second
  page.
- **Gemini** (`models.list()`, paged by the SDK) keeps only models whose
  `supported_actions` include `generateContent`. That is the API's own
  statement of capability, so the rest are dropped, not hidden. The speech,
  image and computer-use variants advertise `generateContent` too but don't
  answer in text; an id regex (`_GEMINI_NON_TEXT`) moves them into `hidden`. Google answers
  a bad key with 400 `API_KEY_INVALID`, not 401, which is why failures are
  sorted by `translators/base.py:is_fatal_translation_error` — the same test
  that aborts a translation, with its "api key not valid" marker — rather than
  by status alone.

**Only models released from 2025 on are offered** by those three
(`listing.RELEASED_FROM`); older ones go to `hidden`, so "Show all" still
reaches them and a model already switched on stays on. A picker full of
`gpt-3.5-turbo`, `gpt-4-0613` and Gemini 1.5 buried the models anyone would
choose. OpenAI and Anthropic are dated by the listing's own `created` /
`created_at`. OpenAI's rule applies only at its own endpoint, like the id
filter: Ollama's `created` is when the model was pulled, not released.
Gemini's listing carries no date, so its family version stands in —
`gemini-` below 2.0 (2.0 went GA in Feb 2025) and `gemma-` below 3 (March
2025) are hidden (Gemma 1's `gemma-7b` is a size, not a version), and an id
with no version in it can't be dated and stays. So the Gemini cut is
approximate: `gemini-2.0-flash-exp` and `gemini-exp-1206` (December 2024) stay
offered. An undated model is kept, never guessed old. Catalog migration 2
marks the listings cached before the rule stale, which would otherwise have
offered the old models for up to a day. It doesn't delete them, because the
same row records the last key check.

`catalog.list_models` runs the lister through `asyncio.to_thread` under
`PROBE_TIMEOUT_S` and turns every failure into `KeyRejected` (a refused key) or
`ListingFailed` (anything else). It is the one seam the route tests replace.

**The catalog** (`providers/catalog.py`, `model_catalog/catalog.db`) is a cache,
not a record: a listing can always be asked for again. It goes through
`storage/sqlite.py`, is versioned by an appended `Migration`, and a file from a
newer build is set aside (`on_too_new="reset"`). Rows are keyed by
`(provider_id, base_url)` and carry the listing, when it was fetched, and the
key's state (`valid` after a listing, `invalid` after a refusal). Three rules:

- **Nothing derived from a key is stored** — not the key, not a hash of it. So
  a key change can't be detected from the row; `PUT /providers/{id}` calls
  `invalidate(provider)` (`routes/providers.py`) whenever it saves or clears a key or moves an
  endpoint. A key swapped in `.env` between runs is bounded only by the 24 h
  TTL.
- **Only the saved pair is written.** `verify` with a typed key, or against
  another endpoint, saves nothing: a key the user may yet discard must not mark
  the saved one valid or invalid.
- **A stale list beats none.** When listing fails and an old listing exists,
  `GET /providers/{id}/models` serves it with the `error` beside it.

## LLM endpoints and models

Settings takes any model name, and OpenAI and Anthropic each take an endpoint
(`base_url`), which is what lets Ollama, LM Studio or a proxy stand in for the
provider (#32). README has the recipe. Four rules hold that together:

- **A saved key is only ever sent to the endpoint it was saved for.** `GET
  /config` never reveals a key, but `base_url` decides where the next request
  goes, and with it the key in its auth header. So `PUT /providers/{id}`
  refuses (422) an endpoint change, clearing one included, unless the same body
  carries `api_key` (so did `PUT /config` while it took keys, until #88), and
  `POST /providers/{id}/verify` uses the saved key only against the saved
  endpoint (`routes/providers.py:resolve_probe_target`). Together they keep
  anything holding the bearer token from reading a key off the wire; don't
  loosen either for convenience. The Settings Models page mirrors the rule (`lib/provider-draft.ts:endpointNeedsKey`) so the
  user meets it before the 422. Endpoints are normalized in one place,
  `config/models.py:normalize_base_url` (trimmed, no trailing slash), so a URL
  typed again is not a change. The environment is held to the same rule:
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` replace the saved key on every load,
  and the sidecar inherits the user's whole environment, so
  `ConfigManager._keep_environment_keys_off_endpoints` ignores them for a
  service that has its own endpoint. Without that, the key a `PUT` had to carry
  gave way to the environment's on the next start, and the environment's went
  to the new endpoint.
- **A parameter a model refuses is left out, not failed on.** Claude Opus 4.7
  and later answer 400 to a non-default `temperature`, and OpenAI's reasoning
  models refuse `temperature` and `max_tokens`; PDFusion sends them on every
  paragraph. `BaseTranslator._call_adapting` wraps `_call_with_backoff`. On a
  400 whose message names one of the backend's `_ADAPTABLE_PARAMS` together
  with a refusal word (`param_compat._REFUSAL_MARKERS`), it records the
  parameter against (service, `base_url`, model) and sends again without it —
  for OpenAI's `max_tokens`, as `max_completion_tokens`. The refusal word is
  load-bearing: "max_tokens is too large" names the parameter too, and wants a
  smaller number. The record lasts as long as the process.
  Nothing checks a model by sending it a completion any more (see "Verifying
  keys and discovering models"); a refused parameter is adapted to on the
  first paragraph instead. The wording matched is OpenAI's documented one; Anthropic's has not been captured, so if a Claude model still fails on
  `temperature`, compare its message with the markers first.
- **A Claude paragraph is asked for at low effort, a chat answer is not.**
  `AnthropicTranslator._effort_kwargs` sends `output_config={"effort": "low"}`
  from `translate` and `validate_configuration`: a model that thinks by default
  (Claude Opus 5, Claude Sonnet 5) would otherwise spend its default effort on
  every paragraph of a document. `effort` is in `_ADAPTABLE_PARAMS` because
  Claude Haiku 4.5 answers 400 to it, and `param_compat._ALSO_NAMED` lets a
  refusal that names only `output_config` count for it. Haiku 4.5's message is
  not captured either, so a Haiku 4.5 run that fails every paragraph points
  here first. `generate`
  sends none, so chat keeps the model's default.
- **A reply that stopped early is a failure, not a translation.** Thinking
  counts toward the token cap, so on a model that thinks by default a cap
  sized for the reply can end it mid-sentence. Claude's `max_tokens`,
  `refusal` and `model_context_window_exceeded` stop reasons, OpenAI's
  `finish_reason == "length"` and Gemini's `MAX_TOKENS` go through
  `_handle_translation_error` in `translate` (counted, never cached) and raise
  in `generate` (the chat's `error` event). The same reason sized the RAG caps:
  1024 for HyDE, 4000 for the answer.
- **There are no model suggestions but Argos's.** A key's own listing is what
  the pickers offer, and the model field takes any name. Only Argos, whose one
  model is fixed, keeps `suggested_models`; with no listing, a provider offers
  just the model it would run, as `saved`. A retired default is caught by
  `test_the_default_model_is_not_retired`, which is how `gemini-1.5-flash`
  once stayed on offer after Google retired it.
- **A retired model in a saved config is replaced as it loads.**
  `save_settings` writes the defaults into `config.toml`, so a default that
  later went away is in every file ever saved.
  `ConfigManager._replace_retired_models` swaps any ID in
  `config/models.py:RETIRED_MODELS` for the service's current default. Add an
  ID there when its provider shuts it down. A model not on the list is left
  alone, since a local server can call its models anything.

Two smaller consequences. The chat answer model is rebuilt when the endpoint
changes (`rag_chain._answer_model` keys on it). And every listing names its
endpoint explicitly, `None` (the provider's own) included
(`routes/providers.py:resolve_probe_target`), so a check of a typed endpoint
can never fall through to the saved one.

**A keyless local server is a provider of its own** (#88). Pointed at Ollama,
the OpenAI card still needs *some* key typed: it is a keyed provider, and
without one it falls back to Argos (`capabilities.resolve_effective_service`),
the rule #32 left as it was. The Ollama entry instead has `requires_key=False`:
`has_api_key` counts it as usable with none, verify and the model list work
with no key, and its SDK is handed `placeholder_key` (OpenAI's refuses to
build a client without one; Ollama ignores it). There is no key, so the
endpoint rule has nothing to guard, and the registry refuses a key for it.
It is never a chat fallback (`priority` is `None`): a local server may not be
running, and "any LLM with a key" should mean one that answers. Chosen as the
answer or translation model, it answers like any other (`ProviderSpec.is_llm`).

**Picking a model happens in the toolbar** (`components/translation/ModelPicker.tsx`,
rules in `lib/model-choice.ts`). The service used to be a toolbar Select and the
model a Settings field, so switching models meant a trip through the sheet. Four
things about it are deliberate:

- **The button names the service that will run, not the one selected.** With
  no key, the sidecar runs Argos in place of the LLM (above), and the old
  toolbar went on showing the LLM and its model throughout. `pickerSummary`
  reads `effectiveService` and says why when the two differ.
- **A keyless provider offers "Add an API key", not its models.** Picking a
  model the sidecar can't run would bring back that same silent swap. The
  entry opens Settings → Models at that provider's card, key field focused.
  Saving a key there also selects the provider (`provider-draft.ts:
  selectsProvider`); see "Settings → Models".
- **It offers the models switched on in Settings → Models** (`enabled_models`),
  so opening it asks no server anything. The translation model (and the
  answer model) stay on offer even once switched off, so the picker can
  always show what is chosen; a provider with a key but nothing switched on
  offers the model it runs (`AppSettings.model_for`). Until #86 the picker
  listed every keyed provider's models each time it opened — OpenAI alone
  lists 100+ — and a name of one's own went through a Settings field.
- **Every provider fact comes from `GET /providers`.** Labels, short labels,
  which providers take a key or an endpoint, the fallback `priority`: nothing
  in `desktop/src` lists providers. The one id the frontend names is
  `OFFLINE_ENGINE` (`lib/translate-request.ts`), the sidecar's fallback.

**The chat header picks the answer model** (`components/chat/AnswerModelPicker.tsx`,
#87). It used to be a read-only badge: changing it meant changing the
translation model, and chat could still answer with another provider. Both
pickers are one component, `components/translation/ModelSelect.tsx` — the same
search, groups and "Add an API key" entries — and the chat one adds **Same as
translation** first, which saves `rag.answer_model = null`. Its button names
the model that will answer (`chatModel`, a copy of `rag_chain._answer_model`'s
order: `answer_model`, then the translation model, then every LLM with a key
by `priority`), so a pick whose provider has no key shows the model that
answers instead. The choice is server state, written with `PUT /config` and
never kept in Zustand. A model picked there is pinned: it stays when the
translation model changes later, which "Same as translation" doesn't.

## Settings → Models

Settings has three tabs: **Models · Cache · Chat** (#86). Models is one card per
provider from `GET /providers`, in registry order; it replaced a tab per
provider and a single Save for all of them. The card's copy — description,
"Get a key" link, the base URL placeholder and hint — is `ProviderSpec`'s
(`description`, `signup_url`, `default_base_url`, `endpoint_hint`), so a new
provider needs nothing here. The rules are `lib/provider-draft.ts`, pure and
under vitest; `components/settings/ModelsTab.tsx` only draws them.

- **Each card saves on its own.** A new key, or a key going to a new endpoint,
  is checked as it is typed — there is no Verify button. Once typing pauses
  (`AUTO_VERIFY_DELAY_MS`) the card lists the key's models
  (`POST /providers/{id}/verify`, never a completion) and opens the list to
  choose from; Save waits for that check and reuses its result rather than
  listing again, then `PUT /providers/{id}`. `autoVerifyRequest` decides what
  is checked: only what Save would check, and never a draft the sidecar would
  refuse (a half-typed URL, or a new endpoint whose saved key hasn't been
  typed again — checking that would send the key where it wasn't saved for).
  Nothing is checked while the base URL field has focus, or each pause would
  send the typed key to a half-typed host; leaving the field starts the
  check. A check answered after the field changed is discarded, and one is
  matched to the draft by the request it sent (`currentCheck`), not cleared
  per keystroke: a trailing space or slash sends the same request, starts no
  new check, and a cleared one left Save on "Checking…" for good. When the check fails
  the button becomes **Save anyway** straight away, since a local server may
  simply not be running. Switching models on and off needs no check — they come from the
  provider's own list. Cache and Chat keep their own immediate switches.
- **The endpoint rule is met before the 422.** `endpointNeedsKey` warns under
  the base URL when a saved key — an unreadable one included
  (`hasSavedKey`) — would go to a new endpoint without being typed again.
- **The model list** is the saved key's listing (`GET /providers/{id}/models`,
  fetched when the list is opened), or, once a typed key's check has run, that
  check's listing (`currentCheck`), since the catalog only describes the saved
  key. A saved key is re-checked by opening the list or Refresh, which moves
  its `key_state`; with the Verify button gone there is no other way. A switch per model;
  what is on becomes `enabled_models`. Search matches every word; the models
  the non-chat filter hid come back with "Show all"; Refresh lists past the
  catalog; *Add* takes a name no list has. Switched-on names no list has come
  first, so a name added by hand can't get lost below a hundred others.
- **A first key switches nothing on.** Its check opens the model list and
  leaves the switches to the user. `seedEnabled` used to switch on every
  suggestion the key could use — five of OpenAI's at once, on paste, once the
  check ran as the key was typed (#96) — so it and the suggestions went.
- **Saving a key can select its provider** (`selectsProvider`): only a new
  key that listed its models (never *Save anyway*), only onto a model that
  listing has — a saved `enabled_models` the new key can't run would fail
  every paragraph where Argos kept working — and only when translation is
  still on Argos, or Settings was opened from the toolbar's "Add an API key
  to use X…" for a provider with no key. Opened from the chat header's, the
  same key chooses chat's answer model instead (`selectsAnswerModel`). `PUT /config` used to promote off Argos
  by itself when a key arrived through it; `PUT /providers` leaves the choice
  to the caller, so the card makes it with `translation_model`.
- **The status line** is `statusLine`: `✓ 42 models · checked 2 min ago`, the
  provider's error, "No API key saved", an unreadable key, or "Not verified".
- **Argos** has no key and no model to choose, so it gets a card of its own:
  the language pairs it ships (from `/config/options`) and an install check.
- **A draft lives as long as its card.** Cards are built when the sheet opens
  and dropped when it closes or the tab changes; a refetch of `/providers`
  (window focus does one) never resets what is being typed. An untouched
  draft does follow its provider (`rebaseDraft`): choosing a translation
  model moves the outgoing one into its provider's `enabled_models`, and an
  idle card that kept the old list would write it back on its next Save.
