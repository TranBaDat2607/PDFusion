# Startup, the sidecar and the shell

How the sidecar starts and stays up, what the Tauri shell does around it, and
where both write their logs.

## The READY handshake

The sidecar prints **one** handshake line at startup that the Rust shell parses:
```
READY port=54213 token=Yd7Hf...G3
```
The token is then forwarded to the webview via the `sidecar://ready` Tauri event and used as the `Authorization: Bearer` header on every fetch from the React side.

The port comes from a socket the sidecar **binds, listens on, and keeps**
(`server.py:_bind_socket`), handed straight to
`uvicorn.Server(...).run(sockets=[sock])`. Picking a port by binding, reading
`getsockname()`, closing, and letting uvicorn re-bind left a window where
something else could take it. It `listen()`s there rather than leaving that to
uvicorn because READY is printed *before* `create_app()` and the lifespan run —
a client connecting in that gap should queue in the backlog, not take a
connection refused it has to know to retry.

Dropping to `uvicorn.Server(...).run()` also drops the tail `uvicorn.run()` has:
`if not server.started: sys.exit(STARTUP_FAILURE)`. `main()` repeats it. Without
it a lifespan that raises returns normally and the process exits **0** — and
since READY has already been printed, the shell would only notice by waiting out
its whole `HEALTH_TIMEOUT` and blaming `/auth/ping`.

## Import cost is a startup budget

`sidecar.rs` gives the READY line **90 s** (`READY_TIMEOUT`) and then
`/auth/ping` **30 s** (`HEALTH_TIMEOUT`). Those are sized for the PyInstaller
one-dir bootloader paging thousands of files past Defender on a first launch —
*not* for Python work. The Python side of startup is ~0.7 s and has to stay
there.

It got there by one rule, applied everywhere:

> **A package `__init__.py` re-exports only names that are free to import.**

That rule is load-bearing rather than stylistic, because importing a submodule
runs its parent's `__init__`. `processors/pdf_cache.py` is pure `sqlite3` +
`hashlib`, yet used to cost 4.9 s — `processors/__init__.py` did
`from .processor import PDFProcessor`, which is BabelDOC. Same shape in
`translators/__init__.py` (3.2 s of provider SDKs via `factory`) and
`api/__init__.py` (`from .server import create_app`, i.e. everything). So
`api/` and `rag/` re-export nothing at all; `processors/` keeps `events` and
`exceptions`; `translators/` keeps `base` and `translation_cache`.

The rest follows from it. BabelDOC and the RAG stack are imported inside the
handlers that use them (`routes/translation.py:_run_translation`,
`routes/rag.py:_build_chain`), with `from __future__ import annotations` +
`TYPE_CHECKING` so the type annotations still name them. `create_app()` is
called *after* READY is printed. Importing `api.server` went from **16.3 s to
0.7 s**, and RAG — off by default — no longer costs most users torch at all.

Two consequences worth keeping in mind:

