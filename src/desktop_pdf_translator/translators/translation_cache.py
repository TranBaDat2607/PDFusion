"""Persistent on-disk translation cache (SQLite, WAL).

Stdlib sqlite3, TTL via ISO8601 expires_at, single index on expires_at for
cheap GC. WAL mode lets many readers run while a
single writer holds the lock — fine for translator throughput where we read
much more than we write.

Cache key is a SHA-256 of `lang_in|lang_out|service|model|source_text`, which
means: changing the model invalidates everything for that model; identical
inputs across services/models stay independent. Argos has no model variance so
its `model` field is the fixed "argostranslate" string from ArgosSettings.

The cache is a process-wide singleton (`get_translation_cache()`); construction
is lazy so importing the module is cheap.
"""

import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def _default_cache_dir() -> Path:
    """Resolve the default cache directory under Windows AppData.

    Matches the convention used by `ConfigManager` (config/manager.py:33) and
    the RAG ChromaDB store, so all PDFusion on-disk state lives under
    `~/AppData/Local/PDFusion/`.
    """
    return Path.home() / "AppData" / "Local" / "PDFusion" / "translation_cache"


def _make_cache_key(
    source_text: str,
    lang_in: str,
    lang_out: str,
    service: str,
    model: Optional[str],
) -> str:
    payload = f"{lang_in}|{lang_out}|{service}|{model or ''}|{source_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TranslationCache:
    """SQLite-backed translation cache with TTL.

    All public methods are thread-safe. Reads are concurrent (SQLite WAL);
    writes serialize through `_write_lock`.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl_days: int = 30,
        max_size_mb: float = 500.0,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days
        self.max_size_mb = max_size_mb
        self.db_path = self.cache_dir / "cache.db"

        self._write_lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._stats_lock = threading.Lock()
        # Per-thread sqlite connection. Reused across translate() calls so
        # hundreds of paragraphs in one PDF don't each pay the connect+WAL
        # handshake (~1-3 ms on Windows).
        self._tls = threading.local()

        self._init_database()
        logger.info(
            "TranslationCache initialized at %s (ttl=%d days, cap=%.1f MB)",
            self.db_path, self.ttl_days, self.max_size_mb,
        )

    def _conn(self) -> sqlite3.Connection:
        """Per-thread sqlite connection, opened on first use."""
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
                CREATE TABLE IF NOT EXISTS translations (
                    cache_key TEXT PRIMARY KEY,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    service TEXT NOT NULL,
                    model TEXT,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    last_used TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires ON translations(expires_at)"
            )
            conn.commit()

    def get(
        self,
        source_text: str,
        lang_in: str,
        lang_out: str,
        service: str,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """Return cached translation or None. Records hit/miss stats."""
        key = _make_cache_key(source_text, lang_in, lang_out, service, model)
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT translated_text, expires_at FROM translations WHERE cache_key = ?",
                (key,),
            ).fetchone()

            if row is None:
                self._bump(miss=True)
                return None

            if datetime.fromisoformat(row["expires_at"]) < datetime.now():
                # Expired — leave row in place; clear_expired() reaps later.
                self._bump(miss=True)
                return None

            try:
                with self._write_lock:
                    conn.execute(
                        "UPDATE translations SET hit_count = hit_count + 1, last_used = ? WHERE cache_key = ?",
                        (datetime.now().isoformat(), key),
                    )
                    conn.commit()
            except sqlite3.OperationalError:
                pass

            self._bump(miss=False)
            return row["translated_text"]
        except Exception as e:
            logger.warning("Cache get failed: %s", e)
            self._bump(miss=True)
            return None

    def set(
        self,
        source_text: str,
        translated_text: str,
        lang_in: str,
        lang_out: str,
        service: str,
        model: Optional[str] = None,
    ) -> bool:
        """Insert or replace a cache entry. Returns True on success."""
        if not translated_text:
            return False
        key = _make_cache_key(source_text, lang_in, lang_out, service, model)
        now = datetime.now()
        expires_at = now + timedelta(days=self.ttl_days)
        try:
            conn = self._conn()
            with self._write_lock:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO translations
                    (cache_key, source_lang, target_lang, service, model,
                     source_text, translated_text, cached_at, expires_at,
                     hit_count, last_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        key, lang_in, lang_out, service, model,
                        source_text, translated_text,
                        now.isoformat(), expires_at.isoformat(),
                    ),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.warning("Cache set failed: %s", e)
            return False

    def clear_expired(self) -> int:
        try:
            now = datetime.now().isoformat()
            conn = self._conn()
            with self._write_lock:
                cur = conn.execute(
                    "DELETE FROM translations WHERE expires_at < ?", (now,)
                )
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.warning("clear_expired failed: %s", e)
            return 0

    def clear_all(self) -> int:
        try:
            conn = self._conn()
            with self._write_lock:
                cur = conn.execute("DELETE FROM translations")
                conn.commit()
                conn.execute("VACUUM")
                return cur.rowcount
        except Exception as e:
            logger.warning("clear_all failed: %s", e)
            return 0

    def stats(self) -> Dict[str, Any]:
        try:
            conn = self._conn()
            total = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM translations WHERE expires_at < ?",
                (datetime.now().isoformat(),),
            ).fetchone()[0]
            by_service = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT service, COUNT(*) FROM translations GROUP BY service"
                ).fetchall()
            }
            size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
            with self._stats_lock:
                hits, misses = self._hits, self._misses
            return {
                "entries": total,
                "active": total - expired,
                "expired": expired,
                "size_mb": round(size_bytes / (1024 * 1024), 3),
                "by_service": by_service,
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / max(1, hits + misses), 3),
                "cache_dir": str(self.cache_dir),
                "ttl_days": self.ttl_days,
                "max_size_mb": self.max_size_mb,
            }
        except Exception as e:
            logger.warning("stats failed: %s", e)
            return {}

    def _bump(self, miss: bool) -> None:
        with self._stats_lock:
            if miss:
                self._misses += 1
            else:
                self._hits += 1


_INSTANCE: Optional[TranslationCache] = None
_INSTANCE_LOCK = threading.Lock()


def _cache_enabled() -> bool:
    try:
        from ..config import get_settings
        return bool(get_settings().translation.cache_translations)
    except Exception:
        return False


def llm_cache_get(
    source_text: str,
    lang_in: str,
    lang_out: str,
    service: str,
    model: Optional[str],
) -> Optional[str]:
    """Cache lookup wrapper for LLM translators. Silently bypasses when caching
    is disabled in settings, so translator code stays clean."""
    if not _cache_enabled():
        return None
    try:
        return get_translation_cache().get(
            source_text=source_text,
            lang_in=lang_in,
            lang_out=lang_out,
            service=service,
            model=model,
        )
    except Exception as e:
        logger.warning("llm_cache_get failed: %s", e)
        return None


def llm_cache_set(
    source_text: str,
    translated_text: str,
    lang_in: str,
    lang_out: str,
    service: str,
    model: Optional[str],
) -> None:
    if not _cache_enabled():
        return
    try:
        get_translation_cache().set(
            source_text=source_text,
            translated_text=translated_text,
            lang_in=lang_in,
            lang_out=lang_out,
            service=service,
            model=model,
        )
    except Exception as e:
        logger.warning("llm_cache_set failed: %s", e)


def get_translation_cache() -> TranslationCache:
    """Process-wide singleton. Constructed on first call, reads TTL/cap from
    current settings if available, falls back to defaults otherwise."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        ttl_days = 30
        max_size_mb = 500.0
        try:
            from ..config import get_settings
            s = get_settings().translation
            ttl_days = getattr(s, "cache_ttl_days", ttl_days)
            max_size_mb = getattr(s, "cache_max_size_mb", max_size_mb)
        except Exception:
            pass
        _INSTANCE = TranslationCache(ttl_days=ttl_days, max_size_mb=max_size_mb)
        return _INSTANCE
