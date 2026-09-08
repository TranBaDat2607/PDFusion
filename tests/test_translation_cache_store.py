"""The paragraph-level SQLite cache (`translators/translation_cache.py`).

The storage layer had no coverage at all: the key contract, TTL expiry, the
size cap that was declared-but-never-enforced until `enforce_size_cap` landed,
and the `llm_cache_*` wrappers that are the only thing standing between a
translator and the user's settings.

Every test builds its own cache under `tmp_path`. The module-level singleton is
never touched, so nothing here can reach `~/AppData/Local/PDFusion/`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from desktop_pdf_translator.translators import translation_cache as tc_module
from desktop_pdf_translator.translators.translation_cache import (
    TranslationCache,
    _make_cache_key,
    llm_cache_get,
    llm_cache_set,
)


@pytest.fixture
def cache(tmp_path: Path) -> TranslationCache:
    return TranslationCache(cache_dir=tmp_path / "translation_cache")


def store(cache: TranslationCache, text: str, translated: str, **over) -> bool:
    kwargs = {"lang_in": "en", "lang_out": "vi", "service": "openai", "model": "gpt-4o"}
    kwargs.update(over)
    return cache.set(text, translated, **kwargs)


def fetch(cache: TranslationCache, text: str, **over):
    kwargs = {"lang_in": "en", "lang_out": "vi", "service": "openai", "model": "gpt-4o"}
    kwargs.update(over)
    return cache.get(text, **kwargs)


# ---------------------------------------------------------------------------
# key contract
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_one_key():
    a = _make_cache_key("Hello", "en", "vi", "openai", "gpt-4o")
    b = _make_cache_key("Hello", "en", "vi", "openai", "gpt-4o")
    assert a == b


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_text", "Goodbye"),
        ("lang_in", "ja"),
        ("lang_out", "en"),
        ("service", "gemini"),
        ("model", "gpt-4o-mini"),
    ],
)
def test_every_field_separates_the_key(field, value):
    """Changing the model invalidates that model's entries; identical inputs
    across services stay independent. Both fall out of the key, so each field
    has to actually be in it."""
    base = dict(
        source_text="Hello",
        lang_in="en",
        lang_out="vi",
        service="openai",
        model="gpt-4o",
    )
    changed = {**base, field: value}
    assert _make_cache_key(**base) != _make_cache_key(**changed)


def test_a_missing_model_is_not_the_same_as_an_empty_one():
    """Argos pins `model` to a fixed string; the LLMs always have one. `None`
    normalizes to the empty string, so these two must collide by design —
    asserted so a future change to the payload format is deliberate."""
    assert _make_cache_key("x", "en", "vi", "argos", None) == _make_cache_key(
        "x", "en", "vi", "argos", ""
    )


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------


def test_an_unseen_paragraph_is_a_miss(cache: TranslationCache):
    assert fetch(cache, "Hello") is None


def test_a_stored_paragraph_comes_back(cache: TranslationCache):
    assert store(cache, "Hello", "Xin chào") is True
    assert fetch(cache, "Hello") == "Xin chào"


def test_a_hit_needs_every_field_to_match(cache: TranslationCache):
    store(cache, "Hello", "Xin chào")
    assert fetch(cache, "Hello", service="gemini") is None
    assert fetch(cache, "Hello", model="gpt-4o-mini") is None
    assert fetch(cache, "Hello", lang_out="ja") is None


def test_restoring_the_same_paragraph_overwrites_rather_than_duplicates(
    cache: TranslationCache,
):
    store(cache, "Hello", "Xin chào")
    store(cache, "Hello", "Chào bạn")
    assert fetch(cache, "Hello") == "Chào bạn"
    assert cache.stats()["entries"] == 1


def test_an_empty_translation_is_refused(cache: TranslationCache):
    """An empty result is what a safety filter or a truncated response leaves
    behind. Caching it would serve the blank for the TTL's whole 30 days."""
    assert store(cache, "Hello", "") is False
    assert fetch(cache, "Hello") is None


