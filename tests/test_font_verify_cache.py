"""BabelDOC re-hashes the same cached fonts on every pipeline stage.

Every stage builds a font mapper, and every mapper runs `verify_file` over all
34 fonts (266 MB, ~1 s) — 6-7 times per translated page. The fonts don't change
between those checks, so `font_verify_cache` remembers a pass. The risky half is
remembering too much: a failure, a font rewritten by a re-download, or a file
changing mid-hash must each be re-checked, or BabelDOC would trust bytes it
never verified. These tests pin both halves.

The pre-check tests inject fake `babeldoc` modules via `sys.modules` (the
module under test imports babeldoc only inside functions) so they don't pay for
the real import; one integration test uses the real package to prove the hook
point BabelDOC actually calls is the one we replace.
"""

from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path

import pytest

from desktop_pdf_translator.processors import font_cache, font_verify_cache


def _sha(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()


def _real_verify(path: Path, expected: str) -> bool:
    if not path.exists():
        return False
    return _sha(path.read_bytes()) == expected


class Counting:
    """A verify function that records every call and really hashes."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, path: Path, expected: str) -> bool:
        self.calls.append((path, expected))
        return _real_verify(path, expected)


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


# ---------------------------------------------------------------------------
# memoize_verify
# ---------------------------------------------------------------------------


def test_a_passing_file_is_hashed_once(tmp_path):
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)

    assert verify(f, _sha(b"font")) is True
    assert verify(f, _sha(b"font")) is True

    assert len(inner.calls) == 1


def test_a_failure_is_never_remembered(tmp_path):
    """A font BabelDOC repairs by re-downloading must be re-checked and pass."""
    f = tmp_path / "a.ttf"
    f.write_bytes(b"corrupt")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)
    good = _sha(b"font")

    assert verify(f, good) is False
    assert verify(f, good) is False
    assert len(inner.calls) == 2

    f.write_bytes(b"font")
    assert verify(f, good) is True


def test_a_missing_file_fails_and_is_not_remembered(tmp_path):
    f = tmp_path / "missing.ttf"
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)

    assert verify(f, _sha(b"font")) is False

    f.write_bytes(b"font")
    assert verify(f, _sha(b"font")) is True
    assert len(inner.calls) == 2


def test_a_resized_file_is_verified_again(tmp_path):
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)
    expected = _sha(b"font")
    assert verify(f, expected) is True

    f.write_bytes(b"corrupt-and-longer")

    assert verify(f, expected) is False
    assert len(inner.calls) == 2


def test_a_file_rewritten_with_the_same_size_is_verified_again(tmp_path):
    """Same length, new mtime: size alone would wave a swapped font through."""
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)
    expected = _sha(b"font")
    assert verify(f, expected) is True

    f.write_bytes(b"FONT")
    _bump_mtime(f)

    assert verify(f, expected) is False
    assert len(inner.calls) == 2


def test_the_same_path_with_another_expected_hash_is_not_answered_from_memory(tmp_path):
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)
    assert verify(f, _sha(b"font")) is True

    assert verify(f, _sha(b"something else")) is False
    assert len(inner.calls) == 2


def test_a_file_changing_while_it_is_hashed_is_not_remembered(tmp_path):
    """The pass describes bytes that no longer exist; trusting it would
    vouch for a file nobody verified."""
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    calls = []

    def racing(path, expected):
        calls.append(1)
        path.write_bytes(b"rewritten-with-a-different-length")
        return True

    verify = font_verify_cache.memoize_verify(racing)

    assert verify(f, "x") is True
    assert verify(f, "x") is True

    assert len(calls) == 2


def test_a_file_changed_mid_hash_then_restored_to_its_old_stat_is_not_remembered(tmp_path):
    """Same length, and the old mtime put back afterwards (a `copy2`-style
    restore): only a stat taken after the hash sees that the bytes moved."""
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    old_mtime_ns = f.stat().st_mtime_ns
    calls = []

    def racing(path, expected):
        calls.append(1)
        path.write_bytes(b"FONT")
        os.utime(path, ns=(old_mtime_ns + 5_000_000_000, old_mtime_ns + 5_000_000_000))
        return True

    verify = font_verify_cache.memoize_verify(racing)

    assert verify(f, "x") is True
    os.utime(f, ns=(old_mtime_ns, old_mtime_ns))
    assert verify(f, "x") is True

    assert len(calls) == 2


def test_results_for_different_paths_are_independent(tmp_path):
    a, b = tmp_path / "a.ttf", tmp_path / "b.ttf"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    inner = Counting()
    verify = font_verify_cache.memoize_verify(inner)

    assert verify(a, _sha(b"aaa")) is True
    assert verify(b, _sha(b"bbb")) is True
    assert verify(a, _sha(b"aaa")) is True
    assert verify(b, _sha(b"bbb")) is True

    assert len(inner.calls) == 2


# ---------------------------------------------------------------------------
# install, against the real BabelDOC
# ---------------------------------------------------------------------------


@pytest.fixture
def real_assets(monkeypatch):
    return pytest.importorskip("babeldoc.assets.assets")


def test_install_memoizes_the_function_babeldoc_calls(monkeypatch, tmp_path, real_assets):
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    original = Counting()
    # setattr first so teardown restores the real global after install().
    monkeypatch.setattr(real_assets, "verify_file", original)

    font_verify_cache.install()
    assert real_assets.verify_file(f, _sha(b"font")) is True
    assert real_assets.verify_file(f, _sha(b"font")) is True

    assert len(original.calls) == 1


def test_install_twice_does_not_wrap_a_second_time(monkeypatch, tmp_path, real_assets):
    f = tmp_path / "a.ttf"
    f.write_bytes(b"font")
    original = Counting()
    monkeypatch.setattr(real_assets, "verify_file", original)

    font_verify_cache.install()
    first = real_assets.verify_file
    font_verify_cache.install()
    # Call counts alone can't see a double wrap: the inner memo absorbs it.
    assert real_assets.verify_file is first
    assert first.__wrapped__ is original
    assert real_assets.verify_file(f, _sha(b"font")) is True
    assert real_assets.verify_file(f, _sha(b"font")) is True

    assert len(original.calls) == 1


def test_babeldocs_own_font_lookup_hashes_a_font_once(monkeypatch, tmp_path, real_assets):
    """The end-to-end claim: `get_font_and_metadata` is what every stage's
    font mapper calls, and it must reach the memo without downloading."""
    from babeldoc.assets import embedding_assets_metadata as meta

    font = tmp_path / "fake-font.ttf"
    font.write_bytes(b"pretend this is a font")
    monkeypatch.setitem(
        meta.EMBEDDING_FONT_METADATA, "fake-font.ttf", {"sha3_256": _sha(font.read_bytes())}
    )
    monkeypatch.setattr(real_assets, "get_cache_file_path", lambda name, sub: font)

    def no_download(*a, **k):
        raise AssertionError("a verified font must not be downloaded")

    # A wrong False goes to the upstream probe before `download_file`; stub
    # both so that fails here instead of reaching the network.
    monkeypatch.setattr(real_assets, "get_fastest_upstream_for_font", no_download)
    monkeypatch.setattr(real_assets, "download_file", no_download)
    original = Counting()
    monkeypatch.setattr(real_assets, "verify_file", original)

    font_verify_cache.install()
    first = real_assets.get_font_and_metadata("fake-font.ttf")
    second = real_assets.get_font_and_metadata("fake-font.ttf")

    assert first[0] == second[0] == font
    assert len(original.calls) == 1


# ---------------------------------------------------------------------------
# preverify_cached_fonts, against fake BabelDOC modules
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_babeldoc(monkeypatch, tmp_path):
    """A fake BabelDOC with three fonts in metadata, two on disk (one corrupt).

    Downloading entry points raise, so any test that calls into them fails
    loudly instead of reaching the network.
    """
    good, corrupt = tmp_path / "good.ttf", tmp_path / "corrupt.ttf"
    good.write_bytes(b"good font")
    corrupt.write_bytes(b"bad bytes")
    paths = {
        "good.ttf": good,
        "corrupt.ttf": corrupt,
        "absent.ttf": tmp_path / "absent.ttf",
    }
    metadata = {
        "good.ttf": {"sha3_256": _sha(b"good font")},
        "corrupt.ttf": {"sha3_256": _sha(b"the real bytes")},
        "absent.ttf": {"sha3_256": _sha(b"never downloaded")},
    }
    counter = Counting()
    downloads: list[tuple] = []

    def forbidden(*a, **k):
        downloads.append(a)
        raise AssertionError("pre-verification must never download")

    pkg = types.ModuleType("babeldoc")
    pkg.__path__ = []
    assets_pkg = types.ModuleType("babeldoc.assets")
    assets_pkg.__path__ = []
    assets = types.ModuleType("babeldoc.assets.assets")
    assets.verify_file = counter
    assets.get_font_and_metadata = forbidden
    assets.get_font_and_metadata_async = forbidden
    assets.download_file = forbidden
    assets.get_cache_file_path = lambda name, sub: paths[name]
    meta = types.ModuleType("babeldoc.assets.embedding_assets_metadata")
    meta.EMBEDDING_FONT_METADATA = metadata
    const = types.ModuleType("babeldoc.const")
    const.get_cache_file_path = lambda name, sub: paths[name]
    pkg.assets, pkg.const = assets_pkg, const
    assets_pkg.assets, assets_pkg.embedding_assets_metadata = assets, meta
    assets.EMBEDDING_FONT_METADATA = metadata
    for name, mod in {
        "babeldoc": pkg,
        "babeldoc.assets": assets_pkg,
        "babeldoc.assets.assets": assets,
        "babeldoc.assets.embedding_assets_metadata": meta,
        "babeldoc.const": const,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return types.SimpleNamespace(
        assets=assets, counter=counter, paths=paths, metadata=metadata, downloads=downloads
    )


def test_preverify_checks_only_fonts_on_disk_and_counts_the_passes(fake_babeldoc):
    passed = font_verify_cache.preverify_cached_fonts()

    checked = {p.name for p, _ in fake_babeldoc.counter.calls}
    assert checked == {"good.ttf", "corrupt.ttf"}
    assert passed == 1


def test_preverify_never_downloads_a_missing_font(fake_babeldoc):
    # Recorded as well as raised: a pre-check that swallowed the AssertionError
    # would otherwise hide the breach.
    font_verify_cache.preverify_cached_fonts()

    assert fake_babeldoc.downloads == []
    assert not fake_babeldoc.paths["absent.ttf"].exists()
    assert all(p.name != "absent.ttf" for p, _ in fake_babeldoc.counter.calls)


def test_preverified_fonts_are_not_hashed_again_by_babeldoc(fake_babeldoc):
    font_verify_cache.preverify_cached_fonts()
    hashed = len(fake_babeldoc.counter.calls)

    good = fake_babeldoc.paths["good.ttf"]
    expected = fake_babeldoc.metadata["good.ttf"]["sha3_256"]
    assert fake_babeldoc.assets.verify_file(good, expected) is True

    assert len(fake_babeldoc.counter.calls) == hashed


# ---------------------------------------------------------------------------
# The job path installs it too
# ---------------------------------------------------------------------------


def test_the_job_path_installs_the_memo(monkeypatch):
    """A Translate click can beat the boot warm thread; without this the first
    job would run every stage's font check unmemoized."""
    import desktop_pdf_translator.processors as processors_pkg
    from desktop_pdf_translator.api.routes import translation

    stub = types.ModuleType("processor")
    stub.PDFProcessor = type("PDFProcessor", (), {})
    events = types.ModuleType("events")
    events.EventType = type("EventType", (), {})
    monkeypatch.setattr(processors_pkg, "processor", stub, raising=False)
    monkeypatch.setattr(processors_pkg, "events", events, raising=False)
    monkeypatch.setitem(sys.modules, "desktop_pdf_translator.processors.processor", stub)
    monkeypatch.setitem(sys.modules, "desktop_pdf_translator.processors.events", events)
    calls: list[int] = []
    monkeypatch.setattr(font_verify_cache, "install", lambda: calls.append(1))
    # The job path installs the shared fonts too; for real, that would patch
    # BabelDOC's FontMapper for the rest of the run.
    monkeypatch.setattr(font_cache, "install", lambda: None)

    translation._load_engine()

    assert calls == [1]