- **The first Translate click would now pay BabelDOC's import.** Mostly it
  doesn't, because `_lifespan` starts an `engine-warm` daemon thread that
  imports `processors.processor` after READY, next to the existing
  `argos-prewarm` thread. Startup no longer *waits* on that import; it still
  *does* it. Budget **5-25 s**, not the 5 s a warm machine suggests — the two
  threads now genuinely contend for the GIL, where before the moved imports the
  prewarm thread found everything already resolved. That thread also loads the
  DocLayout model into `doc_layout_cache` (another ~3 s), but only when the
  asset is already on disk — see ["Loading the layout model once"](translation-pipeline.md#loading-the-layout-model-once-and-what-that-does-not-fix).
- **A click can still beat that thread, so every relocated import runs off the
  event loop.** These are *blocking* imports inside `async def` handlers: on the
  loop they freeze the whole sidecar — health checks, `/pdf/file` for pdf.js,
  every other job's SSE stream — for their full duration. Measured before the
  fix: a Translate click during the warm window took **18.9 s** to return its
  `job_id`, and `/health` (33 ms idle) answered once in the next 40 s. So
  `routes/translation.py:_load_engine` and `routes/rag.py:_load_document_processor`
  exist only to be called through `await asyncio.to_thread(...)`, the same way
  `_build_chain` already was. Each stays inside the `try` that reports through
  `job.finish("error", ...)`: with the import on the job path, an `ImportError`
  that once stopped the sidecar from starting is now a per-job failure, and
  without a terminal SSE event the overlay waits forever.
- **PyInstaller is fine with this.** Its modulegraph walks bytecode including
  function bodies, so a function-level `from x.y import Z` is still statically
  visible and `pdfusion-sidecar.spec` needs no new `hiddenimports`. Only truly
  dynamic imports (`importlib.import_module(<variable>)`) need an entry there.

`tests/test_sidecar_boot.py` fails if any of this regresses.

## First-run engine setup

A translation needs ~290 MB that no wheel carries: BabelDOC's DocLayout ONNX
model, a table-detection model, 34 embedding fonts, 146 cmaps and a tiktoken
encoding (cached in `~/.cache/babeldoc`), plus the Argos en→vi pack. Before
this existed they were fetched implicitly, from inside `TranslationConfig`
construction on the first chunk of the first job, behind an overlay that said
"Initializing translator" — and offline the user was told
**`BabelDOC processing error in chunk 1: 1`**.

That string is the shape of the whole problem. **Every failure in BabelDOC's
asset layer is `exit(1)`** — no message, no type. `run_one_chunk` catches
`BaseException`, and the multiplexer interpolated it: `str(SystemExit(1))` is
`"1"`. `processors/exceptions.py:babeldoc_chunk_error` is where that is now
translated into a sentence, and it deliberately drops `original_error` —
`BabelDOCError.__str__` appends it and the job layer sends `str(exc)`, so
carrying the `SystemExit` through would staple the `1` back on the end.

`engine_assets.py` is the single source of truth for "installed", consulted by
the setup flow, the `POST /translate` pre-flight and that error message, so the
three cannot disagree. Two things about it:

- **It asks less of a job than of setup.** `engine_status()` counts the whole
  183-entry manifest, because that is what an offline-first install means.
  `engine_ready()` — the pre-flight — requires only the layout model
  (`_CORE_ASSET`). Fonts and cmaps are fetched per document and per language, so
  every install predating this flow has a partial cache that has been working
  fine; requiring the full set before a job may start would refuse work those
  machines have been doing for months.
- **It counts by `stat`, never by hash.** BabelDOC's own `verify_file` hashes
  every file, and the set is ~210 MB. This runs on every `POST /translate`.
  Integrity stays BabelDOC's job — it re-verifies and re-downloads what it
  doesn't like. A zero-byte file (an interrupted download) does not count.

`api/routes/setup.py` installs them, and is **the one long job in the sidecar
that is polled rather than streamed.** Every other long job gets a `job_id`
that survives a dropped SSE connection and can be reattached to (see
["Long-running jobs"](api.md#long-running-jobs-sse-pattern)) — installing the engine can't use that
model at all, because it isn't a job with an id: it's a process-wide
singleton, one cache directory being written by at most one download,
regardless of how many browser tabs or reloads ask about it. A webview
reload has nothing to reattach *to* by `job_id`, so the next Install click
would start a second download into the same cache directory unless
something reattaches for it. `GET /setup/status` does that for free. Two
more rules it keeps:

- **Never call BabelDOC's sync wrappers** (`warmup()`,
  `restore_offline_assets_package()`). They run the coroutine through
  `assets.py:run_in_another_thread`, where `threading.excepthook` swallows
  `SystemExit` and the call returns `None` — the failure resurfaces much later
  as a `TypeError` unpacking that `None`. Only the `_async` variants, under an
  `asyncio.run` of our own, inside a `try` that names `SystemExit`.
- **Progress is observed, not reported.** Neither BabelDOC's downloaders nor
  `argostranslate`'s expose a hook, so the percentage is how much of the
  manifest has appeared on disk, and the phase is a noun the install thread
  writes as it moves (`_Phase.noun`, plain assignment — one writer, one reader).

Nothing in `engine_assets.py` or `routes/setup.py` imports babeldoc or
argostranslate at module level; both are on the boot path, and
`tests/test_sidecar_boot.py` covers both.

On the frontend, `App.tsx`'s `EngineGate` sits between the sidecar gate and the
workspace. It is also where a mid-session 409 lands: `useTranslation` sets
`engineSetupRequired` in the store, which overrides a previous "Not now" — the
user has just asked for the one thing the assets are for. The skip marker is a
`pdfusion.*` localStorage key behind pure helpers in `lib/engine-setup.ts`, the
same convention as `sidecar-recovery.ts`. Reading a PDF never waits on any of
this; only translating does.

Consequence for the boot threads: **pre-warm may materialize an asset, never
download one.** `server.py:_should_prewarm_argos` and
`routes/translation.py:_warm_translator` are gated on `argos_pack_ready()`;
`server.py:_warm_translation_engine` is gated on `babeldoc_core_ready()` before
it touches the layout model. They fire at boot and on document open, with
nothing on screen that could report a download or its failure — installing is
the setup flow's job, because that is the surface the user can watch.

The layout model is the newer half of that rule and the easier one to get
wrong, because the download hides one call down: `DocLayoutModel.load_available()`
→ `assets.get_doclayout_onnx_model_path()`, which fetches whenever `verify_file`
fails. Ungated it doesn't merely download unattended — `babeldoc.assets.download_file`
writes **in place**, with no temp + rename, and unlinks the file when the hash
doesn't match, so a boot-thread download racing the setup flow's download of
the same path can destroy the copy the user is watching a progress bar for.
`tests/test_engine_warm_gate.py` covers both directions.

## Sidecar supervision

READY isn't the end of the sidecar's lifecycle — two things watch it after
that, both in `sidecar.rs`:

- A background task polls the child every `SUPERVISOR_POLL_INTERVAL` (250 ms)
  via `try_wait()`. An exit it didn't expect emits `sidecar://exited { code }`.
  What makes it "didn't expect": `SidecarHandle` carries a `shutting_down`
  `AtomicBool`, and `shutdown()` sets it **before** taking the child out of the
  mutex — that ordering is what stops an intentional kill (`restart_app`,
  `RunEvent::ExitRequested`) from being reported as a crash. Reversing it would
  open a window where the poller observes "child gone" without yet observing
  "on purpose."
- The child is confined to a Windows Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`confine_to_job_object`), so it dies
  with this process no matter how this process dies — crash, Task Manager,
  anything short of the OS itself going down. The job handle is **never
  closed**: `KILL_ON_JOB_CLOSE` fires when the *last* handle to the job
  closes, so leaking it for the process's lifetime is what ties the child's
  life to ours. Both the poll and the job assignment are best-effort — neither
  can fail sidecar startup.

On the frontend, `useSidecar.ts` reacts to `sidecar://exited` by calling the
existing `restart_app` command — a full relaunch, not an in-place respawn, for
the same `OnceCell` reason `restart_app` itself documents. Whether it actually
does that is gated by `lib/sidecar-recovery.ts`'s `shouldAutoRestart`: a 60 s
cooldown recorded under a `localStorage` key, not in-memory state, because
`app.restart()` tears down the whole JS context a module or React variable
would otherwise live in. That timestamp is **never cleared by a successful
`ready()`** — only elapsed time clears it. Clearing it on ready was the bug in
the first version of this: a sidecar that crashes on every boot would
relaunch, reach ready, wipe its own cooldown marker, and crash-loop forever
with no way to ever reach the manual "stopped unexpectedly" screen. Leaving it
to expire on its own still gives an unrelated crash days later a fresh
attempt. A separate, one-shot `SIDECAR_RESTART_TOAST_KEY` — set right before
the restart, consumed and cleared the next time `ready()` runs — is what tells
the user their session just got reset, without reusing (and thereby
corrupting) the cooldown key for that purpose.

## Tauri shell details

- **Plugins enabled**: `opener` (open external URLs), `dialog` (file picker), `single-instance` and `window-state` — that's all. `shell` and `fs` were registered but never imported by `desktop/src`; PDFs reach the viewer over HTTP from the sidecar, and Save/Open/Reveal go through the app commands in `lib.rs`. Don't re-add a plugin "just in case": every one widens what an injected script can invoke. Same reasoning inside a plugin: the capability grants `opener:allow-open-url` + `opener:allow-default-urls` rather than `opener:default`, because that set also carries `allow-reveal-item-in-dir` — a second, unvalidated route to the reveal that `reveal_path_in_file_manager` exists to gate. The app commands call the plugin's **Rust** API (`app.opener()`), which capabilities don't apply to, so narrowing the webview's grant costs nothing. The two new plugins cost nothing there either: `single-instance` has no JS API at all, and `window-state`'s (`saveWindowState` / `restoreState`) is left ungranted — save and restore happen in Rust on window create and on exit, so the webview never needs to ask.
- **Single instance**: registered **first**, before every other plugin — a second launch has to be turned away before the rest of the app builds, or you get two windows, two sidecars, and two writers on one `pdfusion.db`, `vectors` store and cache WAL set. Its callback focuses the existing window and, if the second launch named a PDF, emits `pdfusion://open-file` so that document opens in the running app. The *first* launch's own argv is read by the `initial_file_argument` command; `App.tsx` handles both through the same `openDocument`.
- **Window**: 1400×900 default, min 1024×700 — then `tauri-plugin-window-state` restores whatever the user last left. `GUISettings.window_width/height` were deleted with it: they predate the Tauri migration and nothing ever read them. `withGlobalTauri` is off — `__TAURI_INTERNALS__` (which `lib/tauri-ready.ts` waits on) is injected regardless; the flag only adds the legacy `window.__TAURI__` global.
- **Boot screen** (`components/StartupScreen.tsx`): app-level copy ("Starting PDFusion…"), a Retry and a "Show logs folder" button. Those two are offered in the `error` branch **and**, after `SLOW_START_MS` (15 s), in `starting` — otherwise a sidecar that never comes up leaves the user on a bare spinner for the full `READY_TIMEOUT` + `HEALTH_TIMEOUT`, which is two minutes. A healthy boot reaches READY in about a second, so anything still spinning at that mark is already abnormal. Retry calls `restart_app`, which relaunches the process rather than re-spawning the sidecar — the handle is a `OnceCell` set once per process, so re-entering that lifecycle would mean two spawn paths and a window with two Python processes. `restart_app` has to repeat the exit work by hand (`save_window_state`, `cleanup_translate_temp_dirs`): `AppHandle::restart` routes through `RunEvent::ExitRequested` **only when called off the main thread**, and a synchronous command handler runs on it, so it takes the `cleanup_before_exit` branch — resource tables cleared, windows hidden, nothing else. The `ExitRequested` arm in `lib.rs` and `window-state`'s own `RunEvent::Exit` hook both stay silent. Anything added to the exit path has to be added there too. The `PDFUSION_PYTHON` hint is behind `import.meta.env.DEV`; it describes this repo's dev setup and means nothing to someone who installed the `.msi`.
- **CSP**: set in `tauri.conf.json` — `default-src 'self'` with `connect-src` widened to `http://127.0.0.1:*` (the sidecar) plus Tauri's IPC origin, `worker-src blob:` (pdf.js), and `style-src 'unsafe-inline'` (Tailwind's runtime styles). Tauri nonces its own init script, so `script-src` stays at `'self'`. It applies to the bundled app only — in `pnpm tauri dev` the page is served by Vite, which Tauri doesn't inject headers into, so **a CSP break shows up first in `pnpm tauri build`**, not in dev.
- **CORS / dev origins**: the sidecar's allowlist (`server.py:_allowed_origins`) is exactly Tauri's custom-protocol origins, plus Vite's `localhost:1420` **only in dev**. Which one applies is a two-sided handshake: the shell sets `PDFUSION_DEV_ORIGINS` to `"1"`/`"0"` from `cfg!(debug_assertions)` (`sidecar.rs:dev_origins_flag`), on both spawn paths. The sidecar must **not** decide this from `sys.frozen` alone — frozen means "PyInstaller built it", not "shipped app", and discovery prefers a staged `binaries/*.exe` over local Python, so after `build-sidecar.ps1` a `pnpm tauri dev` run pairs a *frozen* sidecar with a *Vite-hosted* webview. Getting that wrong rejects every request the app makes (preflights → `400 Disallowed CORS origin`) and looks exactly like the sidecar failing to start. `sys.frozen` remains the fallback for a sidecar started without the shell. Don't swap `debug_assertions` for `tauri::is_dev()`: that's `!cfg!(feature = "custom-protocol")` and this crate declares no `[features]`, so it's `true` even in a release bundle.
- **Sidecar lifecycle** is wired in `lib.rs::run()`'s `setup` and the `RunEvent::ExitRequested` handler kills the child process.
- **Sidecar cwd & writable paths**: the child is spawned with cwd = the data root (`sidecar::appdata_dir`), **not** the install dir — `C:\Program Files\PDFusion\` is read-only for non-admins (→ `WinError 5` on any relative-path write), and so is `/usr/lib` on Linux. `lib.rs::setup` pre-creates the data root's layout (`sidecar::ensure_appdata_layout`) before spawn so Python subsystems don't race on first-run `mkdir`.
- **Per-job translation output** is a throwaway `%TEMP%\pdfusion-translate-<rand>\` dir (not a persistent `translated_pdfs/`). It's wiped three ways: by the next job, by the Tauri `ExitRequested` handler (`sidecar::cleanup_translate_temp_dirs`), and by the FastAPI lifespan orphan sweep on sidecar startup (`server.py:_sweep_orphan_translate_dirs`, only dirs older than 1h). A re-opened translation comes from the whole-PDF cache, which is disposable too — see "Translation output lifecycle" in [translation-pipeline.md](translation-pipeline.md).
- **Drag and drop**: files dropped on the window arrive through `getCurrentWebview().onDragDropEvent` (`hooks/useFileDrop.ts`) with real paths, and open through the same `openDocument` as the picker and the command line; `DropOverlay` shows what's being dragged. That works because `dragDropEnabled` is left at its default (true), and on Windows that **disables HTML5 drag and drop inside the webview**. Anything that needs HTML5 DnD would have to turn the shell's handling off, and this path with it. `enter` is the only event that carries paths. A drag with no files at all (text dragged out of a page) is ignored rather than refused.
- **"Open with PDFusion"** is registered by `windows/installer-hooks.nsh` (`bundle.windows.nsis.installerHooks`). It writes a `PDFusion.pdf` ProgID, a `.pdf\OpenWithProgids` value and `Applications\PDFusion.exe` under `SHCTX` (HKCU for this per-user install), and removes exactly those on uninstall. **Not `bundle.fileAssociations`**: Tauri's NSIS `APP_ASSOCIATE` overwrites the default value of `Software\Classes\.pdf`, which makes PDFusion the default PDF reader on any machine where the user never picked one. The chosen file arrives as argv, through `initial_file_argument` on a first launch and the single-instance handoff otherwise. Only `pnpm tauri build` exercises the hook (`release.yml` on a tag); CI's `desktop` job never does.
- **Sidecar discovery** order (`sidecar.rs`):
  1. **The bundled build** — `BUNDLED_SIDECAR_FILENAME` resolved via `BaseDirectory::Resource`: `pdfusion-sidecar.exe`, the renamed `externalBin`, on Windows; `sidecar/pdfusion-sidecar` inside the resource directory elsewhere (rule 2 in [cross-platform.md](cross-platform.md)). Anything under `STUB_THRESHOLD_BYTES` (1 MiB) is a `--stub` placeholder and is skipped.
  2. **Local Python** (`locate_python`) — `PDFUSION_PYTHON`, then the conda envs `conda_python_candidates` knows, then PATH: `python` before `python3` on Windows, where `python3` is usually the Microsoft Store stub, and `python3` first elsewhere, where `python` may be absent or Python 2. It runs `-m desktop_pdf_translator.api.server` with `PYTHONPATH=<root>/src`.

## Logs

Both streams land in `logs/` under the data root (`%LOCALAPPDATA%\PDFusion\logs`
on Windows, `~/.local/share/PDFusion/logs` on Linux, `~/Library/Application
Support/PDFusion/logs` on macOS), rotating at 5 MB with 5 backups kept:

- **Python sidecar** → `app.log`, via `utils/logging_setup.py::configure_logging`
  — one shared, `RotatingFileHandler`-backed setup called from all three ways
  the sidecar can start: `main.py` (the bundled `pdfusion-sidecar.exe`, and a
  hand-run `python main.py`), `python -m desktop_pdf_translator.api.server`
  (what `pnpm tauri dev` actually spawns), and the `pdfusion-sidecar` console
  script (`server.py::main` directly, via `pyproject.toml`'s
  `[project.scripts]`). `main.py` calls it before importing `server.py`, so an
  import failure there still lands in `app.log` rather than a windowed app's
  nonexistent stderr; `server.py::main` calls the same function, and
  `force=True` on `logging.basicConfig` makes that second call — when both run
  in one process — a harmless no-op re-application of the same handlers.
- **Rust shell** → `shell.log`, via `tauri_plugin_log` (`lib.rs::run`),
  default level `info`, plus a stdout target so `log::info!`/`warn!` —
  including the `[sidecar stdout]` / `[sidecar stderr]` relays — still show up
  in the `pnpm tauri dev` terminal. This replaced a bare `env_logger::try_init()`
  with no file target, which meant a release build (`main.rs` sets
  `windows_subsystem = "windows"`, so there's no console) discarded that
  stream entirely — "Sidecar failed to start" had nowhere to go.
- **Webview** → also `shell.log`, through the same plugin's JS side
  (`@tauri-apps/plugin-log`, capability `log:allow-log` only).
  `lib/webview-log.ts` forwards `console.warn`/`console.error`, uncaught
  `error` events and unhandled rejections; `main.tsx` installs it before the
  first render, in the shell only. Before it the webview had no log at all: a
  viewer bug that threw inside an effect blanked the whole window while both
  log files read as a clean run, and only devtools held the stack. Three
  guards, each with a test: the sink's own console traffic is never forwarded
  (no loop), a failing IPC call is swallowed (an unhandled rejection would be
  forwarded in turn), and at most `LOG_BUDGET` messages a minute go through,
  with a "N suppressed" line after, so a warning in a render loop can't rotate
  the shell's own lines out of `shell.log`. The plugin tags each line with its
  caller, which is always the forwarder; the error's stack is in the message.

The boot-error screen's "Show logs folder" button (`open_logs_folder` in
`lib.rs`) opens this directory regardless of which file(s) exist yet.

The sidecar's bearer token is `print`ed to stdout, not logged, so it was never in
`app.log`; `sidecar.rs:redact_ready_line` keeps it out of both the dev terminal
and `shell.log`.
