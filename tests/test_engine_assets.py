"""What "the offline engine is installed" means (#21).

`engine_assets` is consulted from three places that must agree — the setup
flow, the `POST /translate` pre-flight, and the in-job error message — so the
answers are worth pinning down.

Nothing here imports babeldoc or argostranslate. That isn't only about speed:
this module sits on the sidecar's boot path, and a test that pulled babeldoc in
would stop noticing if the module started doing the same
(`tests/test_sidecar_boot.py` is the other half of that guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from desktop_pdf_translator import engine_assets

CORE = ("models", "doclayout_yolo_docstructbench_imgsz1024.onnx")
MANIFEST = [CORE, ("models", "ch_PP-OCRv4_det_infer.onnx"), ("fonts", "a.ttf"), ("cmap", "b")]


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for `~/.cache/babeldoc`, with the manifest stubbed to match."""
    monkeypatch.setattr(engine_assets, "babeldoc_manifest", lambda: list(MANIFEST))
    monkeypatch.setattr(
        engine_assets,
        "_cache_path",
        lambda sub_folder, name: tmp_path / sub_folder / name,
    )
    return tmp_path


def _write(cache: Path, asset: tuple[str, str], content: bytes = b"x") -> None:
    path = cache / asset[0] / asset[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_counts_nothing_on_a_fresh_machine(cache: Path):
    assert engine_assets.babeldoc_asset_counts() == (0, len(MANIFEST))


def test_counts_only_what_is_actually_there(cache: Path):
    _write(cache, MANIFEST[0])
    _write(cache, MANIFEST[2])
    assert engine_assets.babeldoc_asset_counts() == (2, len(MANIFEST))


def test_a_zero_byte_asset_does_not_count(cache: Path):
    """An interrupted download leaves the file behind. Counting it would report
    a complete install and send the user into the failure this exists to
    prevent."""
    _write(cache, MANIFEST[0], b"")
    assert engine_assets.babeldoc_asset_counts() == (0, len(MANIFEST))


# ---------------------------------------------------------------------------
# What a *job* requires, as opposed to what setup completes
# ---------------------------------------------------------------------------


def test_a_job_needs_the_layout_model(cache: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: True)
    assert engine_assets.engine_ready("openai") is False
    _write(cache, CORE)
    assert engine_assets.engine_ready("openai") is True


def test_a_partial_cache_is_still_good_enough_to_translate(
    cache: Path, monkeypatch: pytest.MonkeyPatch
):
    """The regression this guards: every install predating the setup flow has a
    cache holding only the fonts and cmaps its documents happened to need.
    Requiring the whole manifest before a job may start would refuse work those
    machines have been doing successfully for months."""
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: True)
    _write(cache, CORE)
    present, total = engine_assets.babeldoc_asset_counts()
    assert present < total
    assert engine_assets.engine_ready("argos") is True


def test_argos_is_required_only_when_argos_will_run(
    cache: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: False)
    _write(cache, CORE)
    assert engine_assets.engine_ready("argos") is False
    # A keyed LLM user never calls Argos, so an absent pack can't hold them up.
    assert engine_assets.engine_ready("openai") is True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_status_reports_both_groups(cache: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: True)
    _write(cache, MANIFEST[0])
    groups = {g.id: g for g in engine_assets.engine_status()}
    assert set(groups) == {engine_assets.GROUP_BABELDOC, engine_assets.GROUP_ARGOS}
    assert groups[engine_assets.GROUP_BABELDOC].present == 1
    assert groups[engine_assets.GROUP_BABELDOC].total == len(MANIFEST)
    assert groups[engine_assets.GROUP_BABELDOC].ready is False
    assert groups[engine_assets.GROUP_ARGOS].ready is True


def test_status_is_ready_only_when_the_whole_manifest_is_present(
    cache: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(engine_assets, "argos_pack_ready", lambda: True)
    for asset in MANIFEST:
        _write(cache, asset)
    assert all(g.ready for g in engine_assets.engine_status())


def test_the_failure_message_never_reduces_to_a_number():
    """The whole user-visible symptom of #21 was the string "1" — `str()` of the
    `SystemExit` BabelDOC's asset layer raises. Whatever this returns, it has to
    be a sentence someone can act on."""
    message = engine_assets.describe_asset_failure()
    assert len(message) > 40
    assert "internet" in message
    assert engine_assets.describe_asset_failure("fonts").endswith("(failed while fetching fonts)")


# ---------------------------------------------------------------------------
# Assets the installer may have shipped
# ---------------------------------------------------------------------------


def test_bundled_zip_is_found_in_the_pyinstaller_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    staged = tmp_path / "babeldoc_assets"
    staged.mkdir()
    zip_path = staged / "offline_assets_deadbeef.zip"
    zip_path.write_bytes(b"PK")
    monkeypatch.setattr(engine_assets.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert engine_assets.bundled_babeldoc_zip() == zip_path


def test_no_bundled_zip_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Shipping the assets is optional — `pdfusion-sidecar.spec` warns and
    carries on — so the absence has to read as "download it", not as a fault."""
    monkeypatch.setattr(engine_assets.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(engine_assets, "_bundled_dirs", lambda *_: [tmp_path / "nope"])
    assert engine_assets.bundled_babeldoc_zip() is None
