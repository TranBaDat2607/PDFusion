"""
Academic API integrations for deep search.
Provides access to PubMed, Semantic Scholar, CORE, and enhanced arXiv.
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
from datetime import datetime

import aiohttp
import xmltodict

from .deep_search import PaperResult

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """Async rate limiter for API calls."""

    def __init__(self, requests_per_second: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second
        """
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
        self.last_request = 0.0
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        async with self.lock:
            now = time.time()
            time_since_last = now - self.last_request

            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                await asyncio.sleep(wait_time)

            self.last_request = time.time()

    async def __aexit__(self, *args):
        pass


class PubMedAPI:
    """PubMed/NCBI E-utilities API integration."""

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize PubMed API.

        Args:
            api_key: Optional NCBI API key (improves rate limit from 3/sec to 10/sec)
        """
        self.api_key = api_key
        # Rate limit: 3 req/sec without key, 10 req/sec with key
        rate = 10 if api_key else 3
        self.rate_limiter = AsyncRateLimiter(requests_per_second=rate)

        logger.info(f"PubMed API initialized (rate: {rate} req/sec)")

    async def search(self, query: str, max_results: int = 10) -> List[PaperResult]:
        """
        Search PubMed using esearch.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            List of PaperResult objects
        """
        try:
            async with self.rate_limiter:
                # Step 1: Search for PMIDs
                params = {
                    'db': 'pubmed',
                    'term': query,
                    'retmax': max_results,
                    'retmode': 'json',
                    'sort': 'relevance'
                }
                if self.api_key:
                    params['api_key'] = self.api_key

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}esearch.fcgi"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            logger.error(f"PubMed search failed: {resp.status}")
                            return []

                        data = await resp.json()
                        pmids = data.get('esearchresult', {}).get('idlist', [])

                if not pmids:
                    logger.info(f"No PubMed results for: {query}")
                    return []

                # Step 2: Fetch details
                papers = await self.fetch_details(pmids)
                logger.info(f"Found {len(papers)} PubMed papers")
                return papers

        except Exception as e:
            logger.error(f"PubMed search error: {e}")
            return []

    async def fetch_details(self, pmids: List[str]) -> List[PaperResult]:
        """
        Fetch paper details using efetch.

        Args:
            pmids: List of PubMed IDs

        Returns:
            List of PaperResult objects
        """
        if not pmids:
            return []

        try:
            async with self.rate_limiter:
                params = {
                    'db': 'pubmed',
                    'id': ','.join(pmids),
                    'retmode': 'xml',
                }
                if self.api_key:
                    params['api_key'] = self.api_key

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}efetch.fcgi"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            logger.error(f"PubMed fetch failed: {resp.status}")
                            return []

                        xml_text = await resp.text()

                # Parse XML
                data = xmltodict.parse(xml_text)
                articles = data.get('PubmedArticleSet', {}).get('PubmedArticle', [])

                # Handle single article (not a list)
                if not isinstance(articles, list):
                    articles = [articles]

                papers = []
                for article in articles:
                    try:
                        paper = self._parse_article(article)
                        if paper:
                            papers.append(paper)
                    except Exception as e:
                        logger.warning(f"Failed to parse article: {e}")

                return papers

        except Exception as e:
            logger.error(f"PubMed fetch error: {e}")
            return []

    def _parse_article(self, article: Dict[str, Any]) -> Optional[PaperResult]:
        """Parse PubMed article XML to PaperResult."""
        try:
            medline = article.get('MedlineCitation', {})
            pmid = medline.get('PMID', {}).get('#text', 'unknown')
            article_data = medline.get('Article', {})

            # Title
            title = article_data.get('ArticleTitle', 'Unknown Title')
            if isinstance(title, dict):
                title = title.get('#text', 'Unknown Title')

            # Authors
            author_list = article_data.get('AuthorList', {}).get('Author', [])
            if not isinstance(author_list, list):
                author_list = [author_list]

            authors = []
            for author in author_list:
                if isinstance(author, dict):
                    last_name = author.get('LastName', '')
                    fore_name = author.get('ForeName', '')
                    if last_name:
                        authors.append(f"{fore_name} {last_name}".strip())

            # Abstract
            abstract_data = article_data.get('Abstract', {})
            abstract_text = abstract_data.get('AbstractText', '')
            if isinstance(abstract_text, list):
                abstract = ' '.join([
                    t.get('#text', t) if isinstance(t, dict) else str(t)
                    for t in abstract_text
                ])
            elif isinstance(abstract_text, dict):
                abstract = abstract_text.get('#text', '')
            else:
                abstract = str(abstract_text)

            # Year
            pub_date = article_data.get('Journal', {}).get('JournalIssue', {}).get('PubDate', {})
            year = pub_date.get('Year', 0)
            if isinstance(year, str):
                try:
                    year = int(year)
                except:
                    year = 0

            # DOI
            article_ids = article.get('PubmedData', {}).get('ArticleIdList', {}).get('ArticleId', [])
            if not isinstance(article_ids, list):
                article_ids = [article_ids]

            doi = None
            for aid in article_ids:
                if isinstance(aid, dict) and aid.get('@IdType') == 'doi':
                    doi = aid.get('#text')
                    break

            return PaperResult(
                paper_id=f"pubmed_{pmid}",
                title=title,
                authors=authors,
                abstract=abstract,
                year=year,
                source='pubmed',
                doi=doi
            )

        except Exception as e:
            logger.error(f"Error parsing PubMed article: {e}")
            return None

    async def get_citations(
        self,
        paper_id: str,
        max_citations: int = 20
    ) -> List[PaperResult]:
        """
        Get papers cited by this paper using elink.

        Args:
            paper_id: PubMed paper ID (with or without prefix)
            max_citations: Maximum citations to return

        Returns:
            List of cited papers
        """
        # Extract PMID
        pmid = paper_id.replace('pubmed_', '')

        try:
            async with self.rate_limiter:
                params = {
                    'dbfrom': 'pubmed',
                    'db': 'pubmed',
                    'id': pmid,
                    'linkname': 'pubmed_pubmed_refs',
                    'retmode': 'json'
                }
                if self.api_key:
                    params['api_key'] = self.api_key

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}elink.fcgi"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            return []

                        data = await resp.json()
                        link_sets = data.get('linksets', [])

                        if not link_sets:
                            return []

                        linked_ids = link_sets[0].get('linksetdbs', [])
                        if not linked_ids:
                            return []

                        pmids = linked_ids[0].get('links', [])[:max_citations]

                        if pmids:
                            return await self.fetch_details(pmids)

                        return []

        except Exception as e:
            logger.error(f"PubMed citations error: {e}")
            return []


class SemanticScholarAPI:
    """Semantic Scholar API integration."""

    BASE_URL = "https://api.semanticscholar.org/graph/v1/"

    def __init__(self):
        """Initialize Semantic Scholar API (no key required)."""
        # Rate limit: 100 requests per 5 minutes = ~0.33 req/sec
        self.rate_limiter = AsyncRateLimiter(requests_per_second=0.3)

        logger.info("Semantic Scholar API initialized")

    async def search(self, query: str, max_results: int = 10) -> List[PaperResult]:
        """
        Search papers.

        Args:
            query: Search query
            max_results: Maximum results

        Returns:
            List of PaperResult objects
        """
        try:
            async with self.rate_limiter:
                params = {
                    'query': query,
                    'limit': max_results,
                    'fields': 'paperId,title,authors,year,abstract,citationCount,url,openAccessPdf,venue'
                }

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}paper/search"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            logger.error(f"Semantic Scholar search failed: {resp.status}")
                            return []

                        data = await resp.json()
                        items = data.get('data', [])

                        papers = []
                        for item in items:
                            paper = self._parse_paper(item)
                            if paper:
                                papers.append(paper)

                        logger.info(f"Found {len(papers)} Semantic Scholar papers")
                        return papers

        except Exception as e:
            logger.error(f"Semantic Scholar search error: {e}")
            return []

    def _parse_paper(self, item: Dict[str, Any]) -> Optional[PaperResult]:
        """Parse Semantic Scholar paper to PaperResult."""
        try:
            paper_id = item.get('paperId', 'unknown')
            title = item.get('title', 'Unknown Title')

            authors = []
            for author in item.get('authors', []):
                name = author.get('name', '')
                if name:
                    authors.append(name)

            abstract = item.get('abstract', '')
            year = item.get('year') or 0
            citation_count = item.get('citationCount', 0)
            venue = item.get('venue', '')

            # PDF URL
            pdf_info = item.get('openAccessPdf')
            pdf_url = pdf_info.get('url') if pdf_info else None

            return PaperResult(
                paper_id=f"s2_{paper_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                year=year,
                source='semantic_scholar',
                citation_count=citation_count,
                pdf_url=pdf_url,
                venue=venue
            )

        except Exception as e:
            logger.error(f"Error parsing Semantic Scholar paper: {e}")
            return None

    async def get_paper(self, paper_id: str) -> Optional[PaperResult]:
        """Get paper details by ID."""
        # Remove prefix
        s2_id = paper_id.replace('s2_', '')

        try:
            async with self.rate_limiter:
                params = {
                    'fields': 'paperId,title,authors,year,abstract,citationCount,url,openAccessPdf,venue'
                }

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}paper/{s2_id}"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            return None

                        item = await resp.json()
                        return self._parse_paper(item)

        except Exception as e:
            logger.error(f"Semantic Scholar get paper error: {e}")
            return None

    async def get_citations(
        self,
        paper_id: str,
        max_citations: int = 20
    ) -> List[PaperResult]:
        """Get papers cited by this paper (references)."""
        s2_id = paper_id.replace('s2_', '')

        try:
            async with self.rate_limiter:
                params = {
                    'fields': 'paperId,title,authors,year,abstract,citationCount',
                    'limit': max_citations
                }

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}paper/{s2_id}/references"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            return []

                        data = await resp.json()
                        items = data.get('data', [])

                        papers = []
                        for item in items:
                            cited_paper = item.get('citedPaper', {})
                            paper = self._parse_paper(cited_paper)
                            if paper:
                                papers.append(paper)

                        return papers

        except Exception as e:
            logger.error(f"Semantic Scholar citations error: {e}")
            return []

    async def get_cited_by(
        self,
        paper_id: str,
        max_cited_by: int = 20
    ) -> List[PaperResult]:
        """Get papers that cite this paper."""
        s2_id = paper_id.replace('s2_', '')

        try:
            async with self.rate_limiter:
                params = {
                    'fields': 'paperId,title,authors,year,abstract,citationCount',
                    'limit': max_cited_by
                }

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}paper/{s2_id}/citations"
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            return []

                        data = await resp.json()
                        items = data.get('data', [])

                        papers = []
                        for item in items:
                            citing_paper = item.get('citingPaper', {})
                            paper = self._parse_paper(citing_paper)
                            if paper:
                                papers.append(paper)

                        return papers

        except Exception as e:
            logger.error(f"Semantic Scholar cited-by error: {e}")
            return []


class COREAPI:
    """CORE API for open access papers."""

    BASE_URL = "https://api.core.ac.uk/v3/"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize CORE API.

        Args:
            api_key: CORE API key (required)
        """
        self.api_key = api_key
        # Rate limit: 10 requests per minute
        self.rate_limiter = AsyncRateLimiter(requests_per_second=10/60)

        if not api_key:
            logger.warning("CORE API key not provided - CORE search will be disabled")
        else:
            logger.info("CORE API initialized")

    async def search(self, query: str, max_results: int = 10) -> List[PaperResult]:
        """
        Search CORE database.

        Args:
            query: Search query
            max_results: Maximum results

        Returns:
            List of PaperResult objects
        """
        if not self.api_key:
            return []

        try:
            async with self.rate_limiter:
                headers = {'Authorization': f'Bearer {self.api_key}'}
                params = {
                    'q': query,
                    'limit': max_results
                }

                async with aiohttp.ClientSession() as session:
                    url = f"{self.BASE_URL}search/works"
                    async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status != 200:
                            logger.error(f"CORE search failed: {resp.status}")
                            return []

                        data = await resp.json()
                        items = data.get('results', [])

                        papers = []
                        for item in items:
                            paper = self._parse_paper(item)
                            if paper:
                                papers.append(paper)

                        logger.info(f"Found {len(papers)} CORE papers")
                        return papers

        except Exception as e:
            logger.error(f"CORE search error: {e}")
            return []

    def _parse_paper(self, item: Dict[str, Any]) -> Optional[PaperResult]:
        """Parse CORE paper to PaperResult."""
        try:
            core_id = item.get('id', 'unknown')
            title = item.get('title', 'Unknown Title')

            authors = []
            for author in item.get('authors', []):
                if isinstance(author, str):
                    authors.append(author)
                elif isinstance(author, dict):
                    name = author.get('name', '')
                    if name:
                        authors.append(name)

            abstract = item.get('abstract', '')
            year = item.get('yearPublished') or 0
            doi = item.get('doi')
            pdf_url = item.get('downloadUrl')

            return PaperResult(
                paper_id=f"core_{core_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                year=year,
                source='core',
                doi=doi,
                pdf_url=pdf_url
            )

        except Exception as e:
            logger.error(f"Error parsing CORE paper: {e}")
            return None


