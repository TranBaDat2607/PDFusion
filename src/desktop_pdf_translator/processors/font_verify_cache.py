"""Remembers which of BabelDOC's cached assets have passed their hash check.

BabelDOC builds a `FontMapper` in every pipeline stage — `ILCreater`,
`ParagraphFinder`, `StylesAndFormulas`, `ILTranslator` (twice on the LLM path),
`Typesetting`, `PDFCreater` (twice when scanned-page detection runs) — and each
one runs `assets.verify_file` over every font of the target language: 34 files,
266 MB of SHA3-256 for Vietnamese, ~1 s. Traced over two different documents on
the LLM path, that is 272 checks per page (238 on Argos, which skips one
mapper) against the same 34 (file, expected hash) pairs, none of them derived
from the document, every file byte-identical before and after. Measured on a
one-page run: 15.5 s → 7.9 s with the passes remembered.

Only the first check in a process can find something new — a missing or corrupt
font, which BabelDOC then re-downloads — so a pass is remembered and a failure
never is. A pass is tied to the file's size and mtime as they were on both
sides of the hash, so a font rewritten afterwards (a re-download, the setup
flow restoring the offline zip) or while it was being hashed is checked again.

Kept free of babeldoc imports at module scope, like `doc_layout_cache.py`: the
boot warm thread imports this, and so does the suite.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

Verify = Callable[[Path, str], bool]

# Marks a function this module produced, so `install()` can tell a memo it
# already put in place from BabelDOC's own function — or from a test's fake,
# which `monkeypatch` restores, taking the mark with it.
_MEMO_MARK = "_pdfusion_memoized"

_install_lock = threading.Lock()


def _signature(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def memoize_verify(verify: Verify) -> Verify:
    """Wrap `verify` so a (file, expected hash) that already passed, and has not
    changed since, is answered without hashing it again."""
    passed: dict[tuple[str, str], tuple[int, int]] = {}
    lock = threading.Lock()

    def memoized(path: Path, sha3_256: str) -> bool:
        key = (os.fspath(path), sha3_256)
        before = _signature(path)
        if before is not None:
            with lock:
                if passed.get(key) == before:
                    return True
        ok = verify(path, sha3_256)
        # Stat again after hashing: a pass on bytes that changed underneath it
        # vouches for a file nobody verified.
        with lock:
            if ok and before is not None and _signature(path) == before:
                passed[key] = before
            else:
                passed.pop(key, None)
        return ok

    setattr(memoized, _MEMO_MARK, True)
    memoized.__wrapped__ = verify  # type: ignore[attr-defined]
    return memoized


def install() -> None:
    """Put the memo in front of `babeldoc.assets.assets.verify_file`.

    BabelDOC's asset functions call `verify_file` as a module global, so
    replacing the attribute is enough to reach every stage's font check.
    Re-check that against `assets.py` when bumping babeldoc. Blocking (imports
    babeldoc's asset module); call off the event loop.
    """
    from babeldoc.assets import assets

    with _install_lock:
        if getattr(assets.verify_file, _MEMO_MARK, False):
            return
        assets.verify_file = memoize_verify(assets.verify_file)


def preverify_cached_fonts() -> int:
    """Check every embedding font already on disk, so the first page doesn't.

    Only fonts that exist are checked, and through `verify_file` rather than
    `get_font_and_metadata`, which downloads what is missing: this runs on the
    boot warm thread, with no UI to report a download, and BabelDOC writes
    downloads in place — racing the setup flow's copy can corrupt it. Returns
    how many fonts passed. Blocking; call off the event loop.
    """
    install()
    from babeldoc.assets import assets
    from babeldoc.assets.embedding_assets_metadata import EMBEDDING_FONT_METADATA
    from babeldoc.const import get_cache_file_path

    started = time.perf_counter()
    checked = passed = 0
    for name, meta in EMBEDDING_FONT_METADATA.items():
        path = get_cache_file_path(name, "fonts")
        if not path.exists():
            continue
        checked += 1
        if assets.verify_file(path, meta["sha3_256"]):
            passed += 1
    logger.info(
        "BabelDOC fonts pre-verified: %d of %d on disk passed in %.1fs",
        passed, checked, time.perf_counter() - started,
    )
    return passed
