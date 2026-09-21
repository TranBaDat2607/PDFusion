# PyInstaller spec for the PDFusion FastAPI sidecar.
#
# Build with:
#     pyinstaller pdfusion-sidecar.spec --clean --noconfirm
#
# Output: dist/pdfusion-sidecar/{pdfusion-sidecar[.exe], _internal/}.
#
# The spec itself is platform-neutral -- PyInstaller adds the .exe suffix only
# on Windows, and the excludes below name packages that simply aren't installed
# elsewhere. What differs is where the result is *staged*, which
# scripts/build_sidecar.py owns: an `externalBin` plus a sibling `_internal/`
# resource on Windows, one resource directory holding both on Linux and macOS
# (#69). Run it through ./build-sidecar.ps1 or ./build-sidecar.sh rather than
# invoking pyinstaller by hand.

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_submodules,
    copy_metadata,
)

block_cipher = None


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# uvicorn / starlette / fastapi pick implementations at runtime via importlib,
# which PyInstaller's static analysis misses. Extend this list whenever a build
# of the bundled exe raises ModuleNotFoundError at startup.
hiddenimports = [
    # uvicorn runtime selection
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",

    # FastAPI / SSE
    "sse_starlette",
    "sse_starlette.sse",

    # pydantic v2 stack
    "pydantic_settings",

    # chromadb dynamic backends
    "chromadb.api.rust",
    "chromadb.db.impl.sqlite",
    "chromadb.execution.executor.local",
    "chromadb.segment.impl.manager.local",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.utils.embedding_functions",

    # RAG embeddings (rag/onnx_embeddings.py)
    "onnxruntime",
    "tokenizers",

    # Argos translate (lazy-imported inside argos_translator). Use the explicit
    # entries below as a fail-safe; the collect_submodules call further down
    # picks up the rest (sbd, settings, fewshot, apis, models, utils, …) which
    # our new native CTranslate2 batching path reaches into directly.
    "argostranslate",
    "argostranslate.package",
    "argostranslate.translate",
    "argostranslate.settings",
    "argostranslate.sbd",

    # tiktoken loads encoding constructors from a *separate* top-level
    # namespace package (`tiktoken_ext`) via `pkgutil.iter_modules` — see
    # tiktoken/registry.py:_available_plugin_modules. PyInstaller's static
    # analysis can't see these, so the encodings have to be force-bundled.
    # babeldoc imports tiktoken at module load, so this is hit during startup.
    "tiktoken",
    "tiktoken.core",
    "tiktoken.model",
    "tiktoken.registry",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
]

# BabelDOC has a deep submodule tree (layout parser, fonts, etc.) — pull all of
# it in rather than tracking import errors one by one.
hiddenimports += collect_submodules("babeldoc")
# bitstring (transitive via babeldoc) picks a backend implementation
# (`bitstore_bitarray` vs `bitstore_tibs`) via `importlib.import_module` at
# package init time — PyInstaller's static analyzer can't see it.
hiddenimports += collect_submodules("bitstring")
# ctranslate2 (lazy-imported by argostranslate.translate) ships its inference
# kernel as a C extension; the Python package has small dynamic loaders that
# PyInstaller occasionally misses.
hiddenimports += collect_submodules("ctranslate2")
# argostranslate has multiple lazy-imported submodules (sbd, settings, apis,
# models, fewshot, utils) reached only at runtime — including from our own
# code's CTranslate2 fast path. Pull the whole package in.
hiddenimports += collect_submodules("argostranslate")
# MiniSBD is the sentence splitter argostranslate is pinned to
# (argos_translator._configure_argos_settings), and the repacked en→vi pack
# carries its 0.19 MB onnx model. It replaces stanza, which is excluded below.
hiddenimports += collect_submodules("minisbd")
# huggingface_hub has a `_LazyImporter` in its package __init__ that resolves
# attributes through `importlib.import_module` — babeldoc's asset layer and
# rag/onnx_embeddings.py's hf_hub_download both go through it.
hiddenimports += collect_submodules("huggingface_hub")
# chromadb has dynamic plugin loading throughout.
hiddenimports += collect_submodules("chromadb")
# `keyring` finds its backends through entry points, which means both the
# submodules (never statically imported) and the dist-info that lists them.
# Only installed off Windows, where DPAPI needs no package -- hence the guard
# rather than an unconditional collect that would fail the Windows build.
try:
    hiddenimports += collect_submodules("keyring")
    # The Secret Service client and its pure-python D-Bus transport. Both are
    # keyring's Linux dependency chain; absent on Windows and macOS.
    for _optional in ("secretstorage", "jeepney"):
        try:
            hiddenimports += collect_submodules(_optional)
        except Exception:  # noqa: BLE001 — not this platform's backend
            pass
