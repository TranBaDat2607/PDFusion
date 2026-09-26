"""The models each key can use, as last listed — a disposable cache (#84).

A listing can always be asked for again, so it lives in the cache tier, never
in `pdfusion.db` (the system of record). Rows are keyed by
`(provider_id, base_url)`: an Ollama endpoint and the provider's own serve
different models under the same key section.

Nothing derived from a key is stored — not the key, not a hash of it. A key
changing is signalled by `invalidate`, which `PUT /config` calls when it saves
one; a key swapped in `.env` between runs is bounded only by `TTL_MS`.

Stdlib-only at import time: `api/routes/config.py` imports this on the boot
path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

from ..storage.migrations import Migration, migrate
from ..storage.sqlite import ThreadLocalConnections, now_ms
from ..utils.paths import appdata_dir
from .listing import ListedModel, Listing
from .registry import provider

logger = logging.getLogger(__name__)

KeyState = Literal["unverified", "valid", "invalid", "unreadable"]

# Model lists change on the order of weeks; a day keeps a newly released model
# from staying invisible for long without listing on every picker open.
TTL_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class CatalogEntry:
    provider_id: str
    base_url: Optional[str]
    # `None` once the key was refused: there is no list to offer.
    listing: Optional[Listing]
    fetched_at: Optional[int]
    key_state: KeyState
    verified_at: int


def _v1_catalog(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE catalog (
            provider_id TEXT NOT NULL,
            -- '' for the provider's own endpoint: NULL never equals NULL, so
            -- it can't be half of a primary key that is meant to collide.
            base_url TEXT NOT NULL,
            models_json TEXT,             -- NULL once the key was refused
            hidden_json TEXT,
            fetched_at INTEGER,           -- Unix ms, UTC
            key_state TEXT NOT NULL,
            verified_at INTEGER NOT NULL, -- Unix ms, UTC
            PRIMARY KEY (provider_id, base_url)
        )
        """
    )


_MIGRATIONS = (Migration(1, "model catalog", _v1_catalog),)


def _encode(models: tuple) -> str:
    return json.dumps([asdict(model) for model in models])


def _decode(text: str) -> tuple:
    return tuple(ListedModel(**fields) for fields in json.loads(text))


class ModelCatalog:
    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else appdata_dir() / "model_catalog"
        self.db_path = self.cache_dir / "catalog.db"
        self._connections = ThreadLocalConnections(self.db_path)
        self._write_lock = threading.Lock()
        # Migrated on first use, not here: the singleton can be built on the
        # event loop, as `TranslationCache` is.
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    migrate(self.db_path, _MIGRATIONS, on_too_new="reset")
                    self._schema_ready = True
        return self._connections.get()

    def get(self, provider_id: str, base_url: Optional[str]) -> Optional[CatalogEntry]:
        row = self._conn().execute(
            "SELECT * FROM catalog WHERE provider_id = ? AND base_url = ?",
            (provider_id, base_url or ""),
        ).fetchone()
        if row is None:
            return None
        listing = None
        if row["models_json"] is not None:
            listing = Listing(
                models=_decode(row["models_json"]),
                hidden=_decode(row["hidden_json"] or "[]"),
            )
        return CatalogEntry(
            provider_id=provider_id,
            base_url=base_url or None,
            listing=listing,
            fetched_at=row["fetched_at"],
            key_state=row["key_state"],
            verified_at=row["verified_at"],
        )

    def put(
        self,
        provider_id: str,
        base_url: Optional[str],
        listing: Listing,
        *,
        now: Optional[int] = None,
    ) -> None:
        now = now_ms() if now is None else now
        self._write(
            provider_id, base_url, _encode(listing.models), _encode(listing.hidden),
            now, "valid", now,
        )

    def mark_invalid(
        self, provider_id: str, base_url: Optional[str], *, now: Optional[int] = None
    ) -> None:
        now = now_ms() if now is None else now
        self._write(provider_id, base_url, None, None, None, "invalid", now)

    def invalidate(self, provider_id: str) -> int:
        conn = self._conn()
        with self._write_lock:
            removed = conn.execute(
                "DELETE FROM catalog WHERE provider_id = ?", (provider_id,)
            ).rowcount
            conn.commit()
        return removed

    def _write(self, provider_id, base_url, models, hidden, fetched_at, state, verified_at):
        conn = self._conn()
        with self._write_lock:
            conn.execute(
                "INSERT OR REPLACE INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?)",
                (provider_id, base_url or "", models, hidden, fetched_at, state, verified_at),
            )
            conn.commit()


def is_fresh(entry: CatalogEntry, now: Optional[int] = None) -> bool:
    if entry.listing is None or entry.fetched_at is None:
        return False
    now = now_ms() if now is None else now
    return now - entry.fetched_at < TTL_MS


_INSTANCE: Optional[ModelCatalog] = None
_INSTANCE_LOCK = threading.Lock()


def get_model_catalog() -> ModelCatalog:
    """Process-wide singleton, built on first call."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = ModelCatalog()
        return _INSTANCE


# ---------------------------------------------------------------------------
# Listing, for the routes
# ---------------------------------------------------------------------------

# Deadline for one listing. Generous enough for a cold TLS handshake to a
# provider, short enough that Settings → Save still feels like a save.
PROBE_TIMEOUT_S = 20.0


class KeyRejected(Exception):
    """The endpoint refused the key (401/403): the key is wrong, not unlucky."""


class ListingFailed(Exception):
    """Anything else — a server that is down, a timeout, a malformed reply."""


async def list_models(provider_id: str, api_key: str, base_url: Optional[str]) -> Listing:
    """List what `api_key` can use at `base_url` with the provider's lister.

    Off the event loop — the lister imports its SDK and makes a blocking call —
    and under a deadline: Gemini's client honours its timeout loosely, and a
    listing left to hang on the loop used to freeze every stream meanwhile.
    A timed-out thread is left to finish on its own; nothing reads its result.

    The one seam the route tests replace.
    """
    from ..translators.base import is_fatal_translation_error

    def run() -> Listing:
        return provider(provider_id).lister()(api_key, base_url)

    try:
        return await asyncio.wait_for(asyncio.to_thread(run), timeout=PROBE_TIMEOUT_S)
    except (asyncio.TimeoutError, TimeoutError):
        raise ListingFailed(f"Timed out contacting {base_url or provider_id}.") from None
    except Exception as exc:  # noqa: BLE001 — sorted into the two outcomes
        # The same test that aborts a translation on a bad key, so the two
        # can't disagree about one. It also catches Gemini, which answers a
        # bad key with 400 API_KEY_INVALID rather than 401.
        if is_fatal_translation_error(exc):
            raise KeyRejected(str(exc)) from exc
        raise ListingFailed(str(exc)) from exc
