"""Copy a produced PDF out of its ephemeral working directory into a
user-chosen, permanent location.

Why this exists
---------------
Every translation writes its rolling output into a throwaway
``%TEMP%\\pdfusion-translate-<rand>\\`` directory that is wiped by the next job,
by the Tauri exit handler, and by the sidecar's orphan sweep. The whole-PDF
cache under ``translated_pdf_cache/files/`` is *also* not a user-owned copy: it
is content-addressed (SHA-named), LRU-evicted at ``pdf_cache_max_size_mb``, and
cleared wholesale by ``DELETE /config/cache``.

So neither location is a place a user can be told their translation was
"saved". This module is the one place that produces a durable copy the user
owns, at a path they picked in the native Save dialog.

The copy goes through a sibling temp file + ``os.replace`` so an interrupted
copy never leaves a truncated PDF at the destination the user believes is good.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PDF_SUFFIX = ".pdf"

# Every PDF starts with this magic. Cheap guard against exporting a
# zero-byte/truncated rolling file as if it were a finished translation.
_PDF_MAGIC = b"%PDF-"


class ExportError(Exception):
    """Base class for export failures.

    ``status`` is the HTTP status the API layer should map this to, so the
    route stays a thin translation layer with no error-classification logic
    of its own.
    """

    status: int = 500


class SourceMissingError(ExportError):
    """The translated artifact is gone — most likely its temp dir was wiped."""

    status = 404


class InvalidExportError(ExportError):
    """The request itself doesn't make sense (bad suffix, same file, …)."""

    status = 400


class ExportPermissionError(ExportError):
    """The destination is not writable by this user."""

    status = 403


class ExportIOError(ExportError):
    """Anything else that went wrong while copying."""

    status = 500


@dataclass(frozen=True)
class ExportResult:
    saved_path: Path
    bytes_written: int


def _resolve(path: Path) -> Path:
    """Absolute form of ``path``. Falls back to the expanded-but-unresolved
    path when the OS can't resolve it (an unreachable UNC prefix, say) — the
    caller's existence checks will produce a better error than we could."""
    expanded = Path(path).expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded


def _is_same_file(source: Path, destination: Path) -> bool:
    if destination.exists():
        try:
            return os.path.samefile(source, destination)
        except OSError:
            # samefile can fail on odd filesystems; fall through to the
            # string comparison rather than blocking a legitimate export.
            pass
    # normcase, because resolve() does not fold case on Windows: `C:\A\x.pdf`
    # and `c:\a\X.pdf` are one file that compares unequal as strings.
    return os.path.normcase(str(source)) == os.path.normcase(str(destination))


def _looks_like_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(_PDF_MAGIC)) == _PDF_MAGIC
    except OSError:
        return False


def export_pdf(
    source: Path,
    destination: Path,
    protect: Optional[Path] = None,
) -> ExportResult:
    """Copy ``source`` to ``destination``, overwriting it if present.

    Overwriting is intended: the native Save dialog has already asked the user
    to confirm replacing an existing file, so a second confirmation here would
    only be able to *refuse* a choice they already made.

    ``protect`` is the one exception — the source *document* the user opened.
    Nothing stops them typing its name into the Save dialog and confirming
    "Replace?", which would destroy their input with no undo, so it is refused
    even though the dialog said yes.

    Raises a subclass of :class:`ExportError` on every failure path.
    """
    source = _resolve(source)
    destination = _resolve(destination)

    if not source.exists():
        raise SourceMissingError(
            f"The translated file is no longer available at {source}. "
            "Translate the document again, then save it."
        )
    if not source.is_file():
        raise InvalidExportError(f"Not a file: {source}")
    if source.suffix.lower() != PDF_SUFFIX:
        raise InvalidExportError(f"Only .pdf files can be exported, got: {source.name}")
    if not _looks_like_pdf(source):
        raise InvalidExportError(
            f"{source.name} is empty or not a valid PDF — refusing to save it."
        )

    if destination.suffix.lower() != PDF_SUFFIX:
        raise InvalidExportError(
            f"Destination must end in .pdf, got: {destination.name}"
        )
    if destination.exists() and destination.is_dir():
        raise InvalidExportError(f"Destination is a directory: {destination}")
    if _is_same_file(source, destination):
        raise InvalidExportError(
            "The source and destination are the same file — nothing to save."
        )
    if protect is not None:
        if _is_same_file(_resolve(protect), destination):
            raise InvalidExportError(
                "That's the document you opened. Saving the translation over it "
                "would destroy your original — choose a different name."
            )

    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ExportPermissionError(f"Cannot create folder {parent}: {exc}") from exc
    except OSError as exc:
        raise ExportIOError(f"Cannot create folder {parent}: {exc}") from exc

    # Sibling temp file so the rename below stays on one filesystem (and is
    # therefore atomic). Leading dot + random suffix keeps it out of the way
    # if we die before the replace.
    staging = parent / f".{destination.name}.pdfusion-{uuid.uuid4().hex[:8]}.part"
    try:
        shutil.copyfile(source, staging)
        os.replace(staging, destination)
    except PermissionError as exc:
        _discard(staging)
        raise ExportPermissionError(
            f"No permission to write {destination}: {exc}"
        ) from exc
    except OSError as exc:
        _discard(staging)
        raise ExportIOError(f"Could not save to {destination}: {exc}") from exc

    try:
        size = destination.stat().st_size
    except OSError:
        size = 0

    logger.info("Exported %s -> %s (%d bytes)", source, destination, size)
    return ExportResult(saved_path=destination, bytes_written=size)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not remove staging file %s", path, exc_info=True)