except Exception:  # noqa: BLE001 — Windows, where keyring is not installed
    pass


# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
# BabelDOC ships fonts, layout-model configs, and template assets next to its
# package — those must be copied into the bundle or runtime calls will fail.
datas = []
datas += collect_data_files("babeldoc")
datas += collect_data_files("minisbd")
# tokenizers has small package data (vocab fallbacks) it expects on disk.
datas += collect_data_files("tokenizers")
# tiktoken_ext.openai_public references encoding files that tiktoken downloads
# on first use (cached under %LOCALAPPDATA%\tiktoken\). It still needs the
# plugin .py files at import time, which collect_submodules handles, but bundle
# any package data too for safety.
datas += collect_data_files("tiktoken_ext")

# Some libraries query package metadata at runtime (PEP 566). PyInstaller
# strips dist-info by default unless asked; copy the ones we know look it up.
for pkg in (
    "openai",
    "anthropic",
    "google-genai",
    "chromadb",
    "fastapi",
    "uvicorn",
    "babeldoc",
    # Entry-point metadata, not just package data: this is how `keyring`
    # discovers which backends exist at all.
    "keyring",
):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001 — best-effort; package may not be installed locally
        pass

# Argos en→vi language pack pre-bundled so the bundled exe doesn't have to
# download ~80 MB on first translate. The pack lives under
# `_internal/argos_pack/` after install; argos_translator.py:_find_bundled_pack()
# resolves it via sys._MEIPASS at runtime. If the asset is missing from the
# checkout the build silently omits it and the runtime falls back to the
# network download path, so this is fully optional.
import glob as _glob
import os as _os
# SPECPATH, not a bare relative path. PyInstaller resolves `scripts` and `datas`
# against the spec's directory, but plain Python like the exists() check below
# runs against the *current* directory -- and Tauri's beforeBundleCommand builds
# from desktop/. A relative literal here silently reports the pack as missing
# even when it is sitting in the checkout.
_argos_pack = _os.path.join(SPECPATH, "assets", "argos", "translate-en_vi.argosmodel")
if _os.path.exists(_argos_pack):
    datas += [(_argos_pack, "argos_pack")]
else:
    print(
        f"WARN: {_argos_pack} not found — the bundled sidecar will download "
        "the Argos pack on first translate. Run ./fetch-offline-assets.ps1 "
        "(Windows) or ./fetch-offline-assets.sh (Linux, macOS) to ship an "
        "offline-first installer."
    )

# BabelDOC's layout models, embedding fonts and cmaps, pre-packaged as its own
# offline-assets zip. `api/routes/setup.py` restores it into ~/.cache/babeldoc
# on first run instead of pulling ~210 MB from GitHub mirrors; without it the
# app downloads (see engine_assets.bundled_babeldoc_zip). Same optionality as
# the Argos pack above — absent is a WARN, not a build failure.
#
# The filename carries a hash of BabelDOC's asset manifest, so glob rather than
# name it: a zip built against a different babeldoc version simply won't match
# what restore_offline_assets_package_async looks for, and the runtime falls
# back to downloading.
_babeldoc_assets_dir = _os.path.join(SPECPATH, "assets", "babeldoc")
_babeldoc_zips = _glob.glob(_os.path.join(_babeldoc_assets_dir, "offline_assets_*.zip"))
if _babeldoc_zips:
    datas += [(sorted(_babeldoc_zips)[0], "babeldoc_assets")]
