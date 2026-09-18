"""What has to be on disk before a translation can run, and how to say so.

Two asset sets sit between a fresh install and a working translation, and
neither is part of the wheel:

- **BabelDOC's assets** — a DocLayout ONNX model, a table-detection ONNX model,
  34 embedding fonts, 146 cmaps and one tiktoken encoding, cached under
  `~/.cache/babeldoc`. BabelDOC fetches them lazily, from inside
  `TranslationConfig` construction, on the first chunk of the first job.
- **The Argos en→vi language pack** — ~80 MB, fetched on the first `translate()`.

Left alone, that is ~290 MB of silent downloads behind an overlay that says
"Initializing translator", and offline it is `exit(1)` deep inside BabelDOC —
which reaches the user as the string ``1``. This module is the single place that
knows what "installed" means, so the setup flow, the `POST /translate`
preflight and the in-job error message all agree.

Nothing here imports babeldoc or argostranslate at module level: the sidecar
imports it on the path to READY, and `tests/test_sidecar_boot.py` fails if
either lands in `sys.modules` that early.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

GROUP_BABELDOC = "babeldoc"
GROUP_ARGOS = "argos"

#: Refusal shown when a job is asked for before the engine is installed.
MISSING_ASSETS_MESSAGE = (
    "PDFusion's offline translation engine isn't installed yet. Run setup to "
    "install it — about 290 MB, from the installer's own copy if it shipped "
    "one, otherwise downloaded once."
)

# Worded to fit both callers: the setup flow, and a job that reached BabelDOC
# and found an asset missing.
_DOWNLOAD_FAILED_MESSAGE = (
    "PDFusion couldn't fetch the layout models, fonts and character maps its "
    "translation engine needs. They download once, from github.com and "
    "huggingface.co — connect to the internet and try again."
)


def describe_asset_failure(phase: Optional[str] = None) -> str:
    """One sentence for a failed asset install, optionally naming the phase.

    Everything under `babeldoc.assets.assets` reports failure by calling
    `exit(1)`, so by the time a caller sees it the only thing it carries is
    `SystemExit(1)` — `str()` of which is ``1``. There is no information to
    preserve, so this replaces the message wholesale rather than appending.
    """
    if phase:
        return f"{_DOWNLOAD_FAILED_MESSAGE} (failed while fetching {phase})"
    return _DOWNLOAD_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# Assets the installer may have shipped
# ---------------------------------------------------------------------------


def _bundled_dirs(frozen_name: str, repo_name: str) -> list[Path]:
    """Candidate directories for an asset staged by the installer.

    Same two-step shape as `translators/argos_translator.py:_find_bundled_pack`:
    the PyInstaller extraction root first (that's the shipped app), then the
    checkout's `assets/` (that's a developer who ran the fetch-offline-assets
    script).
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / frozen_name)
    try:
        # file → desktop_pdf_translator → src → repo root
        candidates.append(Path(__file__).resolve().parents[2] / "assets" / repo_name)
    except IndexError:
        pass
    return candidates


def bundled_babeldoc_zip() -> Optional[Path]:
    """The `offline_assets_<tag>.zip` shipped with the app, if there is one.

    The tag is a hash of BabelDOC's own asset manifest, so a zip built against a
    different babeldoc version is simply not the file
    `restore_offline_assets_package_async` looks for — which is the outcome we
    want: fall back to downloading rather than restoring mismatched assets.
    """
    for directory in _bundled_dirs("babeldoc_assets", "babeldoc"):
        if not directory.is_dir():
            continue
        for zip_path in sorted(directory.glob("offline_assets_*.zip")):
            return zip_path
    return None


def bundled_argos_pack() -> Optional[Path]:
    from .translators.argos_translator import _find_bundled_pack

    return _find_bundled_pack()


# ---------------------------------------------------------------------------
# What is installed right now
# ---------------------------------------------------------------------------


def _cache_path(sub_folder: str, name: str) -> Path:
    """Where BabelDOC keeps one cached asset (`~/.cache/babeldoc/<group>/<name>`).

    A named indirection rather than an inline import so the two callers below
    can be tested against a temp directory without babeldoc — which matters
    because this module is on the boot path and the suite's speed is a property
    it defends (`tests/test_sidecar_boot.py`).
    """
    from babeldoc.const import get_cache_file_path

    return get_cache_file_path(name, sub_folder)