class AcademicAPIManager:
    """
    Manages all academic database APIs with unified interface.
    """

    def __init__(
        self,
        pubmed_api_key: Optional[str] = None,
        core_api_key: Optional[str] = None
    ):
        """
        Initialize API manager.

        Args:
            pubmed_api_key: Optional PubMed API key
            core_api_key: Optional CORE API key
        """
        self.pubmed = PubMedAPI(api_key=pubmed_api_key)
        self.semantic_scholar = SemanticScholarAPI()
        self.core = COREAPI(api_key=core_api_key)

        logger.info("Academic API Manager initialized")

    async def search_all(
        self,
        query: str,
        max_results_per_source: int = 10
    ) -> List[PaperResult]:
        """
        Search all APIs in parallel.

        Args:
            query: Search query
            max_results_per_source: Max results per API

        Returns:
            Combined list of papers from all sources
        """
        # Search all APIs in parallel
        results = await asyncio.gather(
            self.pubmed.search(query, max_results_per_source),
            self.semantic_scholar.search(query, max_results_per_source),
            self.core.search(query, max_results_per_source),
            return_exceptions=True
        )

        # Combine results
        all_papers = []
        for result in results:
            if isinstance(result, list):
                all_papers.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"API search error: {result}")

        logger.info(f"Found {len(all_papers)} papers across all sources")
        return all_papers

    async def get_paper_details(
        self,
        paper_id: str,
        source: str
    ) -> Optional[PaperResult]:
        """
        Get paper details from specific source.

        Args:
            paper_id: Paper ID
            source: Source database name

        Returns:
            PaperResult or None
        """
        try:
            if source == 'pubmed':
                papers = await self.pubmed.fetch_details([paper_id.replace('pubmed_', '')])
                return papers[0] if papers else None
            elif source == 'semantic_scholar':
                return await self.semantic_scholar.get_paper(paper_id)
            elif source == 'core':
                # CORE doesn't have a direct get_paper method in this implementation
                return None
            else:
                logger.warning(f"Unknown source: {source}")
                return None

        except Exception as e:
            logger.error(f"Error getting paper details: {e}")
            return None

    async def get_citations(
        self,
        paper_id: str,
        source: str,
        max_citations: int = 20
    ) -> List[PaperResult]:
        """
        Get papers cited by this paper.

        Args:
            paper_id: Paper ID
            source: Source database
            max_citations: Maximum citations to return

        Returns:
            List of cited papers
        """
        try:
            if source == 'pubmed':
                return await self.pubmed.get_citations(paper_id, max_citations)
            elif source == 'semantic_scholar':
                return await self.semantic_scholar.get_citations(paper_id, max_citations)
            elif source == 'core':
                # CORE API doesn't support citation retrieval in this implementation
                return []
            else:
                return []

        except Exception as e:
            logger.error(f"Error getting citations: {e}")
            return []

    async def get_cited_by(
        self,
        paper_id: str,
        source: str,
        max_cited_by: int = 20
    ) -> List[PaperResult]:
        """
        Get papers that cite this paper.

        Args:
            paper_id: Paper ID
            source: Source database
            max_cited_by: Maximum papers to return

        Returns:
            List of citing papers
        """
        try:
            if source == 'pubmed':
                # PubMed doesn't directly support cited-by in this implementation
                return []
            elif source == 'semantic_scholar':
                return await self.semantic_scholar.get_cited_by(paper_id, max_cited_by)
            elif source == 'core':
                return []
            else:
                return []

        except Exception as e:
            logger.error(f"Error getting cited-by: {e}")
            return []
