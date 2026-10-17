# PDFusion architecture notes

The long form of `CLAUDE.md`: the reasoning behind the invariants, the bugs
that produced them, and the measurements that justify the constants.
`CLAUDE.md` is the short index loaded into every Claude Code session; these
files are what you read when you are about to change one of the things it
warns about. Commands, setup, the module layout and the stack are in
`CLAUDE.md` and [`docs/development.md`](../development.md), and are not
repeated here.

One file per area, each small enough to read whole:

| File | Read it before touching |
|---|---|
| [startup-and-shell.md](startup-and-shell.md) | The READY handshake, import cost on the boot path, first-run engine setup, sidecar supervision, the Tauri shell (plugins, single instance, CSP, CORS, cwd, discovery, "Open with"), logs |
| [api.md](api.md) | The generated OpenAPI types, the SSE job pattern, the parts of the HTTP contract the schema can't express |
| [cross-platform.md](cross-platform.md) | The data root, where the sidecar is staged per platform, per-platform Tauri config, the build launchers, `windows-sys` |
| [config-and-keys.md](config-and-keys.md) | How keys are encrypted and preserved, where config comes from, the v2 `[providers.<id>]` shape and its conversion |
| [providers.md](providers.md) | The provider registry, adding a provider, verifying keys and listing models, endpoints and parameter adaptation, the model pickers, Settings → Models |
| [translation-pipeline.md](translation-pipeline.md) | Output lifecycle, the shared layout model, font hash memoization, rolling-PDF pruning, page ranges, font subsetting, the two caches |
| [translators.md](translators.md) | Language pairs and capabilities, failure counting, the BabelDOC translator interface, backoff and rate limiting, cancellation |
| [data-and-chat.md](data-and-chat.md) | The three stores and their migrations, chat isolation and indexes, ingestion and OCR, retrieval, the multimodal answer and its citations, chat's frontend state |
| [pdf-viewer.md](pdf-viewer.md) | Slot geometry, canvas and decode retention, page swaps and the translation mask, text layers, shortcuts, pdf.js runtime assets, font registration |
| [packaging.md](packaging.md) | The installer build, NSIS and `.deb` targets, why there is no AppImage, the `.deb` size, what the spec prunes, Argos without torch |
| [testing-and-ci.md](testing-and-ci.md) | What is covered, the in-process suite and its backstops, the smoke suite, ruff, the CI matrix |

When you add a note, put it beside its neighbours. Refer to a section in
another file by its heading and file (`"Chat answers" in data-and-chat.md`),
and to code by `file:function`, never by line number — line numbers were the
first thing to go stale here.

## Out of scope

For a later phase:

- **Auto-update** flow.
- **A signing certificate.** The plumbing exists — `bundle.windows` in
  `tauri.windows.conf.json` carries `digestAlgorithm`/`timestampUrl` and
  `release.yml` reads a `WINDOWS_SIGN_COMMAND` secret — but no certificate is
  configured, so shipped installers are unsigned and SmartScreen warns on first
  install. The Linux `.deb` is unsigned too, with no plumbing at all: signing
  it wants a GPG key in the workflow.
- **macOS packaging and verification** — the plumbing is in place (a `dmg`/`app`
  target in `tauri.macos.conf.json`, Keychain-backed keys, the
  `~/Library/Application Support` data root), but nobody has run it on a Mac
  and no artifact is published: an unsigned, un-notarized `.dmg` is refused by
  Gatekeeper with no useful way past it. That is Phase B of #69, and it needs
  hardware — a `macos-latest` runner can prove it builds, but nothing headless
  validates WKWebView, the Keychain prompt or notarization.
- **A Job Object equivalent off Windows.** The Windows child dies with the shell
  however the shell dies, because of `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. On
  Linux and macOS a `SIGKILL` of the shell orphans the sidecar; the supervisor
  poll and `ExitRequested` only cover an orderly exit.
  `prctl(PR_SET_PDEATHSIG)` is the Linux analogue.
- **i18n of the UI strings** (the UI itself stays English; the translation
  *output* follows the toolbar's target language).
- **More Argos language pairs** — the offline backend ships en→vi only. Adding
  a pair means shipping/downloading its pack, then extending Argos's
  `supported_pairs` in `providers/registry.py`.
- **Pre-bundled ML assets for RAG** — the HuggingFace embedding model (118 MB)
  still downloads on first Chat use, with no progress and no preflight. The
  translation side of that problem is solved (see "First-run engine setup" in
  [startup-and-shell.md](startup-and-shell.md)); Chat's half of issue #21 is
  deliberately left for a follow-up.
- **Synchronized scrolling between the Original and Translated panes** — the
  first item in issue #30, left out on purpose when the rest of that issue
  shipped. The panes scroll and zoom independently. `lib/pdf-viewer/layout.ts`'s
  `scrollAnchor` / `offsetForAnchor` (page + fraction) are the pieces a sync
  would build on.
- **Auto-save preference** — saving a translation is an explicit action (Save
  dialog). A "always save `<name>_vi.pdf` beside the source" setting was
  proposed in issue #11 but deliberately not built: it needs a config field, a
  Settings control, and an overwrite policy for repeat runs.