def babeldoc_manifest() -> list[tuple[str, str]]:
    """`(cache sub-folder, file name)` for every asset BabelDOC needs."""
    from babeldoc.assets.assets import generate_all_assets_file_list

    return [
        (sub_folder, desc["name"])
        for sub_folder, descs in generate_all_assets_file_list().items()
        for desc in descs
    ]


def babeldoc_asset_counts() -> tuple[int, int]:
    """`(present, total)` BabelDOC assets, by existence and non-zero size.

    Deliberately not `assets.verify_file`, which is what BabelDOC itself uses:
    that hashes every file, and the set is ~210 MB. This runs on every
    `POST /translate` and twice a second while an install is in flight, so it
    has to stay one stat call per file. Integrity remains BabelDOC's job — it
    re-verifies and re-downloads anything it doesn't like, and a corrupt file is
    a much rarer case than an absent one.
    """
    manifest = babeldoc_manifest()
    present = 0
    for sub_folder, name in manifest:
        try:
            if _cache_path(sub_folder, name).stat().st_size > 0:
                present += 1
        except OSError:
            continue
    return present, len(manifest)


#: The one asset a run cannot start without. BabelDOC resolves it from inside
#: `TranslationConfig` construction (`processor.py:_create_babeldoc_config`), and
#: its absence offline is exactly the `exit(1)` that reaches the user as ``1``.
#: The rest of the manifest is fetched on demand and per document — the fonts
#: for the target language, the cmaps for the source encoding — so a cache with
#: only some of them is a cache that has been working fine, and the preflight
#: must not start refusing jobs for it. The full manifest is what *setup*
#: completes; this is what a *job* requires.
_CORE_ASSET = ("models", "doclayout_yolo_docstructbench_imgsz1024.onnx")


def babeldoc_core_ready() -> bool:
    """Whether the layout model a job cannot start without is on disk."""
    try:
        return _cache_path(*_CORE_ASSET).stat().st_size > 0
    except OSError:
        return False


def argos_pack_installed(from_code: str = "en", to_code: str = "vi") -> bool:
    """Whether the Argos pack for a pair is already unpacked on disk.

    Reads the packages directory rather than calling
    `argostranslate.translate.get_installed_languages()`, which drags in stanza
    and ctranslate2 — far too much for a question asked on every preflight.
    """
    try:
        from argostranslate.settings import package_data_dir
    except Exception as exc:  # noqa: BLE001 — argostranslate may not be installed
        logger.debug("Argos settings unavailable: %s", exc)
        return False
    try:
        entries = list(Path(package_data_dir).iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            meta = json.loads((entry / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("from_code") == from_code and meta.get("to_code") == to_code:
            return True
    return False


def argos_pack_ready() -> bool:
    """Argos is usable without touching the network."""
    return argos_pack_installed() or bundled_argos_pack() is not None


# ---------------------------------------------------------------------------
# Aggregate view
# ---------------------------------------------------------------------------


@dataclass
class AssetGroupStatus:
    id: str
    label: str
    ready: bool
    present: int
    total: int
    detail: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "ready": self.ready,
            "present": self.present,
            "total": self.total,
            "detail": self.detail,
        }


def engine_status() -> list[AssetGroupStatus]:
    """Per-group installed state. No network, no hashing."""
    present, total = babeldoc_asset_counts()
    babeldoc = AssetGroupStatus(
        id=GROUP_BABELDOC,
        label="Layout engine",
        ready=present >= total,
        present=present,
        total=total,
        detail="Page-layout models, fonts and character maps (~210 MB)",
    )
    argos_ready = argos_pack_ready()
    argos = AssetGroupStatus(
        id=GROUP_ARGOS,
        label="Offline translator",
        ready=argos_ready,
        present=1 if argos_ready else 0,
        total=1,
        detail="English to Vietnamese language pack (~80 MB)",
    )
    return [babeldoc, argos]


def engine_ready(service: Optional[str] = None) -> bool:
    """Whether a job for `service` can start without fetching anything.

    BabelDOC's layout model is needed whatever the translator is — it is the
    pipeline. The Argos pack is only needed when Argos is the *effective*
    service, so a user with a working OpenAI key isn't held up by a pack they
    will never call.

    Note this asks less than `engine_status()` reports: see `_CORE_ASSET`. A
    half-populated cache is the normal state of every install that predates the
    setup flow, and refusing its jobs would be a regression dressed as a
    safeguard.
    """
    if not babeldoc_core_ready():
        return False
    if service == GROUP_ARGOS and not argos_pack_ready():
        return False
    return True
