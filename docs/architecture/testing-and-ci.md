# Tests and CI

What the suites cover, the conventions that keep the default run fast, and what
CI gates.

- **What is covered, and what still isn't.** The PDF-export path, the language
  contract, key storage and config read/write, the config API's endpoint and
  key rules, the job registry, both SQLite caches, Argos's batching,
  translator failure/retry accounting and per-model parameter adaptation, the records
  database, and chat's document isolation (`test_rag_isolation.py` runs a real ChromaDB under
  `tmp_path` with a deterministic embedding function, so nothing downloads).
  So are chat's ingestion (real PDFs built with PyMuPDF), OCR respacing, rank
  fusion and page evidence, citation checking, page images, multimodal
  `generate()` per SDK and the answer chain's image and citation rules
  (`test_rag_*.py`, `test_multimodal_generate.py`).
  Splitting a document into chunks and assembling the rolling PDF are covered
  against real PDFs (`test_pdf_pages.py`).
  Cross-platform (#69), `test_storage_paths.py` covers the data root per
  platform — the marked cases run only where they apply, the shell-override
  ones run everywhere — `test_config_security.py` covers the keystore scheme
  through a stand-in backend, and `test_packaging_config.py` holds together the
  three files that have to agree about where the sidecar is staged, shipped and
  found.
  Still uncovered: the BabelDOC pipeline in `processors/processor.py` proper,
  and chat *quality* with the real embedding and OCR models and a real LLM —
  that is what `benchmarks/rag/` measures, so run it after touching `rag/`. On
  the
  frontend, the PDF viewer's pure half is covered (`lib/pdf-viewer/`: geometry,
  find matching, the artifact change log, shortcut mapping). Its DOM half is
  not (`page-renderer.ts`, text layers, find highlighting, drag and drop),
  because vitest runs in node with no DOM. Exercise that half in
  `pnpm tauri dev`.

  **Everything in the default run is in-process, and that is a property the
  suite defends rather than a happy accident.** It finishes in ~10 s, most of
  which is `test_sidecar_boot.py`'s subprocess probes and the provider SDKs
  `test_translation_resilience.py` imports on purpose. It used to cost ~13 s
  for a far smaller suite, because importing **anything** under
  `desktop_pdf_translator.api` pulled in BabelDOC and torch; see ["Import cost
  is a startup budget"](startup-and-shell.md#import-cost-is-a-startup-budget)
  for the rule that fixed it. `test_sidecar_boot.py` is the guard — it asserts in a subprocess that importing `api.server` leaves
  torch, chromadb, stanza, BabelDOC, sklearn and RapidOCR out of `sys.modules`.
  Every name on that list has to be **installed**, which a second test enforces:
  an uninstalled one cannot be imported at boot, so guarding it proves nothing
  and only hides a typo. That is why `transformers` and `sentence_transformers`
  left the list when the excludes took them out of the dependency tree, and
  `camelot` when chat's ingestion stopped using it, while torch and stanza
  stayed. If it goes red, the desktop app's startup
  is what broke; a slow suite is only the symptom you notice first.

  Two conventions that keep it that way, both worth preserving:

  - **A cache test builds its own cache under `tmp_path`.** Every storage class
    here is a process-wide singleton over the app's data root, so a test that
    reaches for `get_pdf_cache()` gets the shared one. The same applies to
    settings: `_refresh_cap_from_settings` and `_cache_enabled` are stubbed
    rather than allowed to find a `config.toml`. Behind that convention sit
    **two** backstops in `tests/conftest.py`, both process-wide and both there
    because a test that looks isolated may not be. It sets `PDFUSION_DATA_DIR`
    (and `%LOCALAPPDATA%`) to a throwaway folder with an empty `config.toml` at
    import time, so `appdata_dir()` can never resolve the developer's real data
    root during a run — added after a default run during #59 migrated a real
    paragraph cache through a path no single test owned. And an autouse fixture
    answers "no keystore here" for `utils/encryption.py`, so a run off Windows
    never creates or reads the developer's live `PDFusion /
    config-encryption-key` entry in their login keyring (#69);
    `test_config_security.py`'s `fake_keystore` overrides it for the tests that
    are about the keystore. Don't remove either.
  - **`tests/test_sidecar_smoke.py` is marked `smoke` and deselected by
    `addopts`.** It is the one suite that spawns real interpreters. It runs
    twice — once against `python -m desktop_pdf_translator.api.server`, once
    against the staged PyInstaller exe, which *skips* when none is built. The
    frozen half is the only thing that can catch a `ModuleNotFoundError` from
    the spec's `excludes` list, or a module the bundle never collected. Both
    halves also wait for the `engine-warm` thread's verdict in `app.log` and
    fail if BabelDOC cannot be imported (#126), which a sidecar that boots and
    answers every route can still be. The frozen half passes against a
    **stale** staged exe, which reads identically. Check the exe's timestamp
    before believing it.
- **`ruff` is the lint gate**, declared in
  `pyproject.toml [project.optional-dependencies].dev` and configured under
  `[tool.ruff]`. It runs its default rule set — pycodestyle errors plus
  pyflakes (`E4`, `E7`, `E9`, `F`) — and the tree is clean, so `ruff check src
  tests scripts` is expected to pass. `scripts/` is on that list because the
  build and asset-staging logic moved there out of the PowerShell here-string
  it used to live inside (#69), where nothing could lint it at all. It is deliberately not widened: line length is
  left to `black`, which is *not* wired into CI, because reflowing the existing
  prose comments would bury every real diff. black / isort / flake8 / mypy stay
  installed for local use and are not gates. There is no pre-commit and no
  Makefile.
- **TypeScript** is checked by `pnpm build` (which runs `tsc` before `vite build`). There is no separate lint step (no ESLint config).
- **CI** is `.github/workflows/ci.yml`, on every PR and push to `main`. Two
  gating jobs, each a matrix over **`windows-latest` and `ubuntu-latest`**
  (#69): `python` (`ruff check` → `pytest` → regenerate + diff-check
  `openapi.json` → `pytest -m smoke`) and `desktop` (`pnpm test` → check
  `api-types.d.ts` is current → `pnpm build` → stage sidecar stubs →
  `cargo check --all-targets` → `cargo test`).

  Both runners, not one standing in for the other, because each platform is the
  only place its own half is exercised: Windows has DPAPI, `%LOCALAPPDATA%` and
  the Job Object; Linux has the XDG root, the keystore *fallback* (no Secret
  Service on a runner), `$HOME`-shaped interpreter discovery, and a `cargo
  check` with `windows-sys` gated out of the dependency graph entirely. macOS
  is deliberately absent — see "Out of scope" in [README.md](README.md#out-of-scope).

  Three things about it that are easy to get wrong on a rewrite:

  - **The Python job installs `requirements.txt`, not just the package.** The
    two are not in lockstep on purpose, and
    `test_sidecar_boot.py:test_every_forbidden_name_is_a_real_module` asserts
    torch / chromadb / RapidOCR & co. are *installed but not imported* — a
    partial install turns the boot guard into a pass for the wrong reason.
  - **The desktop job runs `pnpm build` before touching cargo.** Tauri's build
    script validates `externalBin` and `resources` at compile time and needs
    `frontendDist` (`desktop/dist/`) to exist, so `cargo check` fails on a
    fresh checkout until both the frontend is built and a sidecar is staged.
    `--stub` covers the second in seconds.
  - **The OpenAPI diff-check runs on Windows only.** It is a `git diff` against
    a checked-in file; running it on both runners would gate the same bytes
    twice, and `desktop/src/lib/openapi.json` is already pinned to LF in
    `.gitattributes` precisely because that comparison is newline-sensitive.
    The Linux runner *does* install webkit2gtk before anything cargo touches —
    without it the crate does not link, and the failure names a missing
    `webkit2gtk-4.1.pc` rather than anything about PDFusion.

  A third job, `frozen-sidecar`, runs the real PyInstaller build and then the
  smoke tests against the built binary, on both platforms. It is
  `workflow_dispatch` only: it costs 10-20 minutes per platform, and it is the
  check to run before cutting an installer or after changing
  `pdfusion-sidecar.spec` — and the only thing that settles whether the spec
  produces a *working* Linux sidecar.