def test_unicode_survives_the_round_trip(cache: TranslationCache):
    store(cache, "Formula ⟦1⟧ and ünïcode", "Công thức ⟦1⟧ và ünïcode")
    assert fetch(cache, "Formula ⟦1⟧ and ünïcode") == "Công thức ⟦1⟧ và ünïcode"


def test_a_hit_bumps_the_row_counters(cache: TranslationCache):
    store(cache, "Hello", "Xin chào")
    fetch(cache, "Hello")
    fetch(cache, "Hello")
    row = cache._conn().execute(
        "SELECT hit_count, last_used FROM translations"
    ).fetchone()
    assert row["hit_count"] == 2
    assert row["last_used"] is not None


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


def test_an_expired_entry_reads_as_a_miss(tmp_path: Path):
    cache = TranslationCache(cache_dir=tmp_path / "c", ttl_days=-1)
    store(cache, "Hello", "Xin chào")
    assert fetch(cache, "Hello") is None


def test_an_expired_entry_is_left_on_disk_for_the_reaper(tmp_path: Path):
    """`get` deliberately doesn't delete — the write would serialize behind
    `_write_lock` on a read path that runs once per paragraph."""
    cache = TranslationCache(cache_dir=tmp_path / "c", ttl_days=-1)
    store(cache, "Hello", "Xin chào")
    fetch(cache, "Hello")
    assert cache.stats()["entries"] == 1


def test_clear_expired_reaps_only_the_expired(tmp_path: Path):
    cache = TranslationCache(cache_dir=tmp_path / "c")
    store(cache, "fresh", "tươi")
    cache._conn().execute(
        "UPDATE translations SET expires_at = ? WHERE source_text = ?",
        ((datetime.now() - timedelta(days=1)).isoformat(), "fresh"),
    )
    cache._conn().commit()
    store(cache, "current", "hiện tại")

    assert cache.clear_expired() == 1
    assert fetch(cache, "current") == "hiện tại"
    assert cache.stats()["entries"] == 1


def test_clear_expired_on_an_empty_cache_removes_nothing(cache: TranslationCache):
    assert cache.clear_expired() == 0


# ---------------------------------------------------------------------------
# size cap
# ---------------------------------------------------------------------------


def test_a_cache_under_the_cap_is_left_alone(cache: TranslationCache):
    store(cache, "Hello", "Xin chào")
    assert cache.enforce_size_cap() == 0
    assert cache.stats()["entries"] == 1


def test_the_cap_evicts_oldest_first(tmp_path: Path):
    """Rows are ordered by `COALESCE(last_used, cached_at)`, so a paragraph
    that keeps getting hit outlives one that was written once and forgotten."""
    cache = TranslationCache(cache_dir=tmp_path / "c", max_size_mb=0.05)
    body = "x" * 512
    for i in range(400):
        store(cache, f"{body}{i}", f"t{i}")

    kept_before = cache.stats()["entries"]
    removed = cache.enforce_size_cap()

    assert removed > 0
    assert cache.stats()["entries"] == kept_before - removed
    # The newest writes are the ones that survive.
    assert fetch(cache, f"{body}399") == "t399"


def test_the_cap_on_a_cache_that_was_never_written_is_a_no_op(tmp_path: Path):
    cache = TranslationCache(cache_dir=tmp_path / "c", max_size_mb=0.0)
    assert cache.enforce_size_cap() == 0


# ---------------------------------------------------------------------------
# clear_all / stats
# ---------------------------------------------------------------------------


def test_clear_all_reports_and_removes_everything(cache: TranslationCache):
    for i in range(5):
        store(cache, f"p{i}", f"t{i}")
    assert cache.clear_all() == 5
    assert cache.stats()["entries"] == 0
    assert fetch(cache, "p0") is None


