"""Content-addressed PDF-level translation cache.

Mirrors `translators/translation_cache.py` (SQLite WAL, per-thread connections,
singleton, lazy init) but caches *whole translated PDFs* instead of individual
paragraphs.

Cache key = sha256(file_hash | lang_in | lang_out | service | model | pipeline_version)
where file_hash is sha256 of the input PDF bytes. A hit lets `process_pdf`
skip the entire BabelDOC pipeline (layout / typeset / render / save) by
copying the cached PDF into the live output directory and emitting synthetic
SSE events.

Storage layout:
    ~/AppData/Local/PDFusion/translated_pdf_cache/
        index.db              -- SQLite WAL
        files/<key>.pdf       -- content-addressed translated PDFs
"""

import hashlib
import logging
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Bump when BabelDOC config fields that affect output change (font,
# watermark_output_mode, min_text_length, skip_scanned_detection,
# auto_extract_glossary, enhance_compatibility, pool_max_workers). Old entries
# stay on disk but stop matching; they get LRU-evicted naturally.
PIPELINE_VERSION = "1"

_HASH_CHUNK = 1024 * 1024  # 1 MB streaming reads


def _default_cache_dir() -> Path:
    return Path.home() / "AppData" / "Local" / "PDFusion" / "translated_pdf_cache"


def compute_file_hash(path: Path) -> str:
    """Streaming SHA-256 of the input PDF. ~100-300 ms on a 50 MB PDF."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _make_cache_key(
    file_hash: str,
    lang_in: str,
    lang_out: str,
    service: str,
    model: Optional[str],
    pipeline_version: str,
) -> str:
    # Source language deliberately excluded from the key. Output bytes for a
    # given (file_hash, target_lang, service, model) are independent of what
    # the user picked as `source_lang` — Argos forces auto→en, LLMs auto-
    # detect from content, neither uses the source-lang hint to change output.
    # Keying on it would create separate entries for "auto" vs "en" on the
    # same English PDF and miss valid hits when the user switches the dropdown.
    # `lang_in` accepted for API stability / debug logs only.
    del lang_in  # noqa: F841 — intentionally unused
    payload = f"{file_hash}|{lang_out}|{service}|{model or ''}|{pipeline_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CacheHit:
    cached_path: Path
    cached_at: str            # ISO8601
    hit_count: int
    original_filename: str


class PDFTranslationCache:
    """SQLite-backed PDF cache with LRU eviction.

    Thread-safe: reads are concurrent via SQLite WAL; writes serialize through
    `_write_lock`.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_size_mb: float = 1000.0,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self.files_dir = self.cache_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_mb = max_size_mb
        self.db_path = self.cache_dir / "index.db"

        self._write_lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._stats_lock = threading.Lock()
        self._tls = threading.local()

        self._init_database()
        logger.info(
            "PDFTranslationCache initialized at %s (cap=%.1f MB)",
            self.db_path, self.max_size_mb,
        )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._tls.conn = conn
        return conn

    def _init_database(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pdf_translations (
                    cache_key TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    service TEXT NOT NULL,
                    model TEXT,
                    pipeline_version TEXT NOT NULL,
                    cached_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    last_used TEXT,
                    hit_count INTEGER DEFAULT 0,
                    file_size_bytes INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pdf_last_used ON pdf_translations(last_used)"
            )
            conn.commit()

    def lookup(
        self,
        file_path: Path,
        source_lang: str,
        target_lang: str,
        service: str,
        model: Optional[str],
    ) -> Optional[CacheHit]:
        """Return a CacheHit or None. Bumps hit_count + last_used on hit.

        Returns None (and silently deletes the stale row) if the cached PDF
        file is missing on disk — protects against users wiping `files/`
        without clearing `index.db`.
        """
        try:
            file_hash = compute_file_hash(file_path)
        except OSError as exc:
            logger.warning("PDF cache lookup: hash failed for %s (%s)", file_path, exc)
            self._bump(miss=True)
            return None

        key = _make_cache_key(
            file_hash, source_lang, target_lang, service, model, PIPELINE_VERSION
        )
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT cached_path, cached_at, hit_count, original_filename "
                "FROM pdf_translations WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                self._bump(miss=True)
                return None

            cached_path = Path(row["cached_path"])
            if not cached_path.exists():
                logger.info(
                    "PDF cache row %s points to missing file %s — evicting",
                    key[:12], cached_path,
                )
                with self._write_lock:
                    conn.execute(
                        "DELETE FROM pdf_translations WHERE cache_key = ?", (key,)
                    )
                    conn.commit()
                self._bump(miss=True)
                return None

            now = datetime.now().isoformat()
            try:
                with self._write_lock:
                    conn.execute(
                        "UPDATE pdf_translations SET hit_count = hit_count + 1, "
                        "last_used = ? WHERE cache_key = ?",
                        (now, key),
                    )
                    conn.commit()
            except sqlite3.OperationalError:
                pass

            self._bump(miss=False)
            return CacheHit(
                cached_path=cached_path,
                cached_at=row["cached_at"],
                hit_count=int(row["hit_count"]) + 1,
                original_filename=row["original_filename"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF cache lookup failed: %s", exc)
            self._bump(miss=True)
            return None

    def store(
        self,
        file_path: Path,
        translated_path: Path,
        source_lang: str,
        target_lang: str,
        service: str,
        model: Optional[str],
    ) -> Optional[Path]:
        """Copy `translated_path` into the cache; insert/replace the row.
        Returns the cached file path on success, None on failure (non-fatal).
        """
        if not translated_path.exists():
            logger.warning("PDF cache store: translated file missing %s", translated_path)
            return None
        try:
            file_hash = compute_file_hash(file_path)
        except OSError as exc:
            logger.warning("PDF cache store: input hash failed (%s)", exc)
            return None

        key = _make_cache_key(
            file_hash, source_lang, target_lang, service, model, PIPELINE_VERSION
        )
        dest = self.files_dir / f"{key}.pdf"
        # Copy via .tmp then atomic rename so a concurrent reader (PdfViewer)
        # or another `lookup()` materialize never sees a half-written file at
        # `dest`. os.replace is atomic on Windows and POSIX.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            shutil.copyfile(translated_path, tmp)
            os.replace(tmp, dest)
        except OSError as exc:
            logger.warning("PDF cache store: copy failed (%s)", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        try:
            size_bytes = dest.stat().st_size
        except OSError:
            # stat failed (transient AV/OneDrive lock). The bytes did land
            # (copyfile + os.replace succeeded), so fall back to the source
            # size rather than recording 0 — that would silently break LRU
            # accounting (under-reported total → cap never enforced, then
            # `total -= 0` loops on eviction without freeing space).
            try:
                size_bytes = translated_path.stat().st_size
            except OSError:
                size_bytes = 0
            logger.warning(
                "PDF cache store: dest.stat() failed; using src size=%d",
                size_bytes,
            )

        now = datetime.now().isoformat()
        try:
            conn = self._conn()
            with self._write_lock:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pdf_translations
                    (cache_key, file_hash, source_lang, target_lang, service,
                     model, pipeline_version, cached_path, original_filename,
                     cached_at, last_used, hit_count, file_size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        key, file_hash, source_lang, target_lang, service,
                        model, PIPELINE_VERSION, str(dest), file_path.name,
                        now, now, size_bytes,
                    ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF cache store: DB insert failed (%s)", exc)
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        self._enforce_lru_cap()
        return dest

    def _refresh_cap_from_settings(self) -> None:
        """Re-read `pdf_cache_max_size_mb` from settings so cap changes apply
        without a sidecar restart. Singleton init only reads it once, hence
        this top-up on every enforcement pass. Silently no-ops if settings
        are unavailable (e.g. during unit tests with the bare cache)."""
        try:
            from ..config import get_settings
            new_cap = float(
                getattr(get_settings().translation, "pdf_cache_max_size_mb",
                        self.max_size_mb)
            )
            if new_cap != self.max_size_mb:
                logger.info(
                    "PDF cache cap updated: %.1f MB -> %.1f MB",
                    self.max_size_mb, new_cap,
                )
                self.max_size_mb = new_cap
        except Exception:
            pass

    def _enforce_lru_cap(self) -> None:
        """Drop oldest-`last_used` entries (+ their files) until total size
        is under `max_size_mb`. Best-effort — file delete errors are logged."""
        self._refresh_cap_from_settings()
        try:
            conn = self._conn()
            total = conn.execute(
                "SELECT COALESCE(SUM(file_size_bytes), 0) FROM pdf_translations"
            ).fetchone()[0]
            cap_bytes = int(self.max_size_mb * 1024 * 1024)
            if total <= cap_bytes:
                return
            rows = conn.execute(
                "SELECT cache_key, cached_path, file_size_bytes FROM pdf_translations "
                "ORDER BY COALESCE(last_used, cached_at) ASC"
            ).fetchall()
            to_remove: list[tuple[str, str]] = []
            for row in rows:
                if total <= cap_bytes:
                    break
                to_remove.append((row["cache_key"], row["cached_path"]))
                total -= int(row["file_size_bytes"])
            if not to_remove:
                return
            with self._write_lock:
                conn.executemany(
                    "DELETE FROM pdf_translations WHERE cache_key = ?",
                    [(k,) for k, _ in to_remove],
                )
                conn.commit()
            for _, path in to_remove:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("PDF cache LRU: unlink %s failed: %s", path, exc)
            logger.info("PDF cache LRU: evicted %d entries", len(to_remove))
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF cache LRU sweep failed: %s", exc)

    def stats(self) -> Dict[str, Any]:
        try:
            conn = self._conn()
            total = conn.execute("SELECT COUNT(*) FROM pdf_translations").fetchone()[0]
            size_bytes = conn.execute(
                "SELECT COALESCE(SUM(file_size_bytes), 0) FROM pdf_translations"
            ).fetchone()[0]
            by_service = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT service, COUNT(*) FROM pdf_translations GROUP BY service"
                ).fetchall()
            }
            with self._stats_lock:
                hits, misses = self._hits, self._misses
            return {
                "entries": total,
                "size_mb": round(size_bytes / (1024 * 1024), 3),
                "by_service": by_service,
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / max(1, hits + misses), 3),
                "cache_dir": str(self.cache_dir),
                "max_size_mb": self.max_size_mb,
                "pipeline_version": PIPELINE_VERSION,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF cache stats failed: %s", exc)
            return {}

    def clear_all(self) -> int:
        """Drop every row and delete every cached file. Returns row count
        before deletion."""
        try:
            conn = self._conn()
            with self._write_lock:
                count = conn.execute("SELECT COUNT(*) FROM pdf_translations").fetchone()[0]
                conn.execute("DELETE FROM pdf_translations")
                conn.commit()
                conn.execute("VACUUM")
            for f in self.files_dir.glob("*.pdf"):
                try:
                    f.unlink()
                except OSError as exc:
                    logger.warning("clear_all: unlink %s failed: %s", f, exc)
            return int(count)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF cache clear_all failed: %s", exc)
            return 0

    def _bump(self, miss: bool) -> None:
        with self._stats_lock:
            if miss:
                self._misses += 1
            else:
                self._hits += 1


_INSTANCE: Optional[PDFTranslationCache] = None
_INSTANCE_LOCK = threading.Lock()


def get_pdf_cache() -> PDFTranslationCache:
    """Process-wide singleton. Reads the size cap from settings if available."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        max_size_mb = 1000.0
        try:
            from ..config import get_settings
            max_size_mb = float(
                getattr(get_settings().translation, "pdf_cache_max_size_mb", max_size_mb)
            )
        except Exception:
            pass
        _INSTANCE = PDFTranslationCache(max_size_mb=max_size_mb)
        return _INSTANCE
