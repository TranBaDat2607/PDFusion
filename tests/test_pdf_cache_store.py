"""The whole-PDF translation cache (`processors/pdf_cache.py`).

A hit here skips the entire BabelDOC pipeline, so the storage layer is worth
more than its line count suggests. What's covered:

* the key contract, including the source language that used to be left out;
* the row/file consistency rules — a row whose file is gone is evicted rather
  than served, and a store that fails leaves neither a row nor a stray `.tmp`;
* the atomic copy, which is what stops a concurrent reader (pdf.js, or another
  job materializing the same entry) from seeing a half-written PDF;
* LRU eviction, and the size accounting it depends on.

`is_cacheable_artifact` — the "may this run be written at all" rule — lives in
`test_translation_failure_reporting.py`, next to the failure counting it reads.

Every test builds its own cache under `tmp_path`; the module singleton is never
touched, so nothing reaches `~/AppData/Local/PDFusion/`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktop_pdf_translator.processors import pdf_cache as pdf_cache_module
from desktop_pdf_translator.processors.pdf_cache import (
    PIPELINE_VERSION,
    PDFTranslationCache,
    _make_cache_key,
    compute_file_hash,
)

from conftest import MINIMAL_PDF


@pytest.fixture(autouse=True)
def _no_settings_lookup(monkeypatch: pytest.MonkeyPatch):
    """`_enforce_lru_cap` tops the cap up from live settings on every pass.
    Left alone it would read the developer's own `config.toml` and quietly
    replace the cap a test just set."""
    monkeypatch.setattr(
        PDFTranslationCache, "_refresh_cap_from_settings", lambda self: None
    )


@pytest.fixture
def cache(tmp_path: Path) -> PDFTranslationCache:
    return PDFTranslationCache(cache_dir=tmp_path / "pdf_cache")


@pytest.fixture
def source_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(MINIMAL_PDF)
    return path


@pytest.fixture
def translated_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "job" / "paper_translated_v001.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_PDF + b"% translated\n")
    return path


DEFAULTS = {
    "source_lang": "en",
    "target_lang": "vi",
    "service": "openai",
    "model": "gpt-4o",
}


def put(cache: PDFTranslationCache, src: Path, translated: Path, **over):
    return cache.store(src, translated, **{**DEFAULTS, **over})


def look(cache: PDFTranslationCache, src: Path, **over):
    return cache.lookup(src, **{**DEFAULTS, **over})


# ---------------------------------------------------------------------------
# hashing and the key
# ---------------------------------------------------------------------------


def test_the_hash_is_content_addressed(tmp_path: Path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(MINIMAL_PDF)
    b.write_bytes(MINIMAL_PDF)
    assert compute_file_hash(a) == compute_file_hash(b)

    b.write_bytes(MINIMAL_PDF + b"%more\n")
    assert compute_file_hash(a) != compute_file_hash(b)


def test_a_file_larger_than_one_read_chunk_hashes_correctly(tmp_path: Path):
    """The hash streams in 1 MB chunks; a single-read implementation would
    pass every other test in this file."""
    import hashlib

    payload = bytes(range(256)) * (12 * 1024)  # ~3 MB
    big = tmp_path / "big.pdf"
    big.write_bytes(payload)
    assert compute_file_hash(big) == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "field, value",
    [
        ("file_hash", "otherhash"),
        ("lang_in", "auto"),
        ("lang_out", "ja"),
        ("service", "argos"),
        ("model", "gpt-4o-mini"),
        ("pipeline_version", "999"),
    ],
)
def test_every_field_separates_the_key(field, value):
    base = dict(
        file_hash="abc",
        lang_in="en",
        lang_out="vi",
        service="openai",
        model="gpt-4o",
        pipeline_version=PIPELINE_VERSION,
    )
    assert _make_cache_key(**base) != _make_cache_key(**{**base, field: value})


# ---------------------------------------------------------------------------
# lookup / store
# ---------------------------------------------------------------------------


def test_an_untranslated_pdf_is_a_miss(cache: PDFTranslationCache, source_pdf: Path):
    assert look(cache, source_pdf) is None


def test_a_stored_translation_comes_back(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    assert put(cache, source_pdf, translated_pdf) is not None

    hit = look(cache, source_pdf)
    assert hit is not None
    assert hit.original_filename == "paper.pdf"
    assert hit.cached_path.read_bytes() == translated_pdf.read_bytes()


def test_the_cached_copy_outlives_the_job_output_dir(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    """The whole point: the per-job `%TEMP%` dir is wiped by the next run, and
    the cached PDF is what makes re-opening the same document instant."""
    put(cache, source_pdf, translated_pdf)
    shutil.rmtree(translated_pdf.parent)

    hit = look(cache, source_pdf)
    assert hit is not None and hit.cached_path.exists()


def test_a_hit_needs_every_key_field_to_match(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    put(cache, source_pdf, translated_pdf)
    assert look(cache, source_pdf, target_lang="ja") is None
    assert look(cache, source_pdf, service="argos") is None
    assert look(cache, source_pdf, model="gpt-4o-mini") is None


def test_auto_and_an_explicit_source_do_not_collide(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    """The LLM system prompts name the source language, so `auto` and `en` can
    produce different bytes. Source language was excluded from the key while
    the API pinned every request to `auto`; it must not go back out."""
    put(cache, source_pdf, translated_pdf, source_lang="en")
    assert look(cache, source_pdf, source_lang="auto") is None


def test_editing_the_input_invalidates_its_entry(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    put(cache, source_pdf, translated_pdf)
    source_pdf.write_bytes(MINIMAL_PDF + b"%revision 2\n")
    assert look(cache, source_pdf) is None


def test_a_hit_bumps_its_counter(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    put(cache, source_pdf, translated_pdf)
    assert look(cache, source_pdf).hit_count == 1
    assert look(cache, source_pdf).hit_count == 2


def test_storing_the_same_input_twice_replaces_the_entry(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    put(cache, source_pdf, translated_pdf)
    translated_pdf.write_bytes(MINIMAL_PDF + b"% retranslated\n")
    put(cache, source_pdf, translated_pdf)

    assert cache.stats()["entries"] == 1
    assert look(cache, source_pdf).cached_path.read_bytes().endswith(
        b"% retranslated\n"
    )


def test_a_precomputed_hash_is_honoured(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    """`process_pdf` hashes once and passes the digest to both calls, so the
    50 MB input isn't streamed twice per run."""
    digest = compute_file_hash(source_pdf)
    put(cache, source_pdf, translated_pdf, file_hash=digest)
    assert look(cache, source_pdf, file_hash=digest) is not None