else:
    print(
        f"WARN: no offline_assets_*.zip in {_babeldoc_assets_dir} — the bundled "
        "sidecar will download BabelDOC's ~210 MB of layout models and fonts on "
        "first run. Run ./fetch-offline-assets.ps1 (Windows) or "
        "./fetch-offline-assets.sh (Linux, macOS) to ship an offline-first "
        "installer."
    )


# ---------------------------------------------------------------------------
# Vendored DLLs
# ---------------------------------------------------------------------------
# hyperscan (babeldoc imports it) ships as a delvewheel-repaired wheel: its
# `_hs_ext` extension links a private, hash-named copy of the MSVC runtime that
# lives in a *sibling* `hyperscan.libs/` directory, and `hyperscan/__init__.py`
# registers that directory with `os.add_dll_directory` before importing the
# extension. PyInstaller's dependency scan does not follow a sibling libs
# directory, so nothing put it in the bundle.
#
# What hid this is that numpy and pandas are repaired the same way, and their
# vendored runtime sometimes carries the *same* hash — so `_hs_ext` resolved
# through whichever libs directory another package had already registered.
# When it does not match (a numpy version bump is enough), the failure is
# "DLL load failed while importing _hs_ext", logged only as a non-fatal
# warm-up warning; the first translate then dies in `_load_engine` with a
# misleading `cannot import name 'PDFProcessor'`, because babeldoc's import
# left a half-initialised module behind.
#
# Inert off Windows, where delvewheel is not used and the helper returns
# nothing.
binaries = []
datas, binaries = collect_delvewheel_libs_directory("hyperscan", datas=datas, binaries=binaries)


