"""
Deep Search engine for academic papers.
Provides multi-hop reasoning across papers with local knowledge integration.
"""

import asyncio
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set, Callable

logger = logging.getLogger(__name__)


@dataclass
class PaperResult:
    """Enriched paper result with relevance scoring."""

    paper_id: str
    title: str
    authors: List[str]
    abstract: str
    year: int
    source: str  # pubmed, semantic_scholar, core, arxiv

    # Optional fields
    citation_count: int = 0
    citations: List[str] = field(default_factory=list)  # Papers this paper cites
    cited_by: List[str] = field(default_factory=list)  # Papers that cite this paper
    relevance_score: float = 0.0
    pdf_url: Optional[str] = None
    doi: Optional[str] = None
    key_findings: Optional[str] = None  # AI-extracted summary
    venue: Optional[str] = None  # Journal/conference name

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'paper_id': self.paper_id,
            'title': self.title,
            'authors': self.authors,
            'abstract': self.abstract,
            'year': self.year,
            'source': self.source,
            'citation_count': self.citation_count,
            'citations': self.citations,
            'cited_by': self.cited_by,
            'relevance_score': self.relevance_score,
            'pdf_url': self.pdf_url,
            'doi': self.doi,
            'key_findings': self.key_findings,
            'venue': self.venue,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PaperResult':
        """Create from dictionary."""
        return cls(
            paper_id=data['paper_id'],
            title=data['title'],
            authors=data.get('authors', []),
            abstract=data.get('abstract', ''),
            year=data.get('year', 0),
            source=data['source'],
            citation_count=data.get('citation_count', 0),
            citations=data.get('citations', []),
            cited_by=data.get('cited_by', []),
            relevance_score=data.get('relevance_score', 0.0),
            pdf_url=data.get('pdf_url'),
            doi=data.get('doi'),
            key_findings=data.get('key_findings'),
            venue=data.get('venue'),
            metadata=data.get('metadata', {})
        )


@dataclass
class SearchHop:
    """Represents one hop in the multi-hop search."""

    hop_number: int
    papers: List[PaperResult]
    selection_reason: str
    followed_from: Optional[str] = None  # Paper ID that led to this hop
    timestamp: datetime = field(default_factory=datetime.now)

    def __len__(self) -> int:
        """Number of papers in this hop."""
        return len(self.papers)


@dataclass
class SearchPath:
    """Tracks the reasoning path through papers."""

    hops: List[SearchHop] = field(default_factory=list)
    local_papers: List[PaperResult] = field(default_factory=list)  # From ChromaDB
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    def add_hop(self, hop: SearchHop):
        """Add a hop to the search path."""
        self.hops.append(hop)

    def add_local_papers(self, papers: List[PaperResult]):
        """Add papers from local knowledge base."""
        self.local_papers.extend(papers)

    def complete(self):
        """Mark search as completed."""
        self.end_time = datetime.now()

    @property
    def total_papers(self) -> int:
        """Total number of papers across all hops."""
        return len(self.local_papers) + sum(len(hop) for hop in self.hops)

    @property
    def elapsed_time(self) -> float:
        """Elapsed time in seconds."""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()


@dataclass
class DeepSearchResult:
    """Result of deep search with comprehensive information."""

    question: str
    answer: str
    papers: List[PaperResult]
    search_path: SearchPath
    local_papers: List[PaperResult]  # Papers from local knowledge base
    paper_summaries: List[Dict[str, Any]]  # AI-extracted key findings

    # Stats
    total_papers: int = 0
    total_hops: int = 0
    processing_time: float = 0.0

    # Citation graph (optional)
    citation_graph: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'question': self.question,
            'answer': self.answer,
            'papers': [p.to_dict() for p in self.papers],
            'local_papers': [p.to_dict() for p in self.local_papers],
            'paper_summaries': self.paper_summaries,
            'total_papers': self.total_papers,
            'total_hops': self.total_hops,
            'processing_time': self.processing_time,
            'search_path': {
                'total_papers': self.search_path.total_papers,
                'elapsed_time': self.search_path.elapsed_time,
                'hops': len(self.search_path.hops)
            }
        }


