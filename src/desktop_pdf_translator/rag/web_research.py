"""
Web research engine for enhanced RAG responses.
Integrates Google Search, Scholar, and other sources to supplement PDF knowledge.
"""

import logging
from typing import List, Dict, Any, Tuple, Optional
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
import hashlib

from duckduckgo_search import DDGS

from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)


class WebSource:
    """Represents a web source with content and metadata."""
    
    def __init__(self, url: str, title: str = "", content: str = "", 
                 source_type: str = "web", reliability_score: float = 0.5):
        self.url = url
        self.title = title
        self.content = content
        self.source_type = source_type  # web, scholar, wikipedia, arxiv
        self.reliability_score = reliability_score
        self.scraped_at = datetime.now()
        self.snippet = content[:200] + "..." if len(content) > 200 else content
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            'url': self.url,
            'title': self.title,
            'content': self.content,
            'snippet': self.snippet,
            'source_type': self.source_type,
            'reliability_score': self.reliability_score,
            'scraped_at': self.scraped_at.isoformat()
        }


class ContentScraper:
    """Scrapes and extracts content from web pages."""
    
    def __init__(self):
        self.session = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    async def scrape_url(self, url: str, timeout: int = 10) -> Optional[WebSource]:
        """
        Scrape content from a URL.
        
        Args:
            url: URL to scrape
            timeout: Request timeout in seconds
            
        Returns:
            WebSource object with scraped content
        """
        try:
            # Use requests for now (can be upgraded to aiohttp later)
            response = requests.get(url, headers=self.headers, timeout=timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract title
            title = ""
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
            
            # Extract main content
            content = self._extract_main_content(soup)
            
            # Determine source type and reliability
            source_type, reliability = self._analyze_source(url, soup)
            
            return WebSource(
                url=url,
                title=title,
                content=content,
                source_type=source_type,
                reliability_score=reliability
            )
            
        except Exception as e:
            logger.warning(f"Failed to scrape {url}: {e}")
            return None
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract main content from HTML."""
        
        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            element.decompose()
        
        # Try to find main content areas
        content_selectors = [
            'article',
            'main',
            '[role="main"]',
            '.content',
            '.main-content',
            '#content',
            '#main'
        ]
        
        main_content = None
        for selector in content_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                break
        
        # Fallback to body if no main content found
        if not main_content:
            main_content = soup.find('body')
        
        if main_content:
            # Extract text and clean up
            text = main_content.get_text(separator=' ', strip=True)
            # Clean up extra whitespace
            text = re.sub(r'\s+', ' ', text)
            return text[:5000]  # Limit content length
        
        return ""
    
    def _analyze_source(self, url: str, soup: BeautifulSoup) -> Tuple[str, float]:
        """Analyze source type and reliability."""
        
        domain = urlparse(url).netloc.lower()
        
        # Academic sources (high reliability)
        if any(academic in domain for academic in ['scholar.google', 'arxiv.org', 'pubmed', 'ieee.org', 'acm.org']):
            return 'academic', 0.9
        
        # Wikipedia (medium-high reliability)
        if 'wikipedia.org' in domain:
            return 'wikipedia', 0.8
        
        # Government sources (high reliability)
        if any(gov in domain for gov in ['.gov', '.edu']):
            return 'official', 0.85
        
        # News sources (medium reliability)
        news_domains = ['bbc.com', 'reuters.com', 'ap.org', 'cnn.com', 'nytimes.com']
        if any(news in domain for news in news_domains):
            return 'news', 0.7
        
        # Check for academic indicators in content
        academic_indicators = ['doi:', 'abstract:', 'citation:', 'references:', 'bibliography:']
        content_text = soup.get_text().lower()
        if any(indicator in content_text for indicator in academic_indicators):
            return 'academic', 0.8
        
        # Default web source
        return 'web', 0.5


class SearchEngine:
    """Handles different types of web searches."""
    
    def __init__(self):
        self.scraper = ContentScraper()
        self.cache = {}  # Simple in-memory cache
        self.cache_ttl = timedelta(hours=1)
    
    async def web_search(self, query: str, num_results: int = 5) -> List[str]:
        """
        Perform web search using DuckDuckGo and return URLs.

        Args:
            query: Search query
            num_results: Number of results to return

        Returns:
            List of URLs
        """
        try:
            # Check cache first
            cache_key = f"web_{hashlib.md5(query.encode()).hexdigest()}"
            if cache_key in self.cache:
                cached_result, timestamp = self.cache[cache_key]
                if datetime.now() - timestamp < self.cache_ttl:
                    return cached_result

            # Rate limiting: Add delay to avoid being blocked
            import asyncio
            await asyncio.sleep(0.5)  # 500ms delay between searches

            # Perform search using DuckDuckGo
            urls = []
            try:
                with DDGS() as ddgs:
                    results = ddgs.text(query, max_results=num_results)
                    for result in results:
                        if 'href' in result:
                            urls.append(result['href'])
                        elif 'link' in result:
                            urls.append(result['link'])

                logger.info(f"Web search for '{query}' returned {len(urls)} URLs")

            except Exception as search_error:
                logger.error(f"DuckDuckGo search failed: {search_error}")
                return []

            # Cache results
            self.cache[cache_key] = (urls, datetime.now())
            return urls

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return []
    
    async def scholar_search(self, query: str, num_results: int = 3) -> List[str]:
        """
        Search for academic papers.

        Args:
            query: Academic search query
            num_results: Number of results

        Returns:
            List of URLs
        """
        try:
            # Add academic keywords to improve results
            academic_query = f"{query} research paper academic"
            return await self.web_search(academic_query, num_results)

        except Exception as e:
            logger.error(f"Scholar search failed: {e}")
            return []
    
    async def wikipedia_search(self, query: str) -> List[str]:
        """
        Search Wikipedia for relevant articles.

        Args:
            query: Search query

        Returns:
            List of Wikipedia URLs
        """
        try:
            # Search for Wikipedia articles
            wiki_query = f"{query} wikipedia"
            results = await self.web_search(wiki_query, 3)
            # Filter to only Wikipedia URLs
            return [url for url in results if 'wikipedia.org' in url][:2]

        except Exception as e:
            logger.error(f"Wikipedia search failed: {e}")
            return []
    
    async def arxiv_search(self, query: str) -> List[str]:
        """
        Search arXiv for scientific papers.

        Args:
            query: Scientific search query

        Returns:
            List of arXiv URLs
        """
        try:
            # Search for arXiv papers
            arxiv_query = f"{query} arxiv"
            results = await self.web_search(arxiv_query, 3)
            # Filter to only arXiv URLs
            return [url for url in results if 'arxiv.org' in url][:2]

        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return []


class WebResearchEngine:
    """
    Main web research engine that coordinates searches and content extraction.
    """
    
    def __init__(self):
        self.search_engine = SearchEngine()
        self.scraper = ContentScraper()
        
    async def research_topic(self, query: str, pdf_context: str = "") -> List[WebSource]:
        """
        Research a topic using multiple web sources.
        
        Args:
            query: Research query
            pdf_context: Context from PDF to help generate better search queries
            
        Returns:
            List of WebSource objects with researched content
        """
        logger.info(f"Starting web research for: {query}")
        
        # Generate enhanced search queries
        search_queries = self._generate_search_queries(query, pdf_context)
        
        all_sources = []
        
        # Perform different types of searches (optimized for fewer API calls)
        for search_query in search_queries:
            # Web search - reduced from 3 to 2 results
            web_urls = await self.search_engine.web_search(search_query, 2)

            # Academic search - reduced from 2 to 1 result
            scholar_urls = await self.search_engine.scholar_search(search_query, 1)

            # Wikipedia search - keep at 2 (already filtered to Wikipedia URLs only)
            wiki_urls = await self.search_engine.wikipedia_search(search_query)

            # Combine all URLs
            all_urls = web_urls + scholar_urls + wiki_urls
            
            # Remove duplicates
            unique_urls = list(dict.fromkeys(all_urls))
            
            # Scrape content from URLs
            for url in unique_urls[:8]:  # Limit to avoid overwhelming
                source = await self.scraper.scrape_url(url)
                if source and len(source.content) > 100:  # Only keep substantial content
                    all_sources.append(source)
        
        # Remove duplicate sources and sort by reliability
        unique_sources = self._deduplicate_sources(all_sources)
        unique_sources.sort(key=lambda x: x.reliability_score, reverse=True)
        
        logger.info(f"Web research completed: {len(unique_sources)} sources found")
        return unique_sources[:10]  # Return top 10 sources
    
    def _generate_search_queries(self, query: str, pdf_context: str) -> List[str]:
        """Generate optimized search queries (reduced to avoid rate limiting)."""

        # Strategy: Use only 1-2 well-crafted queries instead of many variations
        # This reduces API calls from 15+ to 3-6 total

        queries = [query]  # Always include original query

        # Optionally add ONE enhanced query with context
        if pdf_context:
            # Extract single most relevant term from PDF context
            context_words = re.findall(r'\b[A-Za-z]{5,}\b', pdf_context.lower())
            if context_words:
                # Get first unique term
                key_term = list(set(context_words))[0]
                enhanced_query = f"{query} {key_term}"
                queries.append(enhanced_query)

        # Return maximum 2 queries to avoid rate limiting
        # This means 2 queries * 3 search types = 6 API calls (vs 15+ before)
        return queries[:2]
    
    def _deduplicate_sources(self, sources: List[WebSource]) -> List[WebSource]:
        """Remove duplicate sources based on URL and content similarity."""
        
        unique_sources = []
        seen_urls = set()
        seen_content_hashes = set()
        
        for source in sources:
            # Skip if URL already seen
            if source.url in seen_urls:
                continue
            
            # Skip if content is very similar (simple hash check)
            content_hash = hashlib.md5(source.content[:500].encode()).hexdigest()
            if content_hash in seen_content_hashes:
                continue
            
            seen_urls.add(source.url)
            seen_content_hashes.add(content_hash)
            unique_sources.append(source)
        
        return unique_sources
    
