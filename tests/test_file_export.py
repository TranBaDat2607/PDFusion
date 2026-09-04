"""Unit tests for `utils.file_export.export_pdf`.

The point of this module is that a translation's only durable copy is the one
the user picked, so these tests care most about the failure paths: a temp
artifact that already got swept, a destination we can't write, and anything
that would leave a half-written file where the user thinks a good PDF is.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from desktop_pdf_translator.utils.file_export import (
    ExportIOError,
    ExportPermissionError,
    InvalidExportError,
    SourceMissingError,
    export_pdf,
)

from conftest import MINIMAL_PDF


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_saves_to_chosen_destination(rolling_pdf: Path, tmp_path: Path):
    destination = tmp_path / "Documents" / "paper_vi.pdf"

    result = export_pdf(rolling_pdf, destination)

    assert result.saved_path == destination.resolve()
    assert result.bytes_written == len(MINIMAL_PDF)
    assert destination.read_bytes() == MINIMAL_PDF


def test_source_survives_the_export(rolling_pdf: Path, tmp_path: Path):
    """Copy, never move: the rolling file still backs the on-screen preview."""
    export_pdf(rolling_pdf, tmp_path / "out.pdf")

    assert rolling_pdf.exists()
    assert rolling_pdf.read_bytes() == MINIMAL_PDF


def test_creates_missing_parent_directories(rolling_pdf: Path, tmp_path: Path):
    destination = tmp_path / "a" / "b" / "c" / "paper_vi.pdf"

    export_pdf(rolling_pdf, destination)

    assert destination.is_file()


def test_overwrites_existing_file(rolling_pdf: Path, tmp_path: Path):
    """The native Save dialog already asked about replacing; don't second-guess."""
    destination = tmp_path / "paper_vi.pdf"
    destination.write_bytes(b"%PDF-1.4\nstale\n")

    result = export_pdf(rolling_pdf, destination)

    assert destination.read_bytes() == MINIMAL_PDF
    assert result.bytes_written == len(MINIMAL_PDF)


def test_leaves_no_staging_files_behind(rolling_pdf: Path, tmp_path: Path):
    dest_dir = tmp_path / "out"
    export_pdf(rolling_pdf, dest_dir / "paper_vi.pdf")

    assert [p.name for p in dest_dir.iterdir()] == ["paper_vi.pdf"]


def test_export_is_independent_of_the_temp_dir(rolling_pdf: Path, tmp_path: Path):
    """The whole point of #11: once saved, wiping `%TEMP%` must not matter."""
    destination = tmp_path / "keep" / "paper_vi.pdf"
    export_pdf(rolling_pdf, destination)

    # Simulate the next job / app-exit / orphan sweep nuking the job dir.
    shutil.rmtree(rolling_pdf.parent)

    assert not rolling_pdf.exists()
    assert destination.read_bytes() == MINIMAL_PDF


def test_accepts_string_paths(rolling_pdf: Path, tmp_path: Path):
    """The API layer passes `Path(...)`, but tolerate raw strings too."""
    destination = tmp_path / "paper_vi.pdf"

    result = export_pdf(str(rolling_pdf), str(destination))

    assert result.saved_path == destination.resolve()


# ---------------------------------------------------------------------------
# Missing / invalid source
# ---------------------------------------------------------------------------


def test_swept_temp_dir_reports_a_recoverable_message(
    rolling_pdf: Path, tmp_path: Path
):
    shutil.rmtree(rolling_pdf.parent)

    with pytest.raises(SourceMissingError) as excinfo:
        export_pdf(rolling_pdf, tmp_path / "paper_vi.pdf")

    assert "Translate the document again" in str(excinfo.value)
    assert SourceMissingError.status == 404


def test_directory_as_source_is_rejected(temp_translate_dir: Path, tmp_path: Path):
    with pytest.raises(InvalidExportError):
        export_pdf(temp_translate_dir, tmp_path / "out.pdf")