# ---------------------------------------------------------------------------
# Excludes
# ---------------------------------------------------------------------------
# Drop heavy transitive deps we don't actually use. Cuts ~500 MB off the
# unpacked bundle (~180 MB off the MSI). Each entry was verified by grepping
# src/ — none appear in any import statement in our code. If a runtime
# ModuleNotFoundError mentions one of these on the bundled exe, remove just
# that single entry and rebuild.
excludes = [
    # --- Stdlib / generic noise ---
    "tkinter",
    "matplotlib",
    "IPython",
    "notebook",
    "jupyter",
    "scipy.misc",

    # --- Old PySide6 GUI (removed in commit 139d977) ---
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
    "qfluentwidgets",
    "qframelesswindow",
    "QtAwesome",
    "qtawesome",
    "QtPy",
    "qtpy",
    "darkdetect",

    # --- Deep-search / web-research feature (removed in commit 35bca2c) ---
    "selenium",
    "trio",
    "trio_websocket",
    "wsproto",
    "outcome",
    "scholarly",
    "arxiv",
    "bibtexparser",
    "feedparser",
    "free_proxy",
    "googlesearch",
    "fake_useragent",
    "newspaper",
    "feedfinder2",
    "sgmllib3k",

    # --- LangChain dead weight (RAG uses chromadb directly) ---
    "langchain",
    "langchain_community",
    "langchain_core",
    "langchain_text_splitters",
    "rank_bm25",
    "faiss",
    "faiss_cpu",

    # --- Heavy transitive NLP stacks (pulled by langchain_community integrations we don't use) ---
    "spacy",
    "thinc",
    "blis",
    "cymem",
    "preshed",
    "srsly",
    "wasabi",
    "catalogue",
    "confection",
    "weasel",
    "langcodes",
    "underthesea",
    "underthesea_core",
    "python_crfsuite",
    "jieba",

    # --- The torch stack: ~590 MB, and nothing here needs it ---
    # `argostranslate.sbd` does `import stanza` at module load and stanza
    # imports torch, which is how a 466 MB tensor library ended up on the
    # offline-translate path. Two changes let all of it go: the sentence
    # splitter is pinned to MiniSBD (onnxruntime) and the shipped en→vi pack
    # carries its model, and `translators/_sbd_compat.install_stanza_stub()`
    # satisfies that unconditional import with an empty module. RAG embeddings
    # moved to onnxruntime too (rag/onnx_embeddings.py), which is what freed
    # transformers and sentence-transformers.
    # `tests/test_sidecar_smoke.py` against the built exe is what proves this.
    "stanza",
    "torch",
    "torchvision",
    "torchaudio",
    "transformers",
    "sentence_transformers",
    "accelerate",
    "safetensors",

    # --- OCR libraries (not used by current pipeline) ---
    "rapidocr_onnxruntime",
    "pytesseract",

    # --- Docs / dev / lint / test tooling ---
    "sphinx",
    "sphinx_rtd_theme",
    "sphinxcontrib",
    "sphinxcontrib_applehelp",
    "sphinxcontrib_devhelp",
    "sphinxcontrib_htmlhelp",
    "sphinxcontrib_qthelp",
    "sphinxcontrib_serializinghtml",
    "sphinxcontrib_jsmath",
    "sphinxcontrib_jquery",
    "alabaster",
    "docutils",
    "snowballstemmer",
    "roman_numerals",
    "black",
    "flake8",
    "mypy",
    "ruff",
    "isort",
    "pytest",
    "pytest_cov",
    "coverage",
    "build",
    "pyproject_hooks",

    # --- pywin32 GUI/COM bits (sidecar only needs the core, not Pythonwin) ---
    # Inert off Windows, where pywin32 isn't installed at all: an exclude for a
    # package PyInstaller never sees is a no-op, not an error.
    "win32com",
    "Pythonwin",

    # --- Old google-generativeai REST discovery stack (replaced by google-genai gRPC) ---
    "googleapiclient",
    "googleapiclient.discovery_cache",
    "google_api_python_client",
    "google_api_core.api_discovery",
    "uritemplate",
    "httplib2",
    "google_auth_httplib2",
]