class DeepSearchEngine:
    """
    Deep search engine for scientific papers.
    Implements multi-hop reasoning with local knowledge integration.
    """

    def __init__(
        self,
        academic_api_manager,
        vector_store_manager,
        paper_cache,
        llm_client,
        settings
    ):
        """
        Initialize deep search engine.

        Args:
            academic_api_manager: Manager for academic APIs (PubMed, Semantic Scholar, CORE)
            vector_store_manager: ChromaDB manager for local knowledge
            paper_cache: Paper caching system
            llm_client: LLM client for concept extraction and synthesis
            settings: Deep search settings
        """
        self.academic_apis = academic_api_manager
        self.vector_store = vector_store_manager
        self.paper_cache = paper_cache
        self.llm = llm_client
        self.settings = settings

        self.visited_papers: Set[str] = set()
        logger.info("DeepSearchEngine initialized")

    async def deep_search(
        self,
        question: str,
        current_pdf_context: str = "",
        max_hops: Optional[int] = None,
        max_papers_per_hop: Optional[int] = None,
        progress_callback: Optional[Callable] = None
    ) -> DeepSearchResult:
        """
        Perform deep search with multi-hop reasoning.

        Args:
            question: User's research question
            current_pdf_context: Context from current PDF (if any)
            max_hops: Maximum hops (overrides settings)
            max_papers_per_hop: Papers per hop (overrides settings)
            progress_callback: Optional callback for progress updates

        Returns:
            DeepSearchResult with comprehensive answer and search trail
        """
        max_hops = max_hops or self.settings.max_hops
        max_papers_per_hop = max_papers_per_hop or self.settings.max_papers_per_hop

        logger.info(f"Starting deep search: {question[:100]}")
        search_path = SearchPath()
        all_papers = []
        self.visited_papers.clear()

        try:
            # Step 0: Local Knowledge Search
            if progress_callback:
                await progress_callback({
                    'stage': 'local_search',
                    'detail': 'Searching local knowledge base...',
                    'progress': 5
                })

            local_papers = await self._search_local_knowledge(question, current_pdf_context)
            search_path.add_local_papers(local_papers)
            logger.info(f"Found {len(local_papers)} papers in local knowledge base")

            # Extract key concepts
            key_concepts = await self._extract_key_concepts(
                question, current_pdf_context, local_papers
            )
            logger.info(f"Extracted key concepts: {key_concepts}")

            # Hop 0: Initial Broad Search
            if progress_callback:
                await progress_callback({
                    'hop': 0,
                    'hop_stage': 'searching',
                    'detail': 'Querying academic databases...',
                    'progress': 15
                })

            hop0_papers = await self._hop_0_initial_search(
                question, key_concepts, max_papers_per_hop
            )

            search_path.add_hop(SearchHop(
                hop_number=0,
                papers=hop0_papers,
                selection_reason="Initial broad search across databases"
            ))
            all_papers.extend(hop0_papers)

            if progress_callback:
                await progress_callback({
                    'hop': 0,
                    'hop_stage': 'completed',
                    'papers_found': len(hop0_papers),
                    'progress': 33
                })

            # Hop 1: Backward Citations (if enabled)
            if max_hops >= 2 and self.settings.follow_citations and hop0_papers:
                if progress_callback:
                    await progress_callback({
                        'hop': 1,
                        'hop_stage': 'searching',
                        'detail': 'Following citations (foundational papers)...',
                        'progress': 40
                    })

                hop1_papers = await self._hop_1_backward_citations(
                    question, hop0_papers, max_papers_per_hop
                )

                search_path.add_hop(SearchHop(
                    hop_number=1,
                    papers=hop1_papers,
                    selection_reason="Foundational papers (backward citations)",
                    followed_from=",".join([p.paper_id for p in hop0_papers[:3]])
                ))
                all_papers.extend(hop1_papers)

                if progress_callback:
                    await progress_callback({
                        'hop': 1,
                        'hop_stage': 'completed',
                        'papers_found': len(hop1_papers),
                        'progress': 66
                    })

            # Hop 2: Forward Citations (if enabled)
            if max_hops >= 3 and self.settings.follow_cited_by and hop0_papers:
                if progress_callback:
                    await progress_callback({
                        'hop': 2,
                        'hop_stage': 'searching',
                        'detail': 'Following cited-by (recent developments)...',
                        'progress': 70
                    })

                hop2_papers = await self._hop_2_forward_citations(
                    question, hop0_papers, max_papers_per_hop
                )

                search_path.add_hop(SearchHop(
                    hop_number=2,
                    papers=hop2_papers,
                    selection_reason="Recent developments (forward citations)",
                    followed_from=",".join([p.paper_id for p in hop0_papers[:3]])
                ))
                all_papers.extend(hop2_papers)

                if progress_callback:
                    await progress_callback({
                        'hop': 2,
                        'hop_stage': 'completed',
                        'papers_found': len(hop2_papers),
                        'progress': 85
                    })

            # Synthesis
            if progress_callback:
                await progress_callback({
                    'hop_stage': 'synthesizing',
                    'detail': 'Synthesizing comprehensive answer...',
                    'progress': 90
                })

            result = await self._synthesize_results(
                question, all_papers, local_papers, search_path
            )

            if progress_callback:
                await progress_callback({
                    'hop_stage': 'completed',
                    'detail': 'Deep search completed',
                    'progress': 100
                })

            search_path.complete()
            logger.info(f"Deep search completed: {len(all_papers)} papers, "
                       f"{search_path.elapsed_time:.1f}s")

            return result

        except Exception as e:
            logger.error(f"Deep search failed: {e}", exc_info=True)
            raise

    async def _search_local_knowledge(
        self,
        question: str,
        current_pdf_context: str
    ) -> List[PaperResult]:
        """
        Search local ChromaDB for relevant past papers.

        Args:
            question: User's question
            current_pdf_context: Context from current PDF

        Returns:
            List of relevant papers from local knowledge base
        """
        try:
            # Search ChromaDB with question + context
            search_query = f"{question}\n\n{current_pdf_context[:500]}"

            results = await self.vector_store.search_similar(
                search_query,
                n_results=5,
                filter_metadata=None
            )

            # Convert to PaperResult format
            local_papers = []
            for result in results:
                # Extract paper info from chunk metadata
                metadata = result.get('metadata', {})
                document_id = metadata.get('document_id', 'unknown')

                # Create PaperResult from local document
                paper = PaperResult(
                    paper_id=f"local_{document_id}",
                    title=metadata.get('title', 'Local Document'),
                    authors=metadata.get('authors', ['Unknown']),
                    abstract=result.get('text', '')[:500],
                    year=metadata.get('year', 0),
                    source='local_chromadb',
                    relevance_score=result.get('similarity', 0.0)
                )
                local_papers.append(paper)

            return local_papers

        except Exception as e:
            logger.warning(f"Local knowledge search failed: {e}")
            return []

    async def _extract_key_concepts(
        self,
        question: str,
        current_pdf_context: str,
        local_papers: List[PaperResult]
    ) -> List[str]:
        """Extract key concepts using LLM."""
        # Implementation placeholder - would use LLM to extract concepts
        # For now, return question words as concepts
        concepts = question.lower().split()
        # Filter stop words and keep meaningful terms
        stop_words = {'what', 'how', 'why', 'when', 'where', 'is', 'are', 'the', 'a', 'an'}
        concepts = [c for c in concepts if c not in stop_words and len(c) > 3]
        return concepts[:5]

    async def _hop_0_initial_search(
        self,
        question: str,
        key_concepts: List[str],
        max_papers: int
    ) -> List[PaperResult]:
        """
        Hop 0: Initial broad search across all academic APIs.
        """
        try:
            # Query all academic APIs in parallel
            query = f"{question} {' '.join(key_concepts)}"
            papers = await self.academic_apis.search_all(query, max_results=max_papers * 2)

            # Score and rank papers
            scored_papers = await self._score_papers(papers, question, key_concepts)

            # Select diverse top papers
            selected = await self._select_diverse_papers(scored_papers, max_papers)

            # Mark as visited
            for paper in selected:
                self.visited_papers.add(paper.paper_id)

            return selected

        except Exception as e:
            logger.error(f"Hop 0 search failed: {e}")
            return []

    async def _hop_1_backward_citations(
        self,
        question: str,
        seed_papers: List[PaperResult],
        max_papers: int
    ) -> List[PaperResult]:
        """
        Hop 1: Follow backward citations (references).
        """
        try:
            all_citations = []

            # Gather citations from seed papers
            for paper in seed_papers:
                citations = await self.academic_apis.get_citations(
                    paper.paper_id, paper.source
                )
                all_citations.extend(citations)

            # Filter unvisited
            unvisited = [
                c for c in all_citations
                if c.paper_id not in self.visited_papers
            ]

            if not unvisited:
                logger.info("No unvisited citations found")
                return []

            # LLM-based selection of most relevant foundational papers
            selected = await self._llm_select_papers(
                question, unvisited,
                context="Select foundational papers that provide background knowledge",
                max_select=max_papers
            )

            # Mark as visited
            for paper in selected:
                self.visited_papers.add(paper.paper_id)

            return selected

        except Exception as e:
            logger.error(f"Hop 1 (backward citations) failed: {e}")
            return []

    async def _hop_2_forward_citations(
        self,
        question: str,
        seed_papers: List[PaperResult],
        max_papers: int
    ) -> List[PaperResult]:
        """
        Hop 2: Follow forward citations (cited-by).
        """
        try:
            all_cited_by = []

            # Gather papers that cite seed papers
            for paper in seed_papers:
                cited_by = await self.academic_apis.get_cited_by(
                    paper.paper_id, paper.source
                )
                all_cited_by.extend(cited_by)

            # Filter to recent papers if enabled
            if self.settings.recent_papers_only:
                threshold_year = datetime.now().year - self.settings.recent_years_threshold
                all_cited_by = [p for p in all_cited_by if p.year >= threshold_year]

            # Filter unvisited
            unvisited = [
                p for p in all_cited_by
                if p.paper_id not in self.visited_papers
            ]

            if not unvisited:
                logger.info("No unvisited cited-by papers found")
                return []

            # LLM-based selection
            selected = await self._llm_select_papers(
                question, unvisited,
                context="Select recent papers that show new developments or applications",
                max_select=max_papers
            )

            # Mark as visited
            for paper in selected:
                self.visited_papers.add(paper.paper_id)

            return selected

        except Exception as e:
            logger.error(f"Hop 2 (forward citations) failed: {e}")
            return []

    async def _score_papers(
        self,
        papers: List[PaperResult],
        question: str,
        key_concepts: List[str]
    ) -> List[PaperResult]:
        """
        Score papers by relevance.
        """
        # Simplified scoring - in real implementation would use embeddings
        for paper in papers:
            # Base score from citation count (normalized)
            citation_score = min(paper.citation_count / 100, 1.0) if paper.citation_count else 0

            # Keyword matching
            text = (paper.title + " " + paper.abstract).lower()
            keyword_matches = sum(1 for concept in key_concepts if concept in text)
            keyword_score = min(keyword_matches / len(key_concepts), 1.0) if key_concepts else 0

            # Recency
            current_year = datetime.now().year
            recency_score = min((paper.year - 2000) / (current_year - 2000), 1.0) if paper.year > 2000 else 0

            # Combined score
            paper.relevance_score = (
                0.4 * keyword_score +
                0.3 * citation_score +
                0.3 * recency_score
            )

        # Sort by score
        papers.sort(key=lambda p: p.relevance_score, reverse=True)
        return papers

    async def _select_diverse_papers(
        self,
        papers: List[PaperResult],
        max_papers: int
    ) -> List[PaperResult]:
        """
        Select diverse papers from different sources.
        """
        selected = []
        sources_used = set()

        # First pass: one from each source
        for paper in papers:
            if len(selected) >= max_papers:
                break
            if paper.source not in sources_used:
                selected.append(paper)
                sources_used.add(paper.source)

        # Second pass: fill remaining slots by relevance
        for paper in papers:
            if len(selected) >= max_papers:
                break
            if paper not in selected:
                selected.append(paper)

        return selected[:max_papers]

    async def _llm_select_papers(
        self,
        question: str,
        papers: List[PaperResult],
        context: str,
        max_select: int
    ) -> List[PaperResult]:
        """
        Use LLM to select most relevant papers.
        """
        # Simplified implementation - would use LLM in real version
        # For now, just score and return top papers
        scored = await self._score_papers(papers, question, [])
        return scored[:max_select]

    async def _synthesize_results(
        self,
        question: str,
        all_papers: List[PaperResult],
        local_papers: List[PaperResult],
        search_path: SearchPath
    ) -> DeepSearchResult:
        """
        Synthesize comprehensive answer from all papers.
        """
        # Extract key findings from each paper (simplified)
        paper_summaries = []
        for paper in all_papers:
            summary = {
                'paper': paper,
                'summary': paper.abstract[:200] if paper.abstract else "No summary available"
            }
            paper_summaries.append(summary)

        # Generate comprehensive answer (simplified)
        answer = f"Based on analysis of {len(all_papers)} papers across {len(search_path.hops)} hops:\n\n"
        answer += f"[This would be a comprehensive synthesis combining insights from all papers]\n\n"
        answer += f"Local knowledge: {len(local_papers)} relevant papers from your library\n"

        if search_path.hops:
            answer += f"Initial papers: {len(search_path.hops[0])} papers\n"
        if len(search_path.hops) > 1:
            answer += f"Foundational work: {len(search_path.hops[1])} papers\n"
        if len(search_path.hops) > 2:
            answer += f"Recent developments: {len(search_path.hops[2])} papers\n"

        return DeepSearchResult(
            question=question,
            answer=answer,
            papers=all_papers,
            local_papers=local_papers,
            search_path=search_path,
            paper_summaries=paper_summaries,
            total_papers=len(all_papers) + len(local_papers),
            total_hops=len(search_path.hops),
            processing_time=search_path.elapsed_time
        )