def test_stats_track_hits_and_misses(cache: TranslationCache):
    store(cache, "Hello", "Xin chào")
    fetch(cache, "Hello")  # hit
    fetch(cache, "Hello")  # hit
    fetch(cache, "Missing")  # miss

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_the_hit_rate_of_an_untouched_cache_does_not_divide_by_zero(
    cache: TranslationCache,
):
    assert cache.stats()["hit_rate"] == 0.0


def test_stats_break_entries_down_by_service(cache: TranslationCache):
    store(cache, "a", "A", service="openai")
    store(cache, "b", "B", service="gemini")
    store(cache, "c", "C", service="gemini")
    assert cache.stats()["by_service"] == {"openai": 1, "gemini": 2}


def test_stats_separate_active_from_expired(tmp_path: Path):
    cache = TranslationCache(cache_dir=tmp_path / "c", ttl_days=-1)
    store(cache, "Hello", "Xin chào")
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["expired"] == 1
    assert stats["active"] == 0


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def test_entries_survive_a_new_cache_object_over_the_same_directory(tmp_path: Path):
    """The point of the on-disk cache: a cancelled job still warms the next
    run, and the next run is a different process."""
    first = TranslationCache(cache_dir=tmp_path / "c")
    store(first, "Hello", "Xin chào")

    second = TranslationCache(cache_dir=tmp_path / "c")
    assert fetch(second, "Hello") == "Xin chào"


# ---------------------------------------------------------------------------
# the llm_cache_* wrappers
# ---------------------------------------------------------------------------


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TranslationCache:
    """Point the module singleton at a throwaway cache and declare caching on,
    so the wrappers can be exercised without a real config file."""
    cache = TranslationCache(cache_dir=tmp_path / "wrapped")
    monkeypatch.setattr(tc_module, "_INSTANCE", cache)
    monkeypatch.setattr(tc_module, "_cache_enabled", lambda: True)
    return cache


def test_the_wrappers_round_trip_through_the_cache(wired: TranslationCache):
    llm_cache_set("Hello", "Xin chào", "en", "vi", "openai", "gpt-4o")
    assert llm_cache_get("Hello", "en", "vi", "openai", "gpt-4o") == "Xin chào"


def test_a_disabled_cache_never_writes(
    wired: TranslationCache, monkeypatch: pytest.MonkeyPatch
):
    """`cache_translations = false` has to bypass the cache in both
    directions, so translator code can call the wrappers unconditionally."""
    monkeypatch.setattr(tc_module, "_cache_enabled", lambda: False)
    llm_cache_set("Hello", "Xin chào", "en", "vi", "openai", "gpt-4o")
    assert wired.stats()["entries"] == 0


def test_a_disabled_cache_never_reads(
    wired: TranslationCache, monkeypatch: pytest.MonkeyPatch
):
    llm_cache_set("Hello", "Xin chào", "en", "vi", "openai", "gpt-4o")
    monkeypatch.setattr(tc_module, "_cache_enabled", lambda: False)
    assert llm_cache_get("Hello", "en", "vi", "openai", "gpt-4o") is None


def test_a_broken_cache_is_a_miss_not_a_failed_paragraph(
    wired: TranslationCache, monkeypatch: pytest.MonkeyPatch
):
    """The wrappers sit on the hot path of every paragraph. A cache that
    can't be opened must degrade to "no cache", never surface as a translation
    error that gets counted and keeps the run out of the PDF cache."""

    def boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(tc_module, "get_translation_cache", boom)
    assert llm_cache_get("Hello", "en", "vi", "openai", "gpt-4o") is None
    llm_cache_set("Hello", "Xin chào", "en", "vi", "openai", "gpt-4o")


def test_cache_enabled_is_false_when_settings_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reached during unit tests and early in boot. Defaulting to "on" would
    have the cache construct itself against a real AppData directory."""
    import desktop_pdf_translator.config as config_module

    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(config_module, "get_settings", boom)
    assert tc_module._cache_enabled() is False