a = Analysis(
    ["main.py"],
    # Absolute via SPECPATH. `pathex` is resolved against the *current*
    # directory (unlike the scripts list above and `datas`, which PyInstaller
    # resolves against the spec's own directory), and Tauri's
    # beforeBundleCommand builds from desktop/. A bare "src" therefore pointed
    # at desktop/src, which does not exist -- so desktop_pdf_translator was
    # silently left out of the bundle and the sidecar died at startup with
    # "ModuleNotFoundError: No module named 'desktop_pdf_translator'".
    pathex=[_os.path.join(SPECPATH, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    # 1 (-O: drop asserts), never 2 (-OO: also drop docstrings). numpy builds
    # real API surface out of docstrings at import time -- `add_docstring` is
    # handed the stripped `None` and raises
    # "TypeError: argument docstring of add_docstring should be a str"
    # from numpy/_core/overrides.py, killing the sidecar before it prints
    # READY. The Rust shell then reports a startup failure with no log line,
    # because the crash happens before logging is configured.
    optimize=1,
)

# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------
# `excludes` can only drop a whole importable package, and the packages that
# dominate this bundle are all reachable: babeldoc's layout parser imports cv2
# (`document_il/midend/layout_parser.py`), its char extractor imports
# `sklearn.cluster` (`document_il/utils/extract_char.py`), and RAG indexing
# imports camelot, which brings pandas. What follows drops payload *inside*
# packages we have to keep.
#
# Bytes are what matter, and it is worth knowing why, because "fewer files"
# is the intuitive answer and it is wrong here. Measured on the real tree:
# writing all 2,240 entries costs 1.7 s, of which per-file overhead is
# ~0.55 ms, and Defender adds only ~0.4 ms per *unknown* binary (+0.14 s
# across all 379 .pyd/.dll). Dropping a thousand small files buys well under a
# second. The rule that pays for itself is the 28 MB one; the small-file rules
# below are kept because they are free and keep the tree honest, not because
# they make an install faster.
#
# What goes stale here is the *reachability* claim, not the paths: a babeldoc
# or camelot upgrade can start using something dropped below. Each rule
# therefore names what would have reached it. `tests/test_sidecar_smoke.py`
# against the frozen exe is what catches a bad one.
import sys as _sys


def _prune(predicate, label):
    """Drop matching entries from both TOCs, and say how many went.

    Printed rather than silent: a prune that quietly stops matching (a renamed
    directory, a new wheel layout) looks like nothing at all, and the place you
    would find out is a shipped installer.
    """
    before = len(a.binaries) + len(a.datas)
    a.binaries = [e for e in a.binaries if not predicate(e[0].replace("\\", "/"))]
    a.datas = [e for e in a.datas if not predicate(e[0].replace("\\", "/"))]
    print(f"PRUNE {label}: {before - len(a.binaries) - len(a.datas)} file(s)")


# OpenCV's FFmpeg video backend, 28 MB in one DLL. Everything here hands cv2
# page bitmaps — imread, resize, cvtColor, the doclayout preprocessing — and
# nothing constructs a VideoCapture or VideoWriter.
#
# Windows only. Here the DLL is resolved by name when the videoio FFmpeg
# backend first initialises, so its absence only marks that backend
# unavailable. The Linux wheel has no equivalent: its ffmpeg lives in
# `opencv_python_headless.libs/libav*.so`, which cv2's own extension module
# lists in DT_NEEDED, and dropping those breaks `import cv2` outright.
if _sys.platform == "win32":
    _prune(
        lambda dest: _os.path.basename(dest).lower().startswith("opencv_videoio_ffmpeg"),
        "opencv videoio ffmpeg backend",
    )

# scikit-learn's bundled sample corpora: the toy sets behind `load_iris()` and
# friends (`data/`), their prose descriptions (`descr/`), two demo photographs
# (`images/`), and the fixtures for sklearn's own test suite (`tests/`). The
# only sklearn entry point in this app is `sklearn.cluster.DBSCAN`, via
# babeldoc's char extractor; nothing calls a loader or a fetcher.
#
# Spelled out per directory rather than as `sklearn/datasets/*`, because that
# directory also holds `_svmlight_format_fast`, a compiled extension
# `sklearn/datasets/__init__.py` imports at package load.
_prune(
    lambda dest: dest.startswith(
        (
            "sklearn/datasets/data/",
            "sklearn/datasets/descr/",
            "sklearn/datasets/images/",
            "sklearn/datasets/tests/",
        )
    ),
    "sklearn sample datasets and test fixtures",
)

# Build-time leftovers from the scientific wheels: MSVC import libraries, the
# Cython and C sources the shipped extensions were generated from, and meson
# build files. A linker reads these; an interpreter never opens one.
#
# `.pyi` is deliberately NOT in this list, obvious as it looks. scikit-image
# (a babeldoc dependency) builds its public namespace with `lazy_loader`,
# which parses `skimage/__init__.pyi` at **import** time to learn what to
# attach — dropping it fails the import with "Cannot load imports from
# non-existent stub", surfacing as a non-fatal warm-up warning and then a
# misleading `cannot import name 'PDFProcessor'` on the first translate.
_prune(
    lambda dest: dest.endswith((".lib", ".pyx", ".pxd", ".h", ".hpp"))
    or _os.path.basename(dest) == "meson.build",
    "C/Cython build artefacts",
)


pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pdfusion-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,          # PyInstaller strip can corrupt PDBs on Windows.
    upx=False,            # UPX often breaks numpy/torch DLLs and trips AV.
    console=True,         # MUST stay True — the Rust shell reads the READY line from stdout.
                          # (No-op off Windows; the shell pipes stdio either way.)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdfusion-sidecar",   # → dist/pdfusion-sidecar/{pdfusion-sidecar[.exe], _internal/}
)
