# Building and packaging

The installer build, what the bundle carries and why, and the measurements
behind its size.

## Building the desktop installer

Same three steps on every platform; only the launcher's extension changes
(`.ps1` on Windows, `.sh` elsewhere — see [cross-platform.md](cross-platform.md) for why there are
launchers at all).

```bash
# 1. Stage the engine assets the installer ships (~290 MB into assets/).
#    Network + several minutes; skips whatever is already staged. Omit this
#    and the build still succeeds — it prints a WARN per missing asset and the
#    app downloads them on first run instead. This also repacks the Argos pack
#    off stanza and onto MiniSBD; see "Argos does not need torch".
conda activate pdfusion
./fetch-offline-assets.sh        # Windows: ./fetch-offline-assets.ps1

# 2. Build the standalone sidecar (PyInstaller, one-dir). NOT optional.
#    Output: dist/pdfusion-sidecar/{pdfusion-sidecar[.exe], _internal/}
#    Then staged where this platform's bundler needs it (table below).
pip install -e ".[dev]"          # ensures pyinstaller is available
./build-sidecar.sh               # Windows: ./build-sidecar.ps1

# 3. Build the Tauri bundle.
#    The beforeBundleCommand in tauri.<platform>.conf.json re-runs the
#    build-sidecar script, so this step always ships a freshly built sidecar —
#    but that hook does NOT make step 2 skippable, because it runs too late to.
#    Tauri validates `externalBin` and `resources` in its build script, i.e.
#    during the cargo build that precedes the bundle phase, while the hook runs
#    inside that phase (tauri-cli `bundle.rs`: the hook, then
#    `bundle_project`). With nothing staged, `pnpm tauri build` fails at
#    compile time and never reaches the hook that would have built it —
#    `resource path binaries\pdfusion-sidecar-<triple>.exe doesn't exist`, and
#    no PyInstaller run in the log. That is the same validation the dev-mode
#    caveat below is about, and it is what broke the first v1.1.0 release: the
#    Windows job had no staging step and died there, while Linux only survived
#    it because its `resources` entry is a glob (an empty match is allowed) and
#    its `externalBin` is empty. Both release jobs now stage `--stub`
#    placeholders first and let the hook replace them, so the real build still
#    happens once, at bundle time. The fetch-offline-assets script is NOT wired
#    into that hook: it needs the network, and a bundle step that silently
#    downloads a third of a gigabyte is the problem this staging exists to fix.
cd desktop
pnpm tauri build
# Windows → target/release/bundle/nsis/PDFusion_<version>_x64-setup.exe
# Linux   → target/release/bundle/deb/*.deb   (no AppImage — see below)
# macOS   → target/release/bundle/{macos/*.app, dmg/*.dmg}   (unverified)
```

> **Dev-mode bootstrap caveat**: Tauri's build script validates `externalBin`
> and `resources` paths at *compile time*, so `cargo check`, `pnpm tauri dev`,
> and `pnpm tauri build` all fail on a fresh checkout until the staged sidecar
> exists. If you don't want to wait for the full PyInstaller build just to
> hack on the React/Rust side, run:
>
> ```bash
> ./build-sidecar.sh --stub        # Windows: ./build-sidecar.ps1 -Stub
> ```
>
> This drops empty placeholder files wherever this platform stages. The Rust
> shell rejects anything under `STUB_THRESHOLD_BYTES` (1 MiB) and falls back to
> your local Python at runtime, so `pnpm tauri dev` works exactly like before.
> Just don't ship the stubbed installer — the bundled binary is zero bytes.

**Iterating on the installer itself?** `pnpm run tauri:build:fast` is the same
build with NSIS's compressor switched from solid LZMA to zlib through
`--config`. Measured on the same payload: the NSIS step drops from **310 s to
94 s** and the whole `pnpm tauri build` from **607 s to 344 s**, for an
installer that grows **463.9 MB → 531.4 MB**.

It is a `--config` override rather than a change to `tauri.windows.conf.json`
because the two sides of that trade fall on different people: the 68 MB is paid
by every user on every download, the four minutes by whoever is rebuilding. And
the user's side of it is small — zlib gives back about 7 s of install time (the
decompression figures are under "What the bundle carries" below). Releases keep
LZMA.

Where the build lands, and why it differs, is the subject of rule 2 in
[cross-platform.md](cross-platform.md); the short version:

| | Staged to | Shipped as |
|---|---|---|
| Windows | `src-tauri/binaries/pdfusion-sidecar-<triple>.exe` + `src-tauri/_internal/` | `externalBin` (renamed, at the install root) + a `resources` glob beside it |
| Linux, macOS | `src-tauri/sidecar/` — the whole one-dir tree | one `resources` directory |

On Windows the `_internal/` tree is staged at `desktop/src-tauri/_internal/`
(not inside `binaries/`) so that Tauri's `resources` glob installs it at
`<install>/_internal/`, sibling to the renamed `pdfusion-sidecar.exe` — which
is what PyInstaller's onedir bootloader requires to find `pythonXYZ.dll` (e.g.
`python311.dll` for this project's Python 3.11) et al. First build is slow
(~10-20 min).

**On Windows, NSIS is the only bundle target, and it installs per user.**
`bundle.targets` in `tauri.windows.conf.json` is `["nsis"]` with
`nsis.installMode: "currentUser"`, so the app lands in
`%LOCALAPPDATA%\Programs\PDFusion` with no UAC prompt. The per-machine WiX
`.msi` is gone: it was the origin of the read-only-cwd bug class the code works
around (`C:\Program Files\` is not writable for non-admins), and pushing the
thousands of `_internal/**/*` files through WiX was slow. Re-add `"msi"` to
that file's `bundle.targets` if an IT-deploy story ever needs one. Note the
install dir is now writable — that does **not** make the AppData cwd work in
`lib.rs::setup` redundant, since a machine upgraded from an MSI install is
still out there, and `/usr/lib` on Linux is not writable either.

**On Linux the only target is `deb`**, and it is not signed. `appimage` was
enabled for v1.1.0 and taken back out — see "Why there is no AppImage" below.
`bundle.linux.deb.depends` is deliberately left unset: tauri-bundler derives
`libwebkit2gtk-4.1-0` and `libgtk-3-0` itself and *appends* anything listed
there, so spelling them out again only risks the list drifting from what the
crate actually links — and adding `libayatana-appindicator3-1` by hand would
make users install a tray library this app never asks for. `rpm` is available
to Tauri and not enabled; add it to `tauri.linux.conf.json` if someone asks —
unlike `appimage`, it ships the resource tree verbatim, so it should not hit
what the AppImage hit.

On top of that, the fetch-offline-assets script stages two runtime asset sets that
the spec bundles when present (`_internal/argos_pack/`,
`_internal/babeldoc_assets/`) and warns about when absent:

| Staged path | What | Consumed by |
|---|---|---|
| `assets/argos/translate-en_vi.argosmodel` | Argos en→vi pack, ~80 MB | `argos_translator.py:_find_bundled_pack` |
| `assets/babeldoc/offline_assets_<tag>.zip` | BabelDOC layout models, fonts, cmaps, ~210 MB | `engine_assets.py:bundled_babeldoc_zip` |

Both are gitignored. `<tag>` is a hash of BabelDOC's own asset manifest, so the
zip is only valid for the babeldoc version it was built against — re-run the
script after bumping babeldoc. A stale zip is not an error: it simply isn't the
file `restore_offline_assets_package_async` looks for, and setup downloads
instead.

The HuggingFace embedding model for RAG chat (118 MB, int8
multilingual-e5-small) is still **not** bundled and still downloads on first use
to `~/.cache/huggingface` — as the ONNX file its `index_spec.EmbeddingSpec` names
+ `tokenizer.json`, fetched by `rag/onnx_embeddings.py`, rather than by
sentence-transformers.

Releases are built by `.github/workflows/release.yml` on a `v*` tag, in two
jobs — `windows-installer` and `linux-bundles` — each staging the offline
assets, running `pnpm tauri build`, and attaching its artifacts to the same
**draft** release. Two jobs rather than a matrix because only one of them has a
signing step. Signing is opt-in and Windows-only — set the
`WINDOWS_SIGN_COMMAND` secret (Azure Trusted Signing) or
`bundle.windows.certificateThumbprint` (an OV cert in the runner's store); with
neither, the job warns and ships unsigned, which is also what the Linux job
always does.

**Why there is no AppImage.** `tauri.linux.conf.json` names `deb` alone.
`appimage` was enabled for v1.1.0 and removed after it failed twice: the
AppImage bundler runs `linuxdeploy`, which walks **every** ELF file in the
AppDir and re-resolves its shared-library dependencies — and our AppDir
contains the whole ~1.2 GB PyInstaller tree under
`usr/lib/PDFusion/sidecar/_internal/`. That tree is full of wheel-vendored
libraries whose names only resolve through their own `RPATH`/`$ORIGIN` at
runtime, and the first one linuxdeploy could not account for aborted the
bundle:

```
Deploying dependencies for ELF file .../sidecar/_internal/scipy.libs/libgfortran-8f1e9814.so.5.0.0
ERROR: Could not find dependency: libquadmath-828275a7.so.0.0.0
ERROR: Failed to deploy dependencies for existing files
```

It had processed **four** of the tree's hundreds of `.so` files at that point,
so fixing that one file would only have bought the next one. This is
structural, not a bad dependency: nothing in the target lets us tell
linuxdeploy to leave a resource directory alone, and an embedded CPython
distribution is exactly what it is built to take apart. The `.deb` has none of
this problem — it ships the tree verbatim — and it is what the release
publishes. Re-enabling `appimage` means solving that walk, not retrying it.

Two things that attempt left behind and that are worth keeping. The job builds
with **`--verbose`**, because `tauri-bundler` *discards* a bundler tool's
stderr at its default log level (`linuxdeploy.rs`: "Linuxdeploy logs everything
into stderr so we have to ignore the output ourselves here") — the first
failure was the bare string `failed to run linuxdeploy`, with nothing else in
the log, and it cost a whole release run to get a cause out of. And
**`NO_STRIP=true`** is the documented workaround for the *other* linuxdeploy
failure, its `strip` choking on a `.relr.dyn` section
(`unknown type [0x13]`, tauri-apps/tauri#14796); it is not set today because
nothing runs linuxdeploy, but it is the first thing to set if anyone re-enables
the target.

## Why the Linux .deb was larger

v1.2.0 shipped a **759 MB `.deb` against a 471 MB Windows installer**, from
the same spec. Unpacked, the two trees were 1,446 MB and 834 MB. After the
changes below the same release builds to a **436 MB `.deb`** (~1.1 GB
installed). The causes, in order of size, and what now handles each:

1. **tauri-bundler dereferences symlinks** (296 MB unpacked). On Linux
   PyInstaller keeps a vendored library once, in its wheel's `<pkg>.libs/`,
   and puts a *symlink* to it at the top of `_internal/`. `build_sidecar.py:stage`
   preserves those (`copytree(symlinks=True)`), but the bundler follows every
   link when it copies `resources`, so the `.deb` carried 42 libraries twice,
   byte for byte — libctranslate2 (78 MB), two OpenBLAS builds, libmupdf, all of
   opencv's Qt and ffmpeg. Windows has no such links, and 1.7 MB of duplicates.
   → `scripts/repack_deb.py`, run by `release.yml` after `pnpm tauri build`,
   turns byte-identical copies back into relative links, keeping the one in
   the deeper directory as the real file (the direction PyInstaller links in).
   It links by **content hash**, never by name, so a few cross-wheel
   duplicates also collapse (the same `libgomp` in `ctranslate2.libs/` and
   `scikit_learn.libs/`) — the loader resolves both paths to one inode, which
   is what it did with two identical copies anyway.
2. **gzip** (~140 MB of download). The bundler writes `data.tar.gz` and has
   no setting for it; NSIS uses solid LZMA. gzip's 32 KB window also cannot
   see the duplicates above. → the same repack rebuilds with
   `dpkg-deb -Zxz -z9`. xz rather than zstd because every dpkg in support reads
   it (zstd needs dpkg ≥ 1.21.18: Debian 12, Ubuntu 21.10) and it compresses
   better. The price is install time: xz unpacks this payload in about ten
   seconds, which is what the Windows installer's LZMA already costs.
3. **Unstripped shared libraries** (139 MB unpacked). Linux wheels ship their
   symbol tables inside the `.so` — 60 MB in ctranslate2's extension alone,
   17 MB in libpython — where Windows wheels keep theirs in PDBs that are never
   collected. → `build_sidecar.py:strip_shared_libraries`, Linux only,
   `strip --strip-unneeded` on `_internal/**/*.so*`: what Debian's `dh_strip`
   does, keeping `.dynsym`. Not PyInstaller's `strip=True`, which also strips
   the bootloader the Python archive is appended to; not on macOS, where it
   breaks the ad-hoc signature arm64 dylibs carry.
4. **Libraries only the Linux environment installs** (~150 MB unpacked).
   - *opencv's Qt build* (121 MB). BabelDOC wants `opencv-python-headless`,
     its `rapidocr-onnxruntime` dependency wants `opencv-python`, and both
     write the same `cv2/` — whichever pip wrote last is what gets frozen. On
     Linux the GUI build's `cv2` has Qt5, a second ffmpeg and a second OpenBLAS
     in `DT_NEEDED`, so it cannot be pruned from the spec; the release and CI
     jobs reinstall the headless wheel instead, and `warn_on_gui_opencv` says
     so when a local build still has the Qt one. rapidocr itself is already an
     `exclude`.
   - `uvloop` and `hf_xet` (25 MB). `uvicorn[standard]` installs uvloop only
     off Windows; `loop="auto"` falls back to asyncio, which is what Windows
     runs. huggingface_hub uses `hf_xet` only if it imports and otherwise
     downloads over HTTP, which is how the chat embedding model has always
     arrived on Windows. Both are spec `excludes`.

5. **The engine-assets zip is deflated** (71 MB of download). BabelDOC
   writes `offline_assets_<tag>.zip` with `ZIP_DEFLATED`, and xz cannot improve
   on deflate output: 217 MB in, 227 MB out. `repack_deb.py:store_asset_zips`
   rewrites it `ZIP_STORED`, and the 357 MB underneath then compresses to
   156 MB. Restore is unaffected — `restore_offline_assets_package_async`
   verifies each member's sha3_256, never the archive — but it is **+129 MB on
   disk** under `/usr/lib`, the one place this trades against the user. Left
   out of `fetch_offline_assets` because the Windows installer would face the
   same trade with LZMA, and that is its own decision.

Measured on one build, each step in turn: 759 MB as shipped → 697 MB from the
build-side cuts (3, 4) → 510 MB with links and xz (1, 2) → 436 MB with the
stored zip (5).

What the user sees is unchanged: the same offline engine assets, the same
first-run setup (checked end to end on the repacked `.deb`: bundled restore,
an offline Argos translate, and a chat index), and the files PyInstaller built,
with links where it put links. Two things did move: `dpkg` unpacks xz a few
seconds slower than gzip, and the zip in (5) takes more disk.

## What the bundle carries, and what was taken out of it

`excludes` can only drop a whole importable package, and the heavy ones are all
reachable by now: babeldoc's layout parser imports cv2
(`document_il/midend/layout_parser.py`), its char extractor imports
`sklearn.cluster.DBSCAN` (`document_il/utils/extract_char.py`). (RAG
indexing imported camelot too, and pandas with it, until the chat ingestion
redesign replaced it with PyMuPDF's table finder.) So the spec also prunes *inside*
the packages it keeps: `_prune()` filters `a.binaries` and `a.datas` after
`Analysis` and prints what each rule removed, because a rule that silently
stops matching — a renamed directory, a new wheel layout — looks exactly like
nothing at all, and the place you would find out is a shipped installer.

**Bytes are what matter, not file count** — worth stating because "fewer
files" is the intuitive answer and the measurements say otherwise. NSIS does
emit one `File` directive and one `Delete` per entry (2,525 of them in the
v1.1.1 installer), but writing the whole 819 MB tree takes 1.7 s, of which
per-file overhead is ~0.55 ms; and Defender, whose on-write scan is the usual
suspect, costs ~0.4 ms per binary it has never seen — **+0.14 s across all 379
`.pyd`/`.dll`** (measured by appending a byte to each, so the copies are
unknown to it). Dropping a thousand small files buys under a second. So one
rule below pays for itself and two are merely tidy; keep that straight before
trading risk for a file count.

Measured on Windows, the staged tree went from **849.2 MB / 2,521 files** to
**818.9 MB / 2,238 files**:

| Rule | Removed | Why nothing reaches it |
|---|---|---|
| OpenCV's FFmpeg videoio backend | 28.3 MB, 1 file | Everything here hands cv2 page bitmaps; nothing constructs a `VideoCapture`. **Windows only** — there the DLL is resolved by name when that backend first initialises, so its absence just marks it unavailable, while the Linux wheel's ffmpeg lives in `opencv_python_headless.libs/libav*.so`, which cv2's own extension lists in `DT_NEEDED`. Dropping those breaks `import cv2` outright. |
| scikit-learn's sample corpora | 1.0 MB, 108 files | `data/`, `descr/`, `images/`, `tests/` — the toy sets behind `load_iris()`, their prose, two demo photographs, and sklearn's own fixtures. The only entry point this app has into sklearn is `DBSCAN`. Spelled out per directory rather than as `sklearn/datasets/*`, which would also catch `_svmlight_format_fast`, a compiled extension the package imports at load. |
| C/Cython build artefacts | 1.0 MB, 173 files | `.lib` import libraries, the `.pyx`/`.pxd` sources the shipped extensions were generated from, `.h` headers, `meson.build`. A linker reads these; an interpreter never opens one. |

Two things that look like dead weight and are not:

- **`.pyi` stubs are load-bearing.** scikit-image (a babeldoc dependency)
  builds its public namespace with `lazy_loader`, which parses
  `skimage/__init__.pyi` at *import* time to learn what to attach. Pruning
  `.pyi` fails that import with "Cannot load imports from non-existent stub".
- **hyperscan's vendored runtime.** `hyperscan` (imported by babeldoc) is a
  delvewheel-repaired wheel: `_hs_ext` links a private, hash-named copy of the
  MSVC runtime in a *sibling* `hyperscan.libs/`, which `hyperscan/__init__.py`
  registers with `os.add_dll_directory`. PyInstaller's dependency scan does not
  follow a sibling libs directory, so nothing collected it — and this went
  unnoticed because numpy and pandas are repaired the same way and their
  vendored runtime *sometimes carries the same hash*, so `_hs_ext` resolved
  through whichever libs directory another package had already registered. A
  numpy bump is enough to end that. The spec now collects it explicitly
  (`collect_delvewheel_libs_directory`).

Both failures present identically and misleadingly: a non-fatal
`Translation engine warm-up failed` warning at startup, and then the first
translate dying in `_load_engine` with `cannot import name 'PDFProcessor' from
desktop_pdf_translator.processors.processor` — because babeldoc's failed import
left a half-initialised module behind for the second importer to find.

A third reached users (#126, v1.3.0) without any prune at all. chardet is
unpinned (babeldoc asks for `>=5.2.0`) and from 7.x compiles itself with
mypyc. On Windows each compiled module is a stub `.pyd` that imports a sibling
`<name>__mypyc` extension from C, which PyInstaller's analysis cannot see.
The contrib `hook-chardet.py` only collects `chardet.pipeline`, so when 7.7.0
compiled `chardet.models._format` the same way, the release build dropped its
companion and every translate failed with "No module named
'chardet.models._format__mypyc'". The spec now collects all of `chardet`. The
build log did warn — `Hidden import "_format__mypyc" not found!` — but among a
dozen identical, harmless warnings for the hook's own wrongly-named guesses.
The Linux wheel puts all of it in one top-level `<hash>__mypyc.so` the hook
does find. Dev envs never see it: one created before 7.x keeps a pure-Python
chardet 5.

**Which is why the smoke suite checks the engine import, and why that is still
not enough to sign off a prune.** BabelDOC is imported inside the handler, not
at startup, so a sidecar that boots and answers every route can still be
unable to translate. `test_the_translation_engine_imports_in_the_running_sidecar`
reads the `engine-warm` thread's verdict from `app.log` and fails on the
warm-up warning, which catches all three of the above. Its first CI run also
found a fourth, in source mode on both platforms: bitstring 5.0 removed
`BitStream`, which babeldoc 0.5 imports while requiring only `>=4.3.0`, so
v1.3.0 could not translate on Linux either. `requirements.txt` and
`pyproject.toml` cap it below 5. Because CI's `python` job runs the smoke suite
in source mode on every PR, a dependency release that breaks the engine
import now fails a PR instead of a release. It proves the engine
*imports*, not that it *runs*: a missing data file or model is only found by a
real translation through the frozen exe — spawn
`dist/pdfusion-sidecar/pdfusion-sidecar.exe`, `POST /translate` with `service:
"argos"` (offline, no API key) and watch the SSE stream terminate in `done`
with `failed_paragraphs: 0`.

What is left is left on purpose: `babeldoc_assets` (228 MB) and `argos_pack`
(67 MB) are the offline-first installer's whole point; `cv2.pyd` (71 MB),
`ctranslate2` (62 MB), `chromadb_rust_bindings` (63 MB), scipy and its libs
(76 MB) are single files or hard dependencies of one. The largest remaining
*file-count* item was camelot's chain — pandas, pytz and tzdata, 1,209 files
for 19 MB. It left as a product decision, not a packaging one: chat ingestion
(`rag/ingest.py`) reads tables with PyMuPDF, and nothing imports camelot. The
same redesign put RapidOCR *in* (16 MB of ONNX models, `collect_data_files`),
which the spec used to exclude as unused.

**Why the installer is the size it is**, and why little else here will change
it: those two asset blobs are already-compressed archives that pass through any
compressor at roughly 1:1, so they are 295 MB of the 819 MB tree and **64% of
the 464 MB installer**. That is also why solid LZMA's extra four minutes buy so
little over zlib (67.5 MB on the whole bundle). Where the choice does show is
install time, and not by much: on this payload LZMA decompresses at ~82 MB/s
and zlib at ~288 MB/s, so ~10 s against ~3 s for the whole tree, on top of
~1.7 s of file writes. A Windows install is therefore something like a dozen
seconds of real work, most of it LZMA — the gap against `dpkg`, which unpacks
a gzip `data.tar`, is mostly that one number. If download size ever matters
more than offline-first, the lever is not pruning site-packages but not
shipping the assets: `fetch-offline-assets` is already optional, and skipping
it is a ~170 MB installer that downloads ~295 MB on first run instead.

Hidden-import additions for chromadb / babeldoc / etc. live in
`pdfusion-sidecar.spec`, which is itself platform-neutral — PyInstaller adds the
`.exe` suffix only on Windows, and the excludes name packages that simply aren't
installed elsewhere. Extend that file (then rerun the build-sidecar script) when
the bundled sidecar raises `ModuleNotFoundError` at startup.

## Argos does not need torch

The offline translator used to drag in 590 MB of tensor library, and the reason
is one line: `argostranslate/sbd.py` does an unguarded top-level `import stanza`,
`argostranslate/translate.py` imports from `sbd`, and `import stanza` pulls both
torch and transformers. Worse, the upstream en→vi pack ships
`stanza/en/tokenize/ewt.pt`, a torch checkpoint — so `StanzaSentencizer` was
genuinely selected and torch genuinely used, not merely imported.

Three changes remove it, and all three are needed:

- **The pack carries MiniSBD instead.** `fetch-offline-assets.ps1` repacks the
  staged `.argosmodel`: `stanza/` out (0.75 MB), `minisbd/en.onnx` in (0.19 MB,
  onnxruntime). The repack is idempotent, so it also fixes a pack staged before
  this existed, and it writes through a sibling temp file + `os.replace`.
- **The splitter is pinned, not merely preferred.**
  `argos_translator._configure_argos_settings` sets
  `settings.chunk_type = ChunkType.MINISBD`. Assignment rather than the
  `ARGOS_CHUNK_TYPE` env var, which `argostranslate/settings.py` reads at
  *module import* time — `PackageTranslation.__init__` reads `chunk_type` per
  translation, so the assignment holds whatever the import order, and whatever
  sbd model a runtime-downloaded pack happens to carry.
- **The import is satisfied by an empty module.**
  `translators/_sbd_compat.install_stanza_stub()` registers a bare
  `ModuleType("stanza")` when the real one is absent. `stanza.Pipeline` is only
  touched inside `StanzaSentencizer.lazy_pipeline`, which is now never
  constructed, so nothing more is needed. It is a no-op when stanza *is*
  installed — which it always is in dev, because argostranslate hard-requires
  it — so dev and the bundle differ only in whether the import is real.

`pdfusion-sidecar.spec` then excludes `stanza`, `torch`, `transformers` and
`sentence_transformers`. Measured like-for-like on the same commit and the same
staged assets, that is **1352 MB → 857 MB unpacked (-495 MB)** and a
**98.7 MB → 43.8 MB** sidecar exe. Two consequences:
**`tests/test_sidecar_smoke.py`'s frozen half is the only check that can catch
an over-exclusion**, and `FORBIDDEN_AT_BOOT` in `test_sidecar_boot.py` still
lists torch and stanza — the dev env has them (argostranslate hard-requires
stanza, which requires torch), the bundle does not. `transformers` and
`sentence_transformers` are *not* on that list: nothing requires them any more,
so a clean install doesn't have them to import, and the list's companion test
requires every name on it to be installed.

RAG embeddings had to move off torch in the same change or the excludes would
have broken Chat: `rag/onnx_embeddings.py` runs the *same*
`paraphrase-multilingual-MiniLM-L12-v2` weights (multilingual, 384-dim, chosen
for Vietnamese) through onnxruntime + tokenizers, reimplementing the only two
things sentence-transformers did here — tokenize, then mean-pool over the
attention mask, per the model's `modules.json`. The feed is built from
`session.get_inputs()` rather than hardcoded, so a re-export with a different
signature still works. The ~470 MB first-use download was unchanged then; the
switch to int8 multilingual-e5-small (`rag/index_spec.py`, measured in
`benchmarks/rag/`) later cut it to 118 MB.

## Two dependency facts not worth re-deriving

- **Both OpenCV wheels are installed, and neither is ours to choose.**
  `opencv-python-headless` is a direct BabelDOC requirement and
  `opencv-python` comes from `rapidocr-onnxruntime`, itself a hard BabelDOC
  requirement. They install to the same `cv2` package and PyInstaller collects
  it once, so there is nothing a pin can fix — which is why the Linux release
  reinstalls the headless wheel instead (see "Why the Linux .deb was larger"
  above).
- **torch is still installed in a dev/CI env** even though the bundle excludes
  it: argostranslate hard-requires `stanza==1.10.1`, which requires torch. See
  "Argos does not need torch" above.
