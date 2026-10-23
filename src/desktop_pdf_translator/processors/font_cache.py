"""BabelDOC's embedding fonts, loaded once per process instead of once per
`FontMapper`.

BabelDOC builds a `FontMapper` in every pipeline stage — six per page on the
Argos path, seven on the LLM path, which also runs scanned-page detection —
and each one reads every embedding font of the target language into a
`pymupdf.Font`: all 34 of them, 266 MB, for any language, because BabelDOC
appends every other family's fonts to each family as fallbacks. None of it was
freed when a stage ended. The constructor memoizes by wrapping bound methods
in `lru_cache` — its own `has_char`/`map_in_type`, and each font's
`has_glyph`/`char_lengths` — so every mapper and every font sits in a
reference cycle, which only a full collection frees, and full collections
follow counts of objects, which 266 MB of font buffers barely move. On a book
that ran the sidecar out of memory: replayed through the real pipeline, four
pages took it from 0.26 GB to 6.5 GB without one full collection.

`install()` replaces `FontMapper.__init__` with a copy of BabelDOC's whose font
loop asks `_shared_font` instead. Sharing is safe because nothing in BabelDOC
writes to a mapper's fonts after construction — the stages only read them —
and PyMuPDF never releases the GIL, so MuPDF calls from different pages run
one at a time (Python runs inside a call only in PyMuPDF's warning and error
callbacks; see "Each font is loaded once per process" in
docs/architecture/translation-pipeline.md). The cost is that the fonts stay:
~266 MB from the first translation until the sidecar exits.

The copy is babeldoc 0.5.24's constructor, so it is only installed on a version
in `VERIFIED_BABELDOC`: babeldoc is pinned to a range, and a newer constructor
the copy doesn't match could break every stage, where leaving it alone only
costs the memory. `tests/test_font_cache.py` compares the copy with the
installed BabelDOC's own constructor; re-check it, then extend the set, when
bumping babeldoc.

Kept free of babeldoc imports at module scope, like `doc_layout_cache.py`.
"""

from __future__ import annotations

import functools
import logging
import os
import threading
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# The babeldoc versions whose `FontMapper.__init__` `_sharing_init` was checked
# against.
VERIFIED_BABELDOC = frozenset({"0.5.24"})

# Marks the constructor this module installed, so `install()` can tell it from
# BabelDOC's own — or from a test's, which `monkeypatch` restores.
_MARK = "_pdfusion_shared_fonts"

_install_lock = threading.Lock()
_fonts_lock = threading.Lock()
# Font file path → its one `pymupdf.Font`. Never pruned: every mapper of every
# page wants all of them.
_fonts: dict[str, Any] = {}


def _shared_font(font_file_name: str, font_path: Path, font_metadata: Mapping[str, Any]):
    """The process's `pymupdf.Font` for `font_path`, loaded on first use and
    given what BabelDOC's constructor gives each font it loads — once, where
    BabelDOC did it again for every mapper. A load that raises is not
    remembered, so the next mapper tries again."""
    key = os.fspath(font_path)
    with _fonts_lock:
        font = _fonts.get(key)
        if font is None:
            import pymupdf

            font = pymupdf.Font(fontfile=key)
            font.has_glyph = functools.lru_cache(maxsize=10240, typed=True)(font.has_glyph)
            font.char_lengths = functools.lru_cache(maxsize=10240, typed=True)(
                font.char_lengths
            )
            font.font_id = font_file_name
            font.font_path = font_path
            font.ascent_fontmap = font_metadata["ascent"]
            font.descent_fontmap = font_metadata["descent"]
            font.encoding_length = font_metadata["encoding_length"]
            _fonts[key] = font
    return font


def _sharing_init(fontmap, original):
    """BabelDOC 0.5.24's `FontMapper.__init__`, with its font loop asking
    `_shared_font`. Everything else is BabelDOC's, line for line, and reaches
    BabelDOC's `assets` through the fontmap module, as the original does."""

    def __init__(self, translation_config):
        self.translation_config = translation_config
        assert translation_config.primary_font_family in [
            None,
            "serif",
            "sans-serif",
            "script",
        ]
        self.primary_font_family = fontmap.PrimaryFontFamily.from_str(
            translation_config.primary_font_family,
        )

        font_family = fontmap.assets.get_font_family(translation_config.lang_out)
        self.font_file_names = []
        for k in (
            "normal",
            "script",
            "fallback",
            "base",
        ):
            self.font_file_names.extend(font_family[k])

        self.fonts = {}
        self.fontid2fontpath = {}
        for font_file_name in self.font_file_names:
            if font_file_name in self.fontid2fontpath:
                continue
            font_path, font_metadata = fontmap.assets.get_font_and_metadata(font_file_name)
            self.fonts[font_file_name] = _shared_font(font_file_name, font_path, font_metadata)
            self.fontid2fontpath[font_file_name] = font_path

        self.normal_font_ids = font_family["normal"]
        self.script_font_ids = font_family["script"]
        self.fallback_font_ids = font_family["fallback"]
        self.base_font_ids = font_family["base"]
        self.fontid2fontpath["base"] = self.fontid2fontpath[font_family["base"][0]]

        self.fontid2font = {f.font_id: f for f in self.fonts.values()}

        self.fontid2font["base"] = self.fontid2font[self.base_font_ids[0]]

        self.normal_fonts = [self.fontid2font[font_id] for font_id in self.normal_font_ids]
        self.script_fonts = [self.fontid2font[font_id] for font_id in self.script_font_ids]
        self.fallback_fonts = [
            self.fontid2font[font_id] for font_id in self.fallback_font_ids
        ]

        self.base_font = self.fontid2font["base"]

        self.type2font = {
            "normal": self.normal_fonts,
            "script": self.script_fonts,
            "fallback": self.fallback_fonts,
            "base": [self.base_font],
        }

        self.has_char = functools.lru_cache(maxsize=10240, typed=True)(self.has_char)
        self.map_in_type = functools.lru_cache(maxsize=10240, typed=True)(
            self.map_in_type
        )

    __init__.__wrapped__ = original  # type: ignore[attr-defined]
    setattr(__init__, _MARK, True)
    return __init__


def install() -> None:
    """Have every BabelDOC `FontMapper` share one loaded copy of each font.

    Patches the class, not the module's name for it: every stage imported
    `FontMapper` by name, so only the class object reaches them all.
    Idempotent. Blocking (imports BabelDOC); call off the event loop.
    """
    import babeldoc
    from babeldoc.format.pdf.document_il.utils import fontmap

    with _install_lock:
        current = fontmap.FontMapper.__init__
        if getattr(current, _MARK, False):
            return
        # A babeldoc that reports no version is as unchecked as a new one, and
        # leaving its constructor alone only costs memory.
        version = getattr(babeldoc, "__version__", None)
        if version not in VERIFIED_BABELDOC:
            logger.warning(
                "babeldoc %s is not one font_cache was checked against; its "
                "FontMapper keeps loading every font for every stage",
                version,
            )
            return
        fontmap.FontMapper.__init__ = _sharing_init(fontmap, current)
