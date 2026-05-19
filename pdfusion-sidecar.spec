# PyInstaller spec for the PDFusion FastAPI sidecar.
#
# Build with:
#     pyinstaller pdfusion-sidecar.spec --clean --noconfirm
#
# Output: dist/pdfusion-sidecar/pdfusion-sidecar.exe (+ _internal/ tree).
# The build-sidecar.ps1 helper then stages this into
# desktop/src-tauri/binaries/ with the triple-suffixed name Tauri expects.

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import (
    collect_data_files,
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

    # FastAPI / SSE / forms
    "sse_starlette",
    "sse_starlette.sse",
    "python_multipart",

    # pydantic v2 stack
    "pydantic_settings",

    # chromadb dynamic backends
    "chromadb.api.fastapi",
    "chromadb.telemetry.product.posthog",
    "chromadb.db.impl.sqlite",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.segment.impl.vector.local_persistent_hnsw",
    "chromadb.utils.embedding_functions",

    # Argos translate (lazy-imported inside argos_translator)
    "argostranslate",
    "argostranslate.package",
    "argostranslate.translate",

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
# transformers picks model-class modules at runtime based on the loaded model
# config (e.g. `transformers.models.xlm_roberta.modeling_xlm_roberta` for the
# default RAG embedding model). sentence-transformers triggers these on first
# `embed()` call, so without them, RAG ask would fail at chat time.
hiddenimports += collect_submodules("transformers")
# huggingface_hub has a `_LazyImporter` in its package __init__ that resolves
# attributes through `importlib.import_module` — sentence-transformers + the
# tokenizers hub calls hit it.
hiddenimports += collect_submodules("huggingface_hub")
# sentence-transformers loads model backends dynamically.
hiddenimports += collect_submodules("sentence_transformers")
# chromadb has dynamic plugin loading throughout.
hiddenimports += collect_submodules("chromadb")
# langchain pulls many internal modules; keep it generous since it is listed in
# requirements and may be hit by indirect imports.
hiddenimports += collect_submodules("langchain")
hiddenimports += collect_submodules("langchain_community")


# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
# BabelDOC ships fonts, layout-model configs, and template assets next to its
# package — those must be copied into the bundle or runtime calls will fail.
datas = []
datas += collect_data_files("babeldoc")

# sentence-transformers / tokenizers / transformers have small package data
# (tokenizer configs, vocab fallbacks) that they expect to find on disk.
datas += collect_data_files("sentence_transformers")
datas += collect_data_files("tokenizers")
datas += collect_data_files("transformers", include_py_files=False)
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
    "google-generativeai",
    "sentence-transformers",
    "chromadb",
    "fastapi",
    "uvicorn",
    "babeldoc",
):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001 — best-effort; package may not be installed locally
        pass

# Default configuration TOML — the ConfigManager reads this at startup as a
# fallback when no user config exists yet.
datas += [("config/default_config.toml", "config")]


# ---------------------------------------------------------------------------
# Excludes
# ---------------------------------------------------------------------------
# Drop heavy transitive deps we don't actually use. Cuts ~150-300 MB off the
# bundle. If a runtime error mentions one of these, remove it from the list.
excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "notebook",
    "jupyter",
    "pytest",
    "scipy.misc",
]


a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
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
    name="pdfusion-sidecar",   # → dist/pdfusion-sidecar/{pdfusion-sidecar.exe, _internal/}
)