def test_storing_a_missing_artifact_is_refused(
    cache: PDFTranslationCache, source_pdf: Path, tmp_path: Path
):
    assert put(cache, source_pdf, tmp_path / "never_written.pdf") is None
    assert cache.stats()["entries"] == 0


def test_an_unreadable_input_is_a_miss_not_a_raise(
    cache: PDFTranslationCache, tmp_path: Path
):
    """A lookup runs on every `POST /translate`; a hashing failure has to
    degrade to "no cache", never fail the job."""
    assert look(cache, tmp_path / "gone.pdf") is None
    assert cache.stats()["misses"] == 1


# ---------------------------------------------------------------------------
# row / file consistency
# ---------------------------------------------------------------------------


def test_a_row_whose_file_was_deleted_is_evicted_rather_than_served(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    """Users do wipe `files/` without touching `index.db`. Handing back a path
    that isn't there would fail much later, inside the copy into the job dir."""
    dest = put(cache, source_pdf, translated_pdf)
    dest.unlink()

    assert look(cache, source_pdf) is None
    assert cache.stats()["entries"] == 0


def test_a_failed_copy_leaves_no_row_and_no_temp_file(
    cache: PDFTranslationCache,
    source_pdf: Path,
    translated_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def deny(*_a, **_kw):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(pdf_cache_module.shutil, "copyfile", deny)

    assert put(cache, source_pdf, translated_pdf) is None
    assert cache.stats()["entries"] == 0
    assert list(cache.files_dir.iterdir()) == []


def test_a_failed_row_insert_takes_the_copied_file_with_it(
    cache: PDFTranslationCache,
    source_pdf: Path,
    translated_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Otherwise `files/` accumulates orphans that no row accounts for, so the
    LRU cap can never see them and never frees them."""

    def deny(self):
        raise RuntimeError("db gone")

    monkeypatch.setattr(PDFTranslationCache, "_conn", deny)

    assert put(cache, source_pdf, translated_pdf) is None
    assert list(cache.files_dir.glob("*.pdf")) == []


def test_the_copy_lands_atomically(
    cache: PDFTranslationCache,
    source_pdf: Path,
    translated_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Copy to a sibling `.tmp` then `os.replace`. A reader that catches the
    directory mid-store must see either nothing or the finished file — pdf.js
    renders a truncated PDF as an error, and this path runs while the viewer
    is showing the artifact."""
    seen: list[list[str]] = []
    real_replace = pdf_cache_module.os.replace

    def watched(src, dst):
        seen.append(sorted(p.name for p in cache.files_dir.iterdir()))
        return real_replace(src, dst)

    monkeypatch.setattr(pdf_cache_module.os, "replace", watched)
    dest = put(cache, source_pdf, translated_pdf)

    # Before the rename only the staging file exists; after it, only the real
    # one — the destination name is never partially written.
    assert seen == [[f"{dest.stem}.pdf.tmp"]]
    assert sorted(p.name for p in cache.files_dir.iterdir()) == [dest.name]


def test_a_stat_failure_falls_back_to_the_source_size(
    cache: PDFTranslationCache,
    source_pdf: Path,
    translated_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The bytes did land, so recording 0 would under-report the total, keep
    the cap from ever being enforced, and then loop on `total -= 0`."""
    real_stat = Path.stat

    def flaky(self, *a, **kw):
        if self.suffix == ".pdf" and self.parent == cache.files_dir:
            raise OSError("locked by the scanner")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky)
    put(cache, source_pdf, translated_pdf)
    monkeypatch.undo()

    size = cache._conn().execute(
        "SELECT file_size_bytes FROM pdf_translations"
    ).fetchone()[0]
    assert size == translated_pdf.stat().st_size


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


def make_entry(cache: PDFTranslationCache, tmp_path: Path, name: str, kb: int) -> Path:
    src = tmp_path / f"{name}.pdf"
    src.write_bytes(MINIMAL_PDF + name.encode())
    out = tmp_path / "job" / f"{name}_translated_v001.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"%PDF-1.4\n" + b"x" * (kb * 1024))
    put(cache, src, out)
    return src


def test_a_cache_under_the_cap_evicts_nothing(tmp_path: Path):
    cache = PDFTranslationCache(cache_dir=tmp_path / "c", max_size_mb=10.0)
    make_entry(cache, tmp_path, "a", 64)
    assert cache.stats()["entries"] == 1


def test_the_cap_evicts_least_recently_used_first(tmp_path: Path):
    cache = PDFTranslationCache(cache_dir=tmp_path / "c", max_size_mb=10.0)
    old = make_entry(cache, tmp_path, "old", 400)
    make_entry(cache, tmp_path, "mid", 400)
    keep = make_entry(cache, tmp_path, "new", 400)

    # Squeeze the cap to roughly one entry and force a pass by storing again.
    cache.max_size_mb = 0.5
    cache._enforce_lru_cap()

    assert look(cache, old) is None
    assert look(cache, keep) is not None


def test_eviction_deletes_the_files_too(tmp_path: Path):
    cache = PDFTranslationCache(cache_dir=tmp_path / "c", max_size_mb=10.0)
    make_entry(cache, tmp_path, "a", 400)
    make_entry(cache, tmp_path, "b", 400)

    cache.max_size_mb = 0.5
    cache._enforce_lru_cap()

    rows = cache._conn().execute("SELECT COUNT(*) FROM pdf_translations").fetchone()[0]
    assert len(list(cache.files_dir.glob("*.pdf"))) == rows


def test_a_hit_protects_an_entry_from_the_next_sweep(tmp_path: Path):
    """Eviction orders by `last_used`, which `lookup` refreshes — an entry the
    user keeps re-opening should outlive one written more recently but never
    touched again."""
    cache = PDFTranslationCache(cache_dir=tmp_path / "c", max_size_mb=10.0)
    first = make_entry(cache, tmp_path, "first", 400)
    make_entry(cache, tmp_path, "second", 400)
    look(cache, first)  # refreshes last_used past `second`'s

    cache.max_size_mb = 0.5
    cache._enforce_lru_cap()

    assert look(cache, first) is not None


def test_the_cap_is_re_read_from_settings_on_every_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Changing `pdf_cache_max_size_mb` applies without a sidecar restart,
    which is only true because the singleton's one-time read is topped up
    here."""
    monkeypatch.undo()  # drop the autouse stub for this one test

    class _Translation:
        pdf_cache_max_size_mb = 7.0

    class _Settings:
        translation = _Translation()

    import desktop_pdf_translator.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: _Settings())

    cache = PDFTranslationCache(cache_dir=tmp_path / "c", max_size_mb=1000.0)
    cache._refresh_cap_from_settings()
    assert cache.max_size_mb == 7.0


# ---------------------------------------------------------------------------
# clear_all / stats
# ---------------------------------------------------------------------------


def test_clear_all_removes_rows_and_files(tmp_path: Path):
    cache = PDFTranslationCache(cache_dir=tmp_path / "c")
    make_entry(cache, tmp_path, "a", 8)
    make_entry(cache, tmp_path, "b", 8)

    assert cache.clear_all() == 2
    assert cache.stats()["entries"] == 0
    assert list(cache.files_dir.glob("*.pdf")) == []


def test_stats_track_hits_misses_and_service_breakdown(
    cache: PDFTranslationCache, source_pdf: Path, translated_pdf: Path
):
    put(cache, source_pdf, translated_pdf)
    look(cache, source_pdf)
    look(cache, source_pdf, target_lang="ja")

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["by_service"] == {"openai": 1}
    assert stats["pipeline_version"] == PIPELINE_VERSION


def test_entries_survive_a_new_cache_object_over_the_same_directory(
    tmp_path: Path, source_pdf: Path, translated_pdf: Path
):
    first = PDFTranslationCache(cache_dir=tmp_path / "c")
    put(first, source_pdf, translated_pdf)

    second = PDFTranslationCache(cache_dir=tmp_path / "c")
    assert look(second, source_pdf) is not None