def test_non_pdf_source_is_rejected(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_bytes(MINIMAL_PDF)

    with pytest.raises(InvalidExportError, match="Only .pdf"):
        export_pdf(source, tmp_path / "out.pdf")


def test_empty_source_is_rejected(empty_rolling_pdf: Path, tmp_path: Path):
    with pytest.raises(InvalidExportError, match="not a valid PDF"):
        export_pdf(empty_rolling_pdf, tmp_path / "out.pdf")

    assert not (tmp_path / "out.pdf").exists()


def test_truncated_source_is_rejected(temp_translate_dir: Path, tmp_path: Path):
    source = temp_translate_dir / "paper_translated_v001.pdf"
    source.write_bytes(b"<html>error page</html>")

    with pytest.raises(InvalidExportError, match="not a valid PDF"):
        export_pdf(source, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Invalid destination
# ---------------------------------------------------------------------------


def test_non_pdf_destination_is_rejected(rolling_pdf: Path, tmp_path: Path):
    with pytest.raises(InvalidExportError, match="must end in .pdf"):
        export_pdf(rolling_pdf, tmp_path / "paper_vi.docx")


def test_destination_that_is_a_directory_is_rejected(
    rolling_pdf: Path, tmp_path: Path
):
    target = tmp_path / "folder.pdf"
    target.mkdir()

    with pytest.raises(InvalidExportError, match="is a directory"):
        export_pdf(rolling_pdf, target)


def test_saving_onto_itself_is_rejected(rolling_pdf: Path):
    with pytest.raises(InvalidExportError, match="same file"):
        export_pdf(rolling_pdf, rolling_pdf)


@pytest.mark.skipif(
    sys.platform != "win32", reason="case-insensitive path check is Windows-specific"
)
def test_saving_onto_itself_is_rejected_case_insensitively(rolling_pdf: Path):
    shouting = Path(str(rolling_pdf).upper())

    with pytest.raises(InvalidExportError, match="same file"):
        export_pdf(rolling_pdf, shouting)


def test_refuses_to_overwrite_the_opened_document(
    rolling_pdf: Path, tmp_path: Path
):
    """The suggested filename can land on the user's own document (a source
    already named `..._vi.pdf`). Replacing it would destroy their input."""
    original = tmp_path / "chapter_vi.pdf"
    original.write_bytes(b"%PDF-1.4\nthe user's document\n")

    with pytest.raises(InvalidExportError, match="document you opened"):
        export_pdf(rolling_pdf, original, protect=original)

    assert original.read_bytes() == b"%PDF-1.4\nthe user's document\n"


def test_protect_does_not_block_other_destinations(rolling_pdf: Path, tmp_path: Path):
    original = tmp_path / "chapter.pdf"
    original.write_bytes(MINIMAL_PDF)

    export_pdf(rolling_pdf, tmp_path / "chapter_vi.pdf", protect=original)

    assert (tmp_path / "chapter_vi.pdf").read_bytes() == MINIMAL_PDF


def test_protect_matches_a_non_existent_path_too(rolling_pdf: Path, tmp_path: Path):
    """A source document that has since been moved/deleted still shouldn't be
    a save target — `samefile` can't help, so the normalized-string path runs."""
    original = tmp_path / "gone.pdf"

    with pytest.raises(InvalidExportError, match="document you opened"):
        export_pdf(rolling_pdf, original, protect=original)


def test_protect_is_optional(rolling_pdf: Path, tmp_path: Path):
    destination = tmp_path / "paper_vi.pdf"

    export_pdf(rolling_pdf, destination)

    assert destination.read_bytes() == MINIMAL_PDF


def test_unwritable_destination_reports_permission_error(
    rolling_pdf: Path, tmp_path: Path, deny_copyfile: None
):
    destination = tmp_path / "locked" / "paper_vi.pdf"

    with pytest.raises(ExportPermissionError):
        export_pdf(rolling_pdf, destination)

    assert not destination.exists()


def test_disk_full_leaves_no_partial_file(
    rolling_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A failed copy must not leave a truncated PDF at the destination — the
    user would take it for a good save and only find out much later."""
    dest_dir = tmp_path / "out"
    destination = dest_dir / "paper_vi.pdf"
    real_copyfile = shutil.copyfile

    def half_write(src, dst, **kwargs):
        real_copyfile(src, dst, **kwargs)  # staging file now exists
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        "desktop_pdf_translator.utils.file_export.shutil.copyfile", half_write
    )

    with pytest.raises(ExportIOError):
        export_pdf(rolling_pdf, destination)

    assert not destination.exists()
    # …and the staging file is cleaned up rather than left as litter.
    assert list(dest_dir.iterdir()) == []


def test_replace_failure_is_reported_and_cleaned_up(
    rolling_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """On Windows, `os.replace` fails if the destination is open elsewhere."""
    dest_dir = tmp_path / "out"
    destination = dest_dir / "paper_vi.pdf"

    def locked(*_args, **_kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr("desktop_pdf_translator.utils.file_export.os.replace", locked)

    with pytest.raises(ExportPermissionError):
        export_pdf(rolling_pdf, destination)

    assert not destination.exists()
    assert list(dest_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Cache-copy source — the other place a translated PDF can come from
# ---------------------------------------------------------------------------


def test_exports_a_cache_materialized_file(tmp_path: Path):
    """A cache hit materializes `<stem>_translated_v999.pdf` into the job dir;
    that path exports exactly like a freshly-rendered one."""
    job_dir = tmp_path / "pdfusion-translate-cache"
    job_dir.mkdir()
    materialized = job_dir / "paper_translated_v999.pdf"
    materialized.write_bytes(MINIMAL_PDF)

    destination = tmp_path / "out" / "paper_vi.pdf"
    export_pdf(materialized, destination)

    # Evicting the cache entry afterwards must not touch the user's copy.
    os.remove(materialized)
    assert destination.read_bytes() == MINIMAL_PDF
