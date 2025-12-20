"""
Paper caching system for deep search.
Caches fetched academic papers to avoid redundant API calls.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PaperCache:
    """Local cache for academic papers with TTL management."""

    def __init__(self, cache_dir: str = "data/paper_cache", ttl_days: int = 7):
        """
        Initialize paper cache.

        Args:
            cache_dir: Directory for cache storage
            ttl_days: Time-to-live in days (default: 7)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.ttl_days = ttl_days
        self.db_path = self.cache_dir / "papers.db"

        self._init_database()
        logger.info(f"Paper cache initialized at {self.cache_dir} with {ttl_days}-day TTL")

    def _init_database(self):
        """Initialize SQLite database for paper metadata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS papers (
                    paper_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT,
                    authors TEXT,
                    abstract TEXT,
                    year INTEGER,
                    citation_count INTEGER,
                    pdf_url TEXT,
                    metadata TEXT,
                    cached_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source
                ON papers(source)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires
                ON papers(expires_at)
            """)

            conn.commit()
            logger.debug("Paper cache database initialized")

    async def get(self, paper_id: str, source: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve paper from cache if exists and not expired.

        Args:
            paper_id: Paper identifier
            source: Source database (pubmed, semantic_scholar, core, arxiv)

        Returns:
            Paper metadata dict if found and valid, None otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM papers
                    WHERE paper_id = ? AND source = ?
                """, (paper_id, source))

                row = cursor.fetchone()

                if not row:
                    logger.debug(f"Cache miss: {source}/{paper_id}")
                    return None

                # Check if expired
                expires_at = datetime.fromisoformat(row['expires_at'])
                if datetime.now() > expires_at:
                    logger.debug(f"Cache expired: {source}/{paper_id}")
                    # Clean up expired entry
                    conn.execute("""
                        DELETE FROM papers
                        WHERE paper_id = ? AND source = ?
                    """, (paper_id, source))
                    conn.commit()
                    return None

                # Parse metadata
                paper_data = {
                    'paper_id': row['paper_id'],
                    'source': row['source'],
                    'title': row['title'],
                    'authors': json.loads(row['authors']) if row['authors'] else [],
                    'abstract': row['abstract'],
                    'year': row['year'],
                    'citation_count': row['citation_count'],
                    'pdf_url': row['pdf_url'],
                }

                # Add extra metadata if present
                if row['metadata']:
                    extra = json.loads(row['metadata'])
                    paper_data.update(extra)

                logger.debug(f"Cache hit: {source}/{paper_id}")
                return paper_data

        except Exception as e:
            logger.error(f"Error retrieving from cache: {e}")
            return None

    async def set(self, paper_data: Dict[str, Any]) -> bool:
        """
        Store paper in cache.

        Args:
            paper_data: Paper metadata dict with keys:
                - paper_id: str (required)
                - source: str (required)
                - title: str
                - authors: List[str]
                - abstract: str
                - year: int
                - citation_count: int
                - pdf_url: str
                - (other fields stored in metadata JSON)

        Returns:
            True if successfully cached, False otherwise
        """
        try:
            paper_id = paper_data.get('paper_id')
            source = paper_data.get('source')

            if not paper_id or not source:
                logger.warning("Cannot cache paper without paper_id and source")
                return False

            # Extract standard fields
            title = paper_data.get('title', '')
            authors = json.dumps(paper_data.get('authors', []))
            abstract = paper_data.get('abstract', '')
            year = paper_data.get('year')
            citation_count = paper_data.get('citation_count')
            pdf_url = paper_data.get('pdf_url')

            # Store extra fields in metadata JSON
            extra_fields = {}
            standard_keys = {'paper_id', 'source', 'title', 'authors', 'abstract',
                           'year', 'citation_count', 'pdf_url'}
            for key, value in paper_data.items():
                if key not in standard_keys:
                    extra_fields[key] = value

            metadata = json.dumps(extra_fields) if extra_fields else None

            # Calculate expiration
            cached_at = datetime.now()
            expires_at = cached_at + timedelta(days=self.ttl_days)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO papers
                    (paper_id, source, title, authors, abstract, year,
                     citation_count, pdf_url, metadata, cached_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    paper_id, source, title, authors, abstract, year,
                    citation_count, pdf_url, metadata,
                    cached_at.isoformat(), expires_at.isoformat()
                ))
                conn.commit()

            logger.debug(f"Cached paper: {source}/{paper_id}")
            return True

        except Exception as e:
            logger.error(f"Error caching paper: {e}")
            return False

    def clear_expired(self) -> int:
        """
        Remove all expired entries from cache.

        Returns:
            Number of entries removed
        """
        try:
            now = datetime.now().isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM papers
                    WHERE expires_at < ?
                """, (now,))

                count = cursor.rowcount
                conn.commit()

            if count > 0:
                logger.info(f"Cleared {count} expired paper(s) from cache")

            return count

        except Exception as e:
            logger.error(f"Error clearing expired papers: {e}")
            return 0

    def clear_all(self) -> int:
        """
        Clear entire cache.

        Returns:
            Number of entries removed
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM papers")
                count = cursor.rowcount
                conn.commit()

            logger.info(f"Cleared all {count} paper(s) from cache")
            return count

        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats (total, by_source, expired)
        """
        try:
            now = datetime.now().isoformat()

            with sqlite3.connect(self.db_path) as conn:
                # Total papers
                cursor = conn.execute("SELECT COUNT(*) FROM papers")
                total = cursor.fetchone()[0]

                # By source
                cursor = conn.execute("""
                    SELECT source, COUNT(*) as count
                    FROM papers
                    GROUP BY source
                """)
                by_source = {row[0]: row[1] for row in cursor.fetchall()}

                # Expired
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM papers
                    WHERE expires_at < ?
                """, (now,))
                expired = cursor.fetchone()[0]

                return {
                    'total': total,
                    'active': total - expired,
                    'expired': expired,
                    'by_source': by_source,
                    'cache_dir': str(self.cache_dir),
                    'ttl_days': self.ttl_days
                }

        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}
