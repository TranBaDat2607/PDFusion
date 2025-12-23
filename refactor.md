# PDFusion Agent-Based Refactoring Plan

**Document Version:** 1.0
**Created:** 2025-12-22
**Target Architecture:** Agent with 3 Tools (RAG, WebSearch, AcademicSearch)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Goals](#architecture-goals)
3. [Phase Breakdown](#phase-breakdown)
4. [Detailed Implementation Steps](#detailed-implementation-steps)
5. [Testing Strategy](#testing-strategy)
6. [Rollback Plan](#rollback-plan)
7. [Timeline & Resources](#timeline--resources)

---

## Overview

### Current State
- **Architecture:** Procedural RAG with hard-coded if-else decision logic
- **Decision Points:** 6 major if-else chains with fixed thresholds
- **Query Routing:** Manual toggle between Standard/Deep Search
- **Web Search Decision:** 5 hard-coded rules
- **Limitations:** Rigid, no adaptability, requires manual mode selection

### Target State
- **Architecture:** Agent-orchestrated tool system
- **Tools:** 3 focused tools (RAG, WebSearch, AcademicSearch)
- **Decision Making:** LLM-powered agent with reasoning
- **Query Routing:** Automatic with fallback to manual
- **Advantages:** Adaptive, explainable, supports hybrid strategies

### Migration Strategy
**Hybrid approach** - Keep existing system as fallback, gradually introduce agent capabilities.

---

## Architecture Goals

### Primary Goals
1. **Intelligent Query Routing** - Auto-detect when to use which tools
2. **Adaptive Search Strategy** - Adjust based on intermediate results
3. **Hybrid Tool Usage** - Combine RAG + Web + Academic as needed
4. **Explainable Decisions** - Agent provides reasoning for choices
5. **Graceful Degradation** - Fallback to rule-based if agent fails

### Non-Goals (Keep Existing)
- Vector search implementation (ChromaDB)
- HyDE retrieval technique
- Re-ranking algorithms
- Rate limiting logic
- Caching mechanisms

---

## Phase Breakdown

```
Phase 0: Preparation (1-2 days)
    ↓
Phase 1: Tool Abstraction Layer (3-5 days)
    ↓
Phase 2: Basic Agent Framework (3-4 days)
    ↓
Phase 3: Agent Decision Logic (4-5 days)
    ↓
Phase 4: Integration & Testing (2-3 days)
    ↓
Phase 5: Optimization & Monitoring (2-3 days)

Total: ~15-20 days
```

---

## Detailed Implementation Steps

---

## PHASE 0: Preparation & Setup

**Goal:** Set up infrastructure for agent development without breaking existing code.

### Step 0.1: Create Agent Module Structure

**Create new directory structure:**
```bash
src/desktop_pdf_translator/agent/
├── __init__.py
├── tools/
│   ├── __init__.py
│   ├── base.py           # BaseTool interface
│   ├── rag_tool.py       # PDF search tool
│   ├── web_search_tool.py    # Web search tool
│   └── academic_search_tool.py  # Deep search tool
├── orchestrator.py       # Main agent orchestrator
├── prompts.py           # Agent prompt templates
└── models.py            # Tool schemas and response models
```

**Task List:**
- [ ] Create `src/desktop_pdf_translator/agent/` directory
- [ ] Create all subdirectories and `__init__.py` files
- [ ] Add to git (ensure tracking)

**Files to create:**
1. `src/desktop_pdf_translator/agent/__init__.py`
2. `src/desktop_pdf_translator/agent/tools/__init__.py`
3. `src/desktop_pdf_translator/agent/tools/base.py`
4. `src/desktop_pdf_translator/agent/orchestrator.py`
5. `src/desktop_pdf_translator/agent/prompts.py`
6. `src/desktop_pdf_translator/agent/models.py`

### Step 0.2: Define Tool Interface

**Create:** `src/desktop_pdf_translator/agent/tools/base.py`

```python
"""
Base tool interface for agent system.
All tools must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class ToolCategory(str, Enum):
    """Tool categories for agent decision making."""

    INTERNAL = "internal"  # RAG, local knowledge
    EXTERNAL_QUICK = "external_quick"  # Web search (fast)
    EXTERNAL_DEEP = "external_deep"  # Academic search (slow)


@dataclass
class ToolResult:
    """Standardized tool result format."""

    success: bool
    data: Any
    metadata: Dict[str, Any]
    error: Optional[str] = None
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'success': self.success,
            'data': self.data,
            'metadata': self.metadata,
            'error': self.error,
            'execution_time': self.execution_time
        }


class BaseTool(ABC):
    """
    Abstract base class for all agent tools.
    Each tool must implement execute() method.
    """

    def __init__(self, name: str, description: str, category: ToolCategory):
        self.name = name
        self.description = description
        self.category = category

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool with given parameters.

        Returns:
            ToolResult with standardized format
        """
        pass

    def get_schema(self) -> Dict[str, Any]:
        """
        Get tool schema for agent understanding.

        Returns:
            Tool specification including name, description, parameters
        """
        return {
            'name': self.name,
            'description': self.description,
            'category': self.category.value,
            'parameters': self._get_parameters()
        }

    @abstractmethod
    def _get_parameters(self) -> Dict[str, Any]:
        """Define tool parameters schema."""
        pass

    def estimate_execution_time(self, **kwargs) -> float:
        """
        Estimate execution time in seconds.
        Helps agent make performance-aware decisions.
        """
        return 1.0  # Default estimate
```

**Task List:**
- [ ] Create `base.py` with BaseTool interface
- [ ] Define ToolResult dataclass
- [ ] Add ToolCategory enum
- [ ] Add docstrings explaining each component

### Step 0.3: Define Response Models

**Create:** `src/desktop_pdf_translator/agent/models.py`

```python
"""
Data models for agent system.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class QueryType(str, Enum):
    """Query classification types."""

    DOCUMENT_SPECIFIC = "document_specific"  # Question about current PDF
    GENERAL_KNOWLEDGE = "general_knowledge"  # General concepts
    ACADEMIC_RESEARCH = "academic_research"  # Research questions
    FACTUAL_LOOKUP = "factual_lookup"       # Quick facts
    COMPARISON = "comparison"                # Compare concepts/papers
    RECENT_NEWS = "recent_news"             # Current events


class SearchStrategy(str, Enum):
    """Search strategy types."""

    PDF_ONLY = "pdf_only"                   # Use only RAG
    PDF_WITH_WEB = "pdf_with_web"          # RAG + Web search
    PDF_WITH_ACADEMIC = "pdf_with_academic" # RAG + Deep search
    WEB_ONLY = "web_only"                   # Only web search
    ACADEMIC_ONLY = "academic_only"         # Only deep search
    HYBRID_ALL = "hybrid_all"               # RAG + Web + Academic


@dataclass
class QueryAnalysis:
    """Result of query analysis by agent."""

    query: str
    query_type: QueryType
    complexity: str  # simple, moderate, complex
    requires_recent_info: bool
    requires_academic_depth: bool
    estimated_pdf_coverage: float  # 0.0-1.0
    keywords: List[str]
    reasoning: str

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ExecutionPlan:
    """Agent's execution plan for answering a query."""

    strategy: SearchStrategy
    tools_to_use: List[str]  # ['rag_tool', 'web_search_tool', ...]
    tool_sequence: str  # 'sequential' or 'parallel'
    reasoning: str
    estimated_time: float  # seconds
    confidence: float  # 0.0-1.0

    # Tool-specific parameters
    rag_params: Dict[str, Any] = field(default_factory=dict)
    web_search_params: Dict[str, Any] = field(default_factory=dict)
    academic_search_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDecision:
    """Records an agent decision for logging/analysis."""

    timestamp: datetime
    query: str
    analysis: QueryAnalysis
    plan: ExecutionPlan
    tools_executed: List[str]
    results: Dict[str, Any]
    final_answer: str
    total_time: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'query': self.query,
            'query_type': self.analysis.query_type.value,
            'strategy': self.plan.strategy.value,
            'tools_used': self.tools_executed,
            'execution_time': self.total_time,
            'reasoning': self.plan.reasoning
        }
```

**Task List:**
- [ ] Create `models.py` with all dataclasses
- [ ] Define QueryType and SearchStrategy enums
- [ ] Add comprehensive docstrings
- [ ] Ensure JSON serialization support

### Step 0.4: Install Agent Framework Dependencies

**Update:** `requirements.txt`

Add these dependencies:
```txt
# Agent framework
langchain>=0.1.0,<0.2.0  # Already exists - line 46
instructor>=0.4.0,<1.0.0  # For structured LLM outputs
pydantic>=2.5.0,<3.0.0    # Already exists - line 4
```

**Task List:**
- [ ] Review existing dependencies
- [ ] Add `instructor` for structured outputs
- [ ] Run `pip install instructor`
- [ ] Verify no conflicts

---

## PHASE 1: Tool Abstraction Layer

**Goal:** Wrap existing functionality as standalone tools without changing behavior.

### Step 1.1: Implement RAG Tool

**Create:** `src/desktop_pdf_translator/agent/tools/rag_tool.py`

```python
"""
RAG Tool - Searches local PDF documents using vector similarity.
Wraps existing ChromaDB vector search functionality.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .base import BaseTool, ToolResult, ToolCategory

logger = logging.getLogger(__name__)


class RAGTool(BaseTool):
    """
    Tool for searching local PDF documents.

    Capabilities:
    - Vector similarity search (semantic)
    - Hybrid search (semantic + keyword)
    - Context enrichment
    - Re-ranking

    Performance: Fast (~1-3 seconds)
    """

    def __init__(self, rag_chain):
        """
        Initialize RAG tool.

        Args:
            rag_chain: Existing EnhancedRAGChain instance
        """
        super().__init__(
            name="rag_tool",
            description=(
                "Search local PDF documents using semantic similarity. "
                "Best for questions about the current document's content. "
                "Fast and accurate for document-specific queries."
            ),
            category=ToolCategory.INTERNAL
        )
        self.rag_chain = rag_chain

    async def execute(
        self,
        query: str,
        document_id: Optional[str] = None,
        max_sources: int = 5,
        include_context: bool = True
    ) -> ToolResult:
        """
        Execute PDF search.

        Args:
            query: Search query
            document_id: Specific document ID (optional)
            max_sources: Maximum sources to retrieve
            include_context: Whether to include surrounding context

        Returns:
            ToolResult with PDF sources
        """
        start_time = datetime.now()

        try:
            # Use existing RAG chain retrieval logic
            pdf_sources = await self.rag_chain._retrieve_pdf_knowledge(
                question=query,
                document_id=document_id,
                max_sources=max_sources
            )

            # Calculate metadata
            avg_score = self._calculate_avg_score(pdf_sources)
            total_length = sum(len(s.get('text', '')) for s in pdf_sources)

            # Determine if results are sufficient
            is_sufficient = self._evaluate_sufficiency(query, pdf_sources, avg_score)

            execution_time = (datetime.now() - start_time).total_seconds()

            return ToolResult(
                success=True,
                data={
                    'sources': pdf_sources,
                    'num_sources': len(pdf_sources),
                    'avg_relevance_score': avg_score,
                    'total_content_length': total_length,
                    'is_sufficient': is_sufficient,
                    'top_source_preview': pdf_sources[0]['text'][:200] if pdf_sources else None
                },
                metadata={
                    'tool': 'rag_tool',
                    'query': query,
                    'document_id': document_id,
                    'execution_time': execution_time
                },
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"RAG tool execution failed: {e}")

            return ToolResult(
                success=False,
                data={},
                metadata={'tool': 'rag_tool', 'query': query},
                error=str(e),
                execution_time=execution_time
            )

    def _calculate_avg_score(self, sources: list) -> float:
        """Calculate average relevance score."""
        if not sources:
            return 0.0

        scores = []
        for source in sources:
            score = (
                source.get('rerank_score') or
                source.get('score') or
                source.get('final_score') or
                0.0
            )
            scores.append(score)

        return sum(scores) / len(scores) if scores else 0.0

    def _evaluate_sufficiency(
        self,
        query: str,
        sources: list,
        avg_score: float
    ) -> bool:
        """
        Evaluate if PDF sources are sufficient to answer the query.

        Returns:
            True if sufficient, False if external search might be needed
        """
        # No sources = definitely not sufficient
        if not sources or len(sources) == 0:
            return False

        # Very few sources with low scores = probably not sufficient
        if len(sources) < 3 and avg_score < 0.5:
            return False

        # Good number of sources with decent scores = sufficient
        if len(sources) >= 3 and avg_score >= 0.6:
            return True

        # Check content coverage
        query_keywords = set(query.lower().split())
        content_coverage = 0
        for source in sources[:3]:
            source_text = source.get('text', '').lower()
            matches = sum(1 for kw in query_keywords if kw in source_text and len(kw) > 3)
            content_coverage += matches

        # High coverage = sufficient
        return content_coverage >= 5

    def _get_parameters(self) -> Dict[str, Any]:
        """Define tool parameters."""
        return {
            'query': {
                'type': 'string',
                'description': 'Search query or question',
                'required': True
            },
            'document_id': {
                'type': 'string',
                'description': 'Specific document ID to search (optional)',
                'required': False
            },
            'max_sources': {
                'type': 'integer',
                'description': 'Maximum number of sources to retrieve',
                'default': 5,
                'required': False
            }
        }

    def estimate_execution_time(self, **kwargs) -> float:
        """Estimate execution time for RAG tool."""
        # RAG is typically fast: 1-3 seconds
        return 2.0
```

**Task List:**
- [ ] Create `rag_tool.py` with complete implementation
- [ ] Test RAG tool standalone (without agent)
- [ ] Verify it returns same results as current RAG chain
- [ ] Add unit tests for sufficiency evaluation

**Testing Checkpoint:**
```python
# Test standalone
rag_tool = RAGTool(existing_rag_chain)
result = await rag_tool.execute(query="What is transformer?")
assert result.success == True
assert len(result.data['sources']) > 0
```

---

### Step 1.2: Implement Web Search Tool

**Create:** `src/desktop_pdf_translator/agent/tools/web_search_tool.py`

```python
"""
Web Search Tool - Quick web search for facts and general knowledge.
Uses DuckDuckGo and Wikipedia.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .base import BaseTool, ToolResult, ToolCategory

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """
    Tool for quick web searches.

    Capabilities:
    - DuckDuckGo web search
    - Wikipedia article search
    - Content scraping and extraction

    Performance: Fast (~2-5 seconds)
    Best for: General knowledge, definitions, current events
    """

    def __init__(self, web_research_engine):
        """
        Initialize Web Search tool.

        Args:
            web_research_engine: Existing WebResearchEngine instance
        """
        super().__init__(
            name="web_search_tool",
            description=(
                "Search the web for general knowledge, facts, and definitions. "
                "Uses DuckDuckGo and Wikipedia. Fast (~5 seconds). "
                "Best for: explaining concepts, finding definitions, general context."
            ),
            category=ToolCategory.EXTERNAL_QUICK
        )
        self.web_engine = web_research_engine

    async def execute(
        self,
        query: str,
        num_results: int = 5,
        include_wikipedia: bool = True,
        pdf_context: str = ""
    ) -> ToolResult:
        """
        Execute web search.

        Args:
            query: Search query
            num_results: Number of results to retrieve
            include_wikipedia: Whether to include Wikipedia
            pdf_context: Optional PDF context for query enhancement

        Returns:
            ToolResult with web sources
        """
        start_time = datetime.now()

        try:
            # Use existing web research engine
            web_sources = await self.web_engine.research_topic(
                query=query,
                pdf_context=pdf_context
            )

            # Limit results
            web_sources = web_sources[:num_results]

            execution_time = (datetime.now() - start_time).total_seconds()

            # Format results
            formatted_sources = [
                {
                    'url': source.url,
                    'title': source.title,
                    'content': source.content,
                    'snippet': source.snippet,
                    'source_type': source.source_type,
                    'reliability_score': source.reliability_score
                }
                for source in web_sources
            ]

            return ToolResult(
                success=True,
                data={
                    'sources': formatted_sources,
                    'num_sources': len(formatted_sources),
                    'source_types': list(set(s['source_type'] for s in formatted_sources)),
                    'avg_reliability': sum(s['reliability_score'] for s in formatted_sources) / len(formatted_sources) if formatted_sources else 0.0
                },
                metadata={
                    'tool': 'web_search_tool',
                    'query': query,
                    'execution_time': execution_time
                },
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Web search tool execution failed: {e}")

            return ToolResult(
                success=False,
                data={'sources': []},
                metadata={'tool': 'web_search_tool', 'query': query},
                error=str(e),
                execution_time=execution_time
            )

    def _get_parameters(self) -> Dict[str, Any]:
        """Define tool parameters."""
        return {
            'query': {
                'type': 'string',
                'description': 'Web search query',
                'required': True
            },
            'num_results': {
                'type': 'integer',
                'description': 'Number of web sources to retrieve',
                'default': 5,
                'required': False
            },
            'pdf_context': {
                'type': 'string',
                'description': 'PDF context to enhance search queries',
                'default': '',
                'required': False
            }
        }

    def estimate_execution_time(self, **kwargs) -> float:
        """Estimate execution time for web search."""
        num_results = kwargs.get('num_results', 5)
        # ~1 second per result (scraping time)
        return min(num_results * 1.0, 5.0)  # Cap at 5 seconds
```

**Task List:**
- [ ] Create `web_search_tool.py` with complete implementation
- [ ] Test web search tool standalone
- [ ] Verify it wraps existing WebResearchEngine correctly
- [ ] Add error handling for rate limits

**Testing Checkpoint:**
```python
# Test standalone
web_tool = WebSearchTool(existing_web_engine)
result = await web_tool.execute(query="What is machine learning?")
assert result.success == True
assert len(result.data['sources']) > 0
```

---

### Step 1.3: Implement Academic Search Tool

**Create:** `src/desktop_pdf_translator/agent/tools/academic_search_tool.py`

```python
"""
Academic Search Tool - Deep academic research across scientific papers.
Implements multi-hop citation following.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .base import BaseTool, ToolResult, ToolCategory

logger = logging.getLogger(__name__)


class AcademicSearchTool(BaseTool):
    """
    Tool for deep academic research.

    Capabilities:
    - Search PubMed, Semantic Scholar, CORE
    - Multi-hop citation following
    - Citation graph analysis
    - Comprehensive paper synthesis

    Performance: Slow (~20-40 seconds with 3 hops)
    Best for: Research questions, literature review, academic depth
    """

    def __init__(self, deep_search_engine):
        """
        Initialize Academic Search tool.

        Args:
            deep_search_engine: Existing DeepSearchEngine instance
        """
        super().__init__(
            name="academic_search_tool",
            description=(
                "Deep search across academic papers (PubMed, Semantic Scholar, CORE). "
                "Multi-hop citation following for comprehensive research. "
                "SLOW (~20-40s) but thorough. "
                "Best for: research questions, finding authoritative sources, literature review."
            ),
            category=ToolCategory.EXTERNAL_DEEP
        )
        self.deep_search = deep_search_engine

    async def execute(
        self,
        query: str,
        max_hops: int = 3,
        max_papers: int = 20,
        pdf_context: str = "",
        progress_callback: Optional[callable] = None
    ) -> ToolResult:
        """
        Execute deep academic search.

        Args:
            query: Research question
            max_hops: Maximum citation hops (1-5)
            max_papers: Maximum total papers
            pdf_context: PDF context for grounding
            progress_callback: Optional progress callback

        Returns:
            ToolResult with academic papers
        """
        start_time = datetime.now()

        try:
            # Use existing deep search engine
            deep_result = await self.deep_search.deep_search(
                question=query,
                current_pdf_context=pdf_context,
                max_hops=max_hops,
                max_papers_per_hop=max_papers // max_hops,
                progress_callback=progress_callback
            )

            execution_time = (datetime.now() - start_time).total_seconds()

            # Format papers
            formatted_papers = [
                {
                    'paper_id': paper.paper_id,
                    'title': paper.title,
                    'authors': paper.authors,
                    'abstract': paper.abstract,
                    'year': paper.year,
                    'source': paper.source,
                    'citation_count': paper.citation_count,
                    'relevance_score': paper.relevance_score,
                    'pdf_url': paper.pdf_url,
                    'doi': paper.doi
                }
                for paper in deep_result.papers
            ]

            return ToolResult(
                success=True,
                data={
                    'papers': formatted_papers,
                    'answer': deep_result.answer,
                    'total_papers': deep_result.total_papers,
                    'total_hops': deep_result.total_hops,
                    'local_papers': len(deep_result.local_papers),
                    'search_path': {
                        'hops': len(deep_result.search_path.hops),
                        'total_papers': deep_result.search_path.total_papers
                    }
                },
                metadata={
                    'tool': 'academic_search_tool',
                    'query': query,
                    'hops_executed': deep_result.total_hops,
                    'execution_time': execution_time
                },
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Academic search tool execution failed: {e}")

            return ToolResult(
                success=False,
                data={'papers': []},
                metadata={'tool': 'academic_search_tool', 'query': query},
                error=str(e),
                execution_time=execution_time
            )

    def _get_parameters(self) -> Dict[str, Any]:
        """Define tool parameters."""
        return {
            'query': {
                'type': 'string',
                'description': 'Research question for academic search',
                'required': True
            },
            'max_hops': {
                'type': 'integer',
                'description': 'Maximum citation hops (1-5)',
                'default': 3,
                'required': False
            },
            'max_papers': {
                'type': 'integer',
                'description': 'Maximum total papers to analyze',
                'default': 20,
                'required': False
            },
            'pdf_context': {
                'type': 'string',
                'description': 'PDF context for grounding search',
                'default': '',
                'required': False
            }
        }

    def estimate_execution_time(self, **kwargs) -> float:
        """Estimate execution time for academic search."""
        max_hops = kwargs.get('max_hops', 3)
        # Rough estimate: 10 seconds per hop
        return max_hops * 10.0
```

**Task List:**
- [ ] Create `academic_search_tool.py` with complete implementation
- [ ] Test academic search tool standalone
- [ ] Verify it wraps DeepSearchEngine correctly
- [ ] Add progress callback support

**Testing Checkpoint:**
```python
# Test standalone
academic_tool = AcademicSearchTool(existing_deep_search)
result = await academic_tool.execute(query="Transformer architecture research", max_hops=2)
assert result.success == True
assert len(result.data['papers']) > 0
```

---

### Step 1.4: Create Tool Registry

**Create:** `src/desktop_pdf_translator/agent/tools/__init__.py`

```python
"""
Tool registry for agent system.
"""

from .base import BaseTool, ToolResult, ToolCategory
from .rag_tool import RAGTool
from .web_search_tool import WebSearchTool
from .academic_search_tool import AcademicSearchTool

__all__ = [
    'BaseTool',
    'ToolResult',
    'ToolCategory',
    'RAGTool',
    'WebSearchTool',
    'AcademicSearchTool',
    'ToolRegistry'
]


class ToolRegistry:
    """
    Registry for managing all available tools.
    Provides tool discovery and instantiation.
    """

    def __init__(self, rag_chain, web_research, deep_search):
        """
        Initialize tool registry with existing components.

        Args:
            rag_chain: EnhancedRAGChain instance
            web_research: WebResearchEngine instance
            deep_search: DeepSearchEngine instance
        """
        # Instantiate all tools
        self.tools = {
            'rag_tool': RAGTool(rag_chain),
            'web_search_tool': WebSearchTool(web_research),
            'academic_search_tool': AcademicSearchTool(deep_search)
        }

    def get_tool(self, tool_name: str) -> BaseTool:
        """Get tool by name."""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        return self.tools[tool_name]

    def get_all_tools(self) -> Dict[str, BaseTool]:
        """Get all available tools."""
        return self.tools.copy()

    def get_tool_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Get schemas for all tools (for agent understanding)."""
        return {
            name: tool.get_schema()
            for name, tool in self.tools.items()
        }
```

**Task List:**
- [ ] Create tool registry
- [ ] Test tool registration and retrieval
- [ ] Verify all tools accessible
- [ ] Add tool schema generation

---

## PHASE 2: Basic Agent Framework

**Goal:** Create agent orchestrator that can plan and execute tool sequences.

### Step 2.1: Create Agent Prompt Templates

**Create:** `src/desktop_pdf_translator/agent/prompts.py`

```python
"""
Prompt templates for agent decision making.
"""


QUERY_ANALYSIS_PROMPT = """
You are a query analysis expert. Analyze this user query and classify it.

QUERY: {query}

Available information:
- Current PDF document: {document_name}
- PDF has {num_chunks} chunks of content

Analyze the query and provide:

1. QUERY_TYPE: Choose ONE:
   - document_specific: Question about current PDF content
   - general_knowledge: General concepts/definitions
   - academic_research: Research or literature review question
   - factual_lookup: Quick fact or data
   - comparison: Comparing concepts or papers
   - recent_news: Current events or recent information

2. COMPLEXITY: simple | moderate | complex

3. REQUIRES_RECENT_INFO: Does the query ask for recent/latest/current information? true/false

4. REQUIRES_ACADEMIC_DEPTH: Does the query need academic papers or research depth? true/false

5. ESTIMATED_PDF_COVERAGE: Based on the query, estimate how likely the PDF contains the answer (0.0-1.0)

6. KEYWORDS: Extract 3-5 main keywords from the query

7. REASONING: Brief explanation of your classification (2-3 sentences)

Return ONLY valid JSON:
{{
    "query_type": "document_specific",
    "complexity": "simple",
    "requires_recent_info": false,
    "requires_academic_depth": false,
    "estimated_pdf_coverage": 0.8,
    "keywords": ["keyword1", "keyword2"],
    "reasoning": "This question asks about..."
}}
"""


STRATEGY_PLANNING_PROMPT = """
You are a search strategy planner. Based on the query analysis and available tools, create an optimal execution plan.

QUERY: {query}

QUERY ANALYSIS:
{analysis}

AVAILABLE TOOLS:
1. rag_tool (Internal PDF search)
   - Speed: FAST (~2 seconds)
   - Coverage: Current document only
   - Reliability: HIGH (if PDF has the info)
   - Use for: Document-specific questions

2. web_search_tool (External web search)
   - Speed: FAST (~3-5 seconds)
   - Coverage: General web + Wikipedia
   - Reliability: MEDIUM (depends on source quality)
   - Use for: General knowledge, definitions, context

3. academic_search_tool (Deep academic search)
   - Speed: SLOW (~20-40 seconds)
   - Coverage: Academic papers (PubMed, Semantic Scholar, CORE)
   - Reliability: HIGH (peer-reviewed sources)
   - Use for: Research questions, authoritative depth

PDF SEARCH RESULTS (if available):
{pdf_results_summary}

Create an execution plan:

1. STRATEGY: Choose ONE primary strategy:
   - pdf_only: Use only RAG (sufficient PDF coverage)
   - pdf_with_web: RAG + Web search (need general context)
   - pdf_with_academic: RAG + Academic search (need research depth)
   - web_only: Skip PDF (not document-specific)
   - academic_only: Pure research (deep academic query)
   - hybrid_all: All three (complex comparison or comprehensive analysis)

2. TOOLS_TO_USE: List of tools in order: ["rag_tool", "web_search_tool", ...]

3. TOOL_SEQUENCE: sequential | parallel
   - sequential: Execute tools one after another (when later tools depend on earlier results)
   - parallel: Execute tools simultaneously (when independent)

4. REASONING: Explain why this strategy is optimal (2-3 sentences)

5. ESTIMATED_TIME: Total estimated execution time in seconds

6. CONFIDENCE: How confident are you this strategy will work? (0.0-1.0)

Return ONLY valid JSON:
{{
    "strategy": "pdf_with_web",
    "tools_to_use": ["rag_tool", "web_search_tool"],
    "tool_sequence": "sequential",
    "reasoning": "Question requires...",
    "estimated_time": 7.0,
    "confidence": 0.85
}}
"""


WEB_SEARCH_DECISION_PROMPT = """
You are evaluating if web search is needed to supplement PDF results.

QUERY: {query}

PDF SEARCH RESULTS:
- Number of sources: {num_sources}
- Average relevance score: {avg_score:.2f}
- Total content length: {total_length} characters

Top PDF source preview:
"{top_source_preview}"

Evaluate if these PDF results can comprehensively answer the query:

Consider:
1. Do the PDF sources directly address the query?
2. Is the content detailed enough?
3. Are relevance scores high enough (>0.6 is good)?
4. Is additional context from the web needed?

Return ONLY valid JSON:
{{
    "needs_web_search": true/false,
    "reasoning": "Brief explanation (1-2 sentences)",
    "confidence": 0.0-1.0
}}
"""


SYNTHESIS_PROMPT = """
You are synthesizing information from multiple sources to answer a question.

QUESTION: {question}

AVAILABLE SOURCES:

{sources_summary}

REQUIREMENTS:
1. Answer in English (clear, professional, academic style)
2. ONLY use information from the sources provided above
3. DO NOT make up or hallucinate sources
4. Cite sources properly (e.g., "According to PDF Source 1...", "Research from Smith et al. (2024)...")
5. If sources conflict, acknowledge different perspectives
6. Be comprehensive yet concise

Generate a well-structured answer with proper citations.
"""
```

**Task List:**
- [ ] Create all prompt templates
- [ ] Test prompts with sample data
- [ ] Ensure JSON parsing works reliably
- [ ] Add prompt versioning for future updates

---

### Step 2.2: Implement Agent Orchestrator (Core)

**Create:** `src/desktop_pdf_translator/agent/orchestrator.py`

```python
"""
Agent orchestrator - Main decision-making and tool coordination.
"""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..translators import TranslatorFactory
from .tools import ToolRegistry, ToolResult
from .models import QueryAnalysis, ExecutionPlan, AgentDecision, QueryType, SearchStrategy
from .prompts import (
    QUERY_ANALYSIS_PROMPT,
    STRATEGY_PLANNING_PROMPT,
    WEB_SEARCH_DECISION_PROMPT,
    SYNTHESIS_PROMPT
)

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Main agent that orchestrates tool usage for answering questions.

    Capabilities:
    - Query analysis and classification
    - Strategy planning
    - Tool selection and execution
    - Multi-source synthesis
    - Decision logging and explanation
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_client,
        model: str = "gpt-4o-mini",
        enable_agent: bool = True,
        fallback_to_traditional: bool = True
    ):
        """
        Initialize agent orchestrator.

        Args:
            tool_registry: Registry of available tools
            llm_client: LLM client for agent decisions
            model: LLM model to use (gpt-4o-mini recommended for speed/cost)
            enable_agent: Whether to use agent or traditional logic
            fallback_to_traditional: Fallback if agent fails
        """
        self.tools = tool_registry
        self.llm_client = llm_client
        self.model = model
        self.enable_agent = enable_agent
        self.fallback_to_traditional = fallback_to_traditional

        # Decision history for learning
        self.decision_history: List[AgentDecision] = []

        logger.info(f"Agent orchestrator initialized (model: {model}, agent: {enable_agent})")

    async def answer_question(
        self,
        question: str,
        document_id: Optional[str] = None,
        document_name: str = "Unknown",
        num_chunks: int = 0,
        user_preferences: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Answer a question using agent-orchestrated tools.

        Args:
            question: User's question
            document_id: Current document ID
            document_name: Current document name (for context)
            num_chunks: Number of chunks in current document
            user_preferences: Optional user preferences (web enabled, etc.)

        Returns:
            Comprehensive answer with sources and agent reasoning
        """
        start_time = datetime.now()

        try:
            # Step 1: Analyze query
            logger.info(f"[AGENT] Analyzing query: {question[:100]}...")
            analysis = await self._analyze_query(question, document_name, num_chunks)

            # Step 2: Always start with RAG (PDF search) - it's fast and foundational
            logger.info("[AGENT] Step 1: Executing RAG tool (PDF search)...")
            rag_result = await self.tools.get_tool('rag_tool').execute(
                query=question,
                document_id=document_id,
                max_sources=5
            )

            # Step 3: Create execution plan based on PDF results
            logger.info("[AGENT] Step 2: Planning additional search strategy...")
            plan = await self._plan_strategy(question, analysis, rag_result)

            # Step 4: Execute planned tools
            logger.info(f"[AGENT] Step 3: Executing plan - {plan.strategy.value}")
            logger.info(f"[AGENT] Tools to use: {plan.tools_to_use}")

            tool_results = {'rag_tool': rag_result}

            # Execute additional tools based on plan
            if 'web_search_tool' in plan.tools_to_use:
                logger.info("[AGENT] Executing web search tool...")
                pdf_context = self._extract_context_from_rag(rag_result)
                web_result = await self.tools.get_tool('web_search_tool').execute(
                    query=question,
                    num_results=5,
                    pdf_context=pdf_context
                )
                tool_results['web_search_tool'] = web_result

            if 'academic_search_tool' in plan.tools_to_use:
                logger.info("[AGENT] Executing academic search tool...")
                pdf_context = self._extract_context_from_rag(rag_result)
                academic_result = await self.tools.get_tool('academic_search_tool').execute(
                    query=question,
                    max_hops=3,
                    max_papers=20,
                    pdf_context=pdf_context
                )
                tool_results['academic_search_tool'] = academic_result

            # Step 5: Synthesize final answer
            logger.info("[AGENT] Step 4: Synthesizing final answer...")
            final_answer = await self._synthesize_answer(question, tool_results)

            execution_time = (datetime.now() - start_time).total_seconds()

            # Step 6: Log decision for analysis
            decision = AgentDecision(
                timestamp=datetime.now(),
                query=question,
                analysis=analysis,
                plan=plan,
                tools_executed=list(tool_results.keys()),
                results=tool_results,
                final_answer=final_answer,
                total_time=execution_time
            )
            self.decision_history.append(decision)

            # Format response (compatible with existing system)
            return self._format_response(final_answer, tool_results, analysis, plan, execution_time)

        except Exception as e:
            logger.error(f"[AGENT] Failed to answer question: {e}")

            # Fallback to traditional if enabled
            if self.fallback_to_traditional:
                logger.warning("[AGENT] Falling back to traditional RAG...")
                return await self._fallback_to_traditional(question, document_id)
            else:
                raise

    async def _analyze_query(
        self,
        query: str,
        document_name: str,
        num_chunks: int
    ) -> QueryAnalysis:
        """
        Analyze query using LLM to understand intent and requirements.

        Returns:
            QueryAnalysis with classification and reasoning
        """
        prompt = QUERY_ANALYSIS_PROMPT.format(
            query=query,
            document_name=document_name,
            num_chunks=num_chunks
        )

        try:
            # Use LLM for analysis
            response = await self._call_llm(prompt, temperature=0.2)
            analysis_data = json.loads(response)

            return QueryAnalysis(
                query=query,
                query_type=QueryType(analysis_data['query_type']),
                complexity=analysis_data['complexity'],
                requires_recent_info=analysis_data['requires_recent_info'],
                requires_academic_depth=analysis_data['requires_academic_depth'],
                estimated_pdf_coverage=analysis_data['estimated_pdf_coverage'],
                keywords=analysis_data['keywords'],
                reasoning=analysis_data['reasoning']
            )

        except Exception as e:
            logger.error(f"Query analysis failed: {e}, using defaults")
            # Fallback to safe defaults
            return QueryAnalysis(
                query=query,
                query_type=QueryType.GENERAL_KNOWLEDGE,
                complexity="moderate",
                requires_recent_info=False,
                requires_academic_depth=False,
                estimated_pdf_coverage=0.5,
                keywords=query.split()[:3],
                reasoning="Analysis failed, using defaults"
            )

    async def _plan_strategy(
        self,
        query: str,
        analysis: QueryAnalysis,
        rag_result: ToolResult
    ) -> ExecutionPlan:
        """
        Plan search strategy based on query analysis and PDF results.

        Returns:
            ExecutionPlan with tools to use and reasoning
        """
        # Prepare PDF results summary
        if rag_result.success:
            pdf_summary = f"""
PDF Search Results:
- Sources found: {rag_result.data.get('num_sources', 0)}
- Average relevance: {rag_result.data.get('avg_relevance_score', 0):.2f}
- Sufficient: {rag_result.data.get('is_sufficient', False)}
- Preview: "{rag_result.data.get('top_source_preview', '')}"
"""
        else:
            pdf_summary = "PDF search failed - no results available"

        prompt = STRATEGY_PLANNING_PROMPT.format(
            query=query,
            analysis=self._format_analysis(analysis),
            pdf_results_summary=pdf_summary
        )

        try:
            # Use LLM for planning
            response = await self._call_llm(prompt, temperature=0.3)
            plan_data = json.loads(response)

            return ExecutionPlan(
                strategy=SearchStrategy(plan_data['strategy']),
                tools_to_use=plan_data['tools_to_use'],
                tool_sequence=plan_data['tool_sequence'],
                reasoning=plan_data['reasoning'],
                estimated_time=plan_data['estimated_time'],
                confidence=plan_data['confidence']
            )

        except Exception as e:
            logger.error(f"Strategy planning failed: {e}, using safe default")
            # Fallback to safe default: PDF only
            return ExecutionPlan(
                strategy=SearchStrategy.PDF_ONLY,
                tools_to_use=['rag_tool'],
                tool_sequence='sequential',
                reasoning="Planning failed, using PDF-only fallback",
                estimated_time=2.0,
                confidence=0.5
            )

    async def _synthesize_answer(
        self,
        question: str,
        tool_results: Dict[str, ToolResult]
    ) -> str:
        """
        Synthesize final answer from all tool results.

        Args:
            question: Original question
            tool_results: Results from all executed tools

        Returns:
            Final synthesized answer
        """
        # Prepare sources summary
        sources_parts = []

        # PDF sources
        if 'rag_tool' in tool_results and tool_results['rag_tool'].success:
            rag_data = tool_results['rag_tool'].data
            sources_parts.append("=== PDF SOURCES ===")
            for i, source in enumerate(rag_data.get('sources', [])[:3]):
                text = source.get('text', '')[:300]
                page = source.get('metadata', {}).get('page', 'N/A')
                sources_parts.append(f"[PDF Source {i+1}, Page {page}]: {text}...")

        # Web sources
        if 'web_search_tool' in tool_results and tool_results['web_search_tool'].success:
            web_data = tool_results['web_search_tool'].data
            sources_parts.append("\n=== WEB SOURCES ===")
            for i, source in enumerate(web_data.get('sources', [])[:3]):
                content = source.get('content', '')[:300]
                source_type = source.get('source_type', 'web')
                sources_parts.append(f"[Web Source {i+1} - {source_type}]: {content}...")

        # Academic sources
        if 'academic_search_tool' in tool_results and tool_results['academic_search_tool'].success:
            academic_data = tool_results['academic_search_tool'].data
            sources_parts.append("\n=== ACADEMIC PAPERS ===")
            for i, paper in enumerate(academic_data.get('papers', [])[:3]):
                title = paper.get('title', '')
                authors = ', '.join(paper.get('authors', [])[:2])
                abstract = paper.get('abstract', '')[:200]
                sources_parts.append(f"[Paper {i+1}] {title} ({authors}): {abstract}...")

        sources_summary = '\n'.join(sources_parts)

        # Create synthesis prompt
        prompt = SYNTHESIS_PROMPT.format(
            question=question,
            sources_summary=sources_summary
        )

        try:
            # Generate answer
            answer = await self._call_llm(prompt, temperature=0.3, max_tokens=1000)
            return answer

        except Exception as e:
            logger.error(f"Answer synthesis failed: {e}")
            return f"Error generating answer: {str(e)}"

    async def _call_llm(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 500
    ) -> str:
        """
        Call LLM for agent decisions.

        Args:
            prompt: Prompt for LLM
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response

        Returns:
            LLM response text
        """
        try:
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful AI assistant that provides structured responses in JSON format."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=temperature,
                max_tokens=max_tokens
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def _format_analysis(self, analysis: QueryAnalysis) -> str:
        """Format query analysis for prompt."""
        return f"""
- Type: {analysis.query_type.value}
- Complexity: {analysis.complexity}
- Requires recent info: {analysis.requires_recent_info}
- Requires academic depth: {analysis.requires_academic_depth}
- Estimated PDF coverage: {analysis.estimated_pdf_coverage:.1%}
- Keywords: {', '.join(analysis.keywords)}
- Reasoning: {analysis.reasoning}
"""

    def _extract_context_from_rag(self, rag_result: ToolResult) -> str:
        """Extract context string from RAG result."""
        if not rag_result.success:
            return ""

        sources = rag_result.data.get('sources', [])
        context_parts = [s.get('text', '')[:200] for s in sources[:3]]
        return ' '.join(context_parts)

    def _format_response(
        self,
        answer: str,
        tool_results: Dict[str, ToolResult],
        analysis: QueryAnalysis,
        plan: ExecutionPlan,
        execution_time: float
    ) -> Dict[str, Any]:
        """
        Format agent response to match existing system interface.

        Returns:
            Response dictionary compatible with existing UI
        """
        # Extract references from tool results
        pdf_references = []
        web_references = []

        # PDF references
        if 'rag_tool' in tool_results and tool_results['rag_tool'].success:
            pdf_sources = tool_results['rag_tool'].data.get('sources', [])
            pdf_references = self._create_pdf_references(pdf_sources)

        # Web references
        if 'web_search_tool' in tool_results and tool_results['web_search_tool'].success:
            web_sources = tool_results['web_search_tool'].data.get('sources', [])
            web_references = web_sources

        # Academic references (treated as web for now)
        if 'academic_search_tool' in tool_results and tool_results['academic_search_tool'].success:
            papers = tool_results['academic_search_tool'].data.get('papers', [])
            # Convert papers to web-like references
            for paper in papers:
                web_references.append({
                    'url': paper.get('pdf_url', ''),
                    'title': paper.get('title', ''),
                    'snippet': paper.get('abstract', '')[:200],
                    'source_type': 'academic',
                    'authors': ', '.join(paper.get('authors', [])[:3]),
                    'year': paper.get('year', '')
                })

        return {
            'answer': answer,
            'pdf_references': pdf_references,
            'web_references': web_references,
            'quality_metrics': {
                'confidence': plan.confidence,
                'strategy_used': plan.strategy.value,
                'tools_used': list(tool_results.keys())
            },
            'processing_time': execution_time,
            'sources_used': {
                'pdf_sources': len(pdf_references),
                'web_sources': len(web_references)
            },
            'search_type': 'agent_orchestrated',
            'web_search_used': 'web_search_tool' in tool_results or 'academic_search_tool' in tool_results,
            'timestamp': datetime.now().isoformat(),
            'agent_reasoning': {
                'query_analysis': analysis.reasoning,
                'strategy_reasoning': plan.reasoning,
                'tools_executed': list(tool_results.keys()),
                'execution_plan': plan.strategy.value
            }
        }

    def _create_pdf_references(self, pdf_sources: List[Dict]) -> List[Dict]:
        """Convert PDF sources to reference format."""
        references = []
        for source in pdf_sources:
            references.append({
                'page': source.get('metadata', {}).get('page', 0),
                'text': source.get('text', ''),
                'confidence': source.get('rerank_score', 0.0),
                'document_id': source.get('metadata', {}).get('document_id', ''),
                'document_path': source.get('metadata', {}).get('document_path', ''),
                'chunk_id': source.get('metadata', {}).get('chunk_id', '')
            })
        return references

    async def _fallback_to_traditional(
        self,
        question: str,
        document_id: Optional[str]
    ) -> Dict[str, Any]:
        """Fallback to traditional RAG chain."""
        logger.warning("[AGENT] Using traditional RAG chain as fallback")

        # This would call the original EnhancedRAGChain
        # To be implemented in Phase 4
        raise NotImplementedError("Traditional fallback not yet implemented")
```

**Task List:**
- [ ] Create orchestrator.py with core agent logic
- [ ] Implement query analysis
- [ ] Implement strategy planning
- [ ] Implement synthesis
- [ ] Add comprehensive logging
- [ ] Test each method independently

---

## PHASE 3: Agent Decision Logic

**Goal:** Implement intelligent decision-making for tool selection.

### Step 3.1: Implement Smart Web Search Decision

**Add to:** `src/desktop_pdf_translator/agent/orchestrator.py`

```python
    async def _should_use_web_search(
        self,
        query: str,
        rag_result: ToolResult
    ) -> tuple[bool, str]:
        """
        Agent-powered decision: Should we use web search?

        REPLACES: rag_chain.py lines 477-540 (hard-coded rules)

        Args:
            query: User's question
            rag_result: Results from RAG tool

        Returns:
            (needs_web_search: bool, reasoning: str)
        """
        if not rag_result.success:
            return True, "PDF search failed - web search needed"

        rag_data = rag_result.data

        # If RAG tool already determined sufficiency, trust it
        if rag_data.get('is_sufficient', False):
            return False, "RAG tool reports sufficient PDF coverage"

        # Use LLM for nuanced decision
        prompt = WEB_SEARCH_DECISION_PROMPT.format(
            query=query,
            num_sources=rag_data.get('num_sources', 0),
            avg_score=rag_data.get('avg_relevance_score', 0),
            total_length=rag_data.get('total_content_length', 0),
            top_source_preview=rag_data.get('top_source_preview', '')[:200]
        )

        try:
            response = await self._call_llm(prompt, temperature=0.2, max_tokens=200)
            decision_data = json.loads(response)

            needs_web = decision_data['needs_web_search']
            reasoning = decision_data['reasoning']
            confidence = decision_data['confidence']

            logger.info(f"[AGENT] Web search decision: {needs_web} (confidence: {confidence:.2f})")
            logger.info(f"[AGENT] Reasoning: {reasoning}")

            return needs_web, reasoning

        except Exception as e:
            logger.error(f"Web search decision failed: {e}, using conservative default")
            # Conservative: use web search if uncertain
            return True, f"Decision failed ({e}), using web search to be safe"
```

**Task List:**
- [ ] Implement LLM-powered web search decision
- [ ] Test with various query types
- [ ] Compare with old hard-coded logic
- [ ] Log all decisions for analysis

---

### Step 3.2: Implement Query Type Detection

**Enhancement to:** `_analyze_query()` method

The query analysis already handles this, but we can add pattern boosting:

```python
    def _boost_analysis_with_patterns(self, analysis: QueryAnalysis, query: str) -> QueryAnalysis:
        """
        Boost LLM analysis with pattern matching as safety check.
        Combines agent intelligence with deterministic patterns.
        """
        query_lower = query.lower()

        # Pattern: Academic indicators
        academic_patterns = [
            'research', 'study', 'papers', 'literature', 'survey',
            'state of the art', 'recent advances', 'review'
        ]
        if any(p in query_lower for p in academic_patterns):
            analysis.requires_academic_depth = True

        # Pattern: Recent info indicators
        recent_patterns = [
            'latest', 'recent', 'current', 'new', '2024', '2025',
            'today', 'now', 'modern'
        ]
        if any(p in query_lower for p in recent_patterns):
            analysis.requires_recent_info = True

        # Pattern: Document-specific indicators
        document_patterns = [
            'this paper', 'this document', 'the author says',
            'according to this', 'in this pdf'
        ]
        if any(p in query_lower for p in document_patterns):
            analysis.query_type = QueryType.DOCUMENT_SPECIFIC
            analysis.estimated_pdf_coverage = max(analysis.estimated_pdf_coverage, 0.8)

        return analysis
```

**Task List:**
- [ ] Add pattern boosting to query analysis
- [ ] Test that it catches edge cases
- [ ] Ensure it doesn't override valid LLM decisions

---

### Step 3.3: Implement Hybrid Strategy Support

**Add to:** `orchestrator.py`

```python
    async def _execute_hybrid_strategy(
        self,
        question: str,
        document_id: Optional[str],
        analysis: QueryAnalysis
    ) -> Dict[str, ToolResult]:
        """
        Execute hybrid strategy (RAG + Web + Academic in parallel).

        NEW CAPABILITY: Previously only either/or, now can combine all three!
        """
        import asyncio

        logger.info("[AGENT] Executing hybrid strategy (all tools in parallel)...")

        # Execute all three tools in parallel
        results = await asyncio.gather(
            self.tools.get_tool('rag_tool').execute(
                query=question,
                document_id=document_id
            ),
            self.tools.get_tool('web_search_tool').execute(
                query=question,
                num_results=3
            ),
            self.tools.get_tool('academic_search_tool').execute(
                query=question,
                max_hops=2,
                max_papers=10
            ),
            return_exceptions=True
        )

        # Process results
        tool_results = {}
        tool_names = ['rag_tool', 'web_search_tool', 'academic_search_tool']

        for i, (name, result) in enumerate(zip(tool_names, results)):
            if isinstance(result, Exception):
                logger.error(f"[AGENT] {name} failed: {result}")
                tool_results[name] = ToolResult(
                    success=False,
                    data={},
                    metadata={'tool': name},
                    error=str(result)
                )
            else:
                tool_results[name] = result

        return tool_results
```

**Task List:**
- [ ] Implement parallel tool execution
- [ ] Add error handling for individual tool failures
- [ ] Ensure results are properly aggregated
- [ ] Test with all three tools simultaneously

---

## PHASE 4: Integration & Migration

**Goal:** Integrate agent system with existing codebase while maintaining backward compatibility.

### Step 4.1: Create Agent-Enhanced RAG Chain

**Create:** `src/desktop_pdf_translator/rag/agent_rag_chain.py`

```python
"""
Agent-enhanced RAG chain that wraps traditional RAG with agent orchestration.
Provides backward compatibility while enabling agent capabilities.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .rag_chain import EnhancedRAGChain
from ..agent.orchestrator import AgentOrchestrator
from ..agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class AgentRAGChain(EnhancedRAGChain):
    """
    Enhanced RAG chain with agent orchestration.

    Modes:
    - agent_mode=True: Use agent for decisions
    - agent_mode=False: Use traditional if-else logic
    - agent_mode='auto': Try agent, fallback to traditional on error
    """

    def __init__(self, vector_store, web_research, agent_mode='auto', llm_model='gpt-4o-mini'):
        """
        Initialize agent-enhanced RAG chain.

        Args:
            vector_store: ChromaDB manager
            web_research: Web research engine
            agent_mode: 'auto', True, or False
            llm_model: Model for agent decisions (gpt-4o-mini recommended)
        """
        # Initialize parent (traditional RAG)
        super().__init__(vector_store, web_research)

        self.agent_mode = agent_mode

        # Initialize agent components
        if agent_mode != False:
            try:
                # Create tool registry
                tool_registry = ToolRegistry(
                    rag_chain=self,  # Pass self for RAG tool
                    web_research=web_research,
                    deep_search=self.deep_search_engine
                )

                # Create agent orchestrator
                self.agent = AgentOrchestrator(
                    tool_registry=tool_registry,
                    llm_client=self.translator.client if hasattr(self.translator, 'client') else None,
                    model=llm_model,
                    enable_agent=True,
                    fallback_to_traditional=True
                )

                logger.info(f"Agent mode enabled: {agent_mode}")

            except Exception as e:
                logger.error(f"Failed to initialize agent: {e}")
                self.agent = None
                if agent_mode == True:  # Strict mode
                    raise
        else:
            self.agent = None
            logger.info("Agent mode disabled - using traditional logic")

    async def answer_question(
        self,
        question: str,
        document_id: Optional[str] = None,
        include_web_research: bool = True,
        max_pdf_sources: int = 5,
        max_web_sources: int = 5,
        use_deep_search: bool = False,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Answer question - auto-selects agent or traditional based on configuration.

        OVERRIDES: parent answer_question() method

        Returns:
            Answer with sources (same format as traditional for compatibility)
        """
        # Determine which mode to use
        should_use_agent = self._should_use_agent_mode()

        if should_use_agent and self.agent:
            try:
                logger.info("[MODE] Using AGENT orchestration")

                # Get document info for agent context
                document_name = "Current Document"  # TODO: Get actual name
                num_chunks = 0  # TODO: Get from vector store

                # Use agent
                result = await self.agent.answer_question(
                    question=question,
                    document_id=document_id,
                    document_name=document_name,
                    num_chunks=num_chunks,
                    user_preferences={
                        'include_web_research': include_web_research,
                        'use_deep_search': use_deep_search
                    }
                )

                logger.info("[MODE] Agent execution successful")
                return result

            except Exception as e:
                logger.error(f"[MODE] Agent failed: {e}")

                if self.agent_mode == True:  # Strict agent mode
                    raise
                else:  # Auto mode - fallback to traditional
                    logger.warning("[MODE] Falling back to traditional RAG")
                    return await super().answer_question(
                        question, document_id, include_web_research,
                        max_pdf_sources, max_web_sources, use_deep_search,
                        progress_callback
                    )
        else:
            # Use traditional RAG
            logger.info("[MODE] Using TRADITIONAL RAG logic")
            return await super().answer_question(
                question, document_id, include_web_research,
                max_pdf_sources, max_web_sources, use_deep_search,
                progress_callback
            )

    def _should_use_agent_mode(self) -> bool:
        """Determine if agent mode should be used."""
        if self.agent_mode == False:
            return False
        elif self.agent_mode == True:
            return True
        else:  # 'auto'
            # Use agent if available
            return self.agent is not None
```

**Task List:**
- [ ] Create AgentRAGChain class
- [ ] Implement mode switching (agent/traditional/auto)
- [ ] Add backward compatibility
- [ ] Test mode switching works correctly

---

### Step 4.2: Update GUI to Support Agent Mode

**Modify:** `src/desktop_pdf_translator/gui/rag_chat_panel.py`

**Changes needed:**

1. **Add agent mode toggle** (around line 1210):

```python
# In create_input_area(), add agent mode toggle

# Agent Mode toggle - new feature!
self.agent_mode_cb = QCheckBox(" AI Agent Mode")
try:
    import qtawesome as qta
    self.agent_mode_cb.setIcon(qta.icon('fa5s.robot', color='#4CAF50'))
except:
    pass
self.agent_mode_cb.setChecked(True)  # Default ON - use agent
self.agent_mode_cb.setToolTip(
    "Use AI agent for intelligent tool selection (Recommended)\n"
    "Agent automatically decides when to use web/academic search"
)
self.agent_mode_cb.stateChanged.connect(self._toggle_agent_mode)
options_layout.addWidget(self.agent_mode_cb)
```

2. **Add toggle handler**:

```python
def _toggle_agent_mode(self):
    """Toggle agent mode on/off."""
    is_enabled = self.agent_mode_cb.isChecked()

    if is_enabled:
        self.status_label.setText("AI Agent mode enabled - automatic tool selection")
        # When agent is on, hide manual web research checkbox
        # Agent decides automatically
        self.web_research_cb.setVisible(False)
        logger.info("Agent mode enabled")
    else:
        self.status_label.setText("Manual mode - you control search options")
        self.web_research_cb.setVisible(True)
        logger.info("Agent mode disabled")
```

3. **Update RAG system initialization** (around line 1398):

```python
def initialize_rag_system(self):
    """Initialize the RAG system components with agent support."""
    try:
        # Initialize vector store
        self.vector_store = ChromaDBManager()

        # Initialize web research
        self.web_research = WebResearchEngine()

        # Initialize RAG chain WITH AGENT
        from ..rag.agent_rag_chain import AgentRAGChain

        self.rag_chain = AgentRAGChain(
            vector_store=self.vector_store,
            web_research=self.web_research,
            agent_mode='auto',  # Auto mode: try agent, fallback to traditional
            llm_model='gpt-4o-mini'  # Fast and cheap for decisions
        )

        # Initialize reference manager
        self.reference_manager = ReferenceManager()
        self.reference_manager.set_pdf_viewer_callback(self._navigate_to_pdf)
        self.reference_manager.set_web_browser_callback(self._open_web_link)

        self.status_label.setText("RAG system ready (Agent mode enabled)")
        logger.info("RAG system initialized with agent support")

    except Exception as e:
        error_msg = f"RAG system initialization error: {str(e)}"
        self.status_label.setText(error_msg)
        logger.error(error_msg)
        QMessageBox.warning(self, "Initialization Error", error_msg)
```

4. **Update status display** (already done in previous steps):

```python
# Status now shows agent reasoning
if 'agent_reasoning' in answer_data:
    reasoning = answer_data['agent_reasoning']
    logger.info(f"[AGENT] Strategy: {reasoning.get('execution_plan')}")
    logger.info(f"[AGENT] Tools: {reasoning.get('tools_executed')}")
```

**Task List:**
- [ ] Add agent mode toggle to UI
- [ ] Update RAG initialization to use AgentRAGChain
- [ ] Add agent reasoning display
- [ ] Test UI with agent enabled/disabled

---

### Step 4.3: Configuration and Settings

**Modify:** `src/desktop_pdf_translator/config/models.py`

Add agent settings to existing config:

```python
class AgentSettings(BaseModel):
    """Agent orchestration settings."""

    enabled: bool = Field(True, description="Enable agent-based orchestration")
    mode: Literal["auto", "always", "never"] = Field("auto", description="Agent mode")

    # LLM settings for agent
    llm_model: str = Field("gpt-4o-mini", description="Model for agent decisions")
    temperature: float = Field(0.2, ge=0.0, le=1.0, description="Agent decision temperature")
    max_tokens: int = Field(500, ge=100, le=2000, description="Max tokens for agent responses")

    # Decision thresholds (agent uses these as hints)
    min_pdf_sources: int = Field(3, ge=1, le=10, description="Minimum PDF sources for sufficiency")
    min_relevance_score: float = Field(0.6, ge=0.0, le=1.0, description="Minimum avg score for sufficiency")

    # Performance settings
    enable_parallel_execution: bool = Field(True, description="Allow parallel tool execution")
    max_total_execution_time: int = Field(60, ge=10, le=300, description="Max total time (seconds)")

    # Logging and monitoring
    log_decisions: bool = Field(True, description="Log all agent decisions")
    decision_log_path: str = Field("data/agent_decisions.jsonl", description="Decision log file")


class AppSettings(BaseModel):
    """Main application settings model."""

    # ... existing settings ...

    agent: AgentSettings = Field(default_factory=AgentSettings)  # ADD THIS
```

**Task List:**
- [ ] Add AgentSettings to config models
- [ ] Update default config file
- [ ] Test config loading
- [ ] Add config validation

---

## PHASE 5: Testing & Optimization

**Goal:** Comprehensive testing and performance optimization.

### Step 5.1: Unit Tests for Tools

**Create:** `tests/agent/test_tools.py`

```python
"""
Unit tests for agent tools.
"""

import pytest
import asyncio
from src.desktop_pdf_translator.agent.tools import RAGTool, WebSearchTool, AcademicSearchTool


class TestRAGTool:
    """Test RAG tool functionality."""

    @pytest.mark.asyncio
    async def test_rag_tool_basic_search(self, mock_rag_chain):
        """Test basic RAG tool search."""
        tool = RAGTool(mock_rag_chain)
        result = await tool.execute(query="What is transformer?")

        assert result.success == True
        assert 'sources' in result.data
        assert result.execution_time > 0

    @pytest.mark.asyncio
    async def test_rag_tool_sufficiency_evaluation(self, mock_rag_chain):
        """Test sufficiency evaluation logic."""
        tool = RAGTool(mock_rag_chain)
        result = await tool.execute(query="What is transformer?")

        assert 'is_sufficient' in result.data
        assert isinstance(result.data['is_sufficient'], bool)

    @pytest.mark.asyncio
    async def test_rag_tool_error_handling(self, broken_rag_chain):
        """Test error handling."""
        tool = RAGTool(broken_rag_chain)
        result = await tool.execute(query="test")

        assert result.success == False
        assert result.error is not None


class TestWebSearchTool:
    """Test Web Search tool functionality."""

    @pytest.mark.asyncio
    async def test_web_search_basic(self, mock_web_engine):
        """Test basic web search."""
        tool = WebSearchTool(mock_web_engine)
        result = await tool.execute(query="Python programming")

        assert result.success == True
        assert 'sources' in result.data

    @pytest.mark.asyncio
    async def test_web_search_with_context(self, mock_web_engine):
        """Test web search with PDF context."""
        tool = WebSearchTool(mock_web_engine)
        result = await tool.execute(
            query="transformer architecture",
            pdf_context="attention mechanism encoder decoder"
        )

        assert result.success == True


class TestAcademicSearchTool:
    """Test Academic Search tool functionality."""

    @pytest.mark.asyncio
    async def test_academic_search_basic(self, mock_deep_search):
        """Test basic academic search."""
        tool = AcademicSearchTool(mock_deep_search)
        result = await tool.execute(query="transformer research")

        assert result.success == True
        assert 'papers' in result.data

    @pytest.mark.asyncio
    async def test_academic_search_hops(self, mock_deep_search):
        """Test multi-hop search."""
        tool = AcademicSearchTool(mock_deep_search)
        result = await tool.execute(query="BERT architecture", max_hops=2)

        assert result.data['total_hops'] <= 2
```

**Task List:**
- [ ] Create test file structure
- [ ] Implement all unit tests
- [ ] Create mock objects for testing
- [ ] Ensure >80% code coverage

---

### Step 5.2: Integration Tests

**Create:** `tests/agent/test_orchestrator.py`

```python
"""
Integration tests for agent orchestrator.
"""

import pytest
from src.desktop_pdf_translator.agent.orchestrator import AgentOrchestrator


class TestAgentOrchestrator:
    """Test agent orchestration end-to-end."""

    @pytest.mark.asyncio
    async def test_document_specific_query(self, agent, sample_pdf):
        """Test agent handles document-specific query with PDF only."""
        result = await agent.answer_question(
            question="What is the main contribution of this paper?",
            document_id="test_doc",
            document_name="test.pdf",
            num_chunks=100
        )

        # Should use only RAG tool
        assert 'agent_reasoning' in result
        assert 'rag_tool' in result['agent_reasoning']['tools_executed']
        assert 'web_search_tool' not in result['agent_reasoning']['tools_executed']

    @pytest.mark.asyncio
    async def test_general_knowledge_query(self, agent):
        """Test agent uses web search for general knowledge."""
        result = await agent.answer_question(
            question="What is machine learning?",
            document_id=None,
            document_name="unrelated.pdf",
            num_chunks=50
        )

        # Should use RAG + Web
        tools_used = result['agent_reasoning']['tools_executed']
        assert 'rag_tool' in tools_used or 'web_search_tool' in tools_used

    @pytest.mark.asyncio
    async def test_research_query(self, agent):
        """Test agent uses academic search for research questions."""
        result = await agent.answer_question(
            question="Latest research on transformer architectures since 2024",
            document_id="test_doc",
            document_name="transformers.pdf",
            num_chunks=100
        )

        # Should use academic search
        tools_used = result['agent_reasoning']['tools_executed']
        assert 'academic_search_tool' in tools_used
```

**Task List:**
- [ ] Create integration test suite
- [ ] Test all query types
- [ ] Test tool combinations
- [ ] Test error handling and fallbacks

---

### Step 5.3: Performance Optimization

**Add to:** `orchestrator.py`

```python
class AgentOrchestrator:
    # ... existing code ...

    def __init__(self, ...):
        # ... existing code ...

        # Add caching for agent decisions
        self.decision_cache = {}  # Cache recent decisions
        self.cache_ttl = 300  # 5 minutes

    async def answer_question(self, question: str, **kwargs):
        """Enhanced with decision caching."""

        # Check cache for identical queries
        cache_key = self._get_cache_key(question, kwargs)
        if cache_key in self.decision_cache:
            cached_decision, timestamp = self.decision_cache[cache_key]
            age = (datetime.now() - timestamp).total_seconds()

            if age < self.cache_ttl:
                logger.info(f"[CACHE] Using cached decision (age: {age:.1f}s)")
                # Reuse the same tool execution plan
                return await self._execute_cached_plan(cached_decision, question, kwargs)

        # ... rest of method ...

        # Cache the decision
        self.decision_cache[cache_key] = (decision, datetime.now())

    def _get_cache_key(self, question: str, kwargs: Dict) -> str:
        """Generate cache key for query."""
        import hashlib
        key_str = f"{question}_{kwargs.get('document_id', '')}"
        return hashlib.md5(key_str.encode()).hexdigest()
```

**Optimizations to implement:**
- [ ] Add decision caching (avoid re-analyzing identical queries)
- [ ] Implement parallel tool execution when possible
- [ ] Add timeout handling (max 60 seconds)
- [ ] Optimize LLM calls (use cheaper models for simple decisions)

---

### Step 5.4: Monitoring and Logging

**Create:** `src/desktop_pdf_translator/agent/monitoring.py`

```python
"""
Monitoring and analytics for agent decisions.
"""

import json
import logging
from pathlib import Path
from typing import List
from datetime import datetime

from .models import AgentDecision

logger = logging.getLogger(__name__)


class AgentMonitor:
    """
    Monitors agent decisions and logs for analysis.
    """

    def __init__(self, log_path: str = "data/agent_decisions.jsonl"):
        """Initialize monitor."""
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_decision(self, decision: AgentDecision):
        """Log an agent decision to file."""
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(decision.to_dict()) + '\n')
        except Exception as e:
            logger.error(f"Failed to log decision: {e}")

    def get_decision_stats(self) -> Dict[str, Any]:
        """Get statistics about agent decisions."""
        try:
            decisions = self._load_decisions()

            if not decisions:
                return {}

            # Calculate stats
            total_decisions = len(decisions)

            strategy_counts = {}
            avg_execution_time = 0
            tool_usage = {}

            for dec in decisions:
                # Strategy distribution
                strategy = dec.get('strategy_used', 'unknown')
                strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

                # Execution time
                avg_execution_time += dec.get('execution_time', 0)

                # Tool usage
                for tool in dec.get('tools_used', []):
                    tool_usage[tool] = tool_usage.get(tool, 0) + 1

            return {
                'total_decisions': total_decisions,
                'strategy_distribution': strategy_counts,
                'avg_execution_time': avg_execution_time / total_decisions,
                'tool_usage': tool_usage
            }

        except Exception as e:
            logger.error(f"Failed to calculate stats: {e}")
            return {}

    def _load_decisions(self) -> List[Dict]:
        """Load all decisions from log file."""
        decisions = []
        try:
            if self.log_path.exists():
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        decisions.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to load decisions: {e}")
        return decisions
```

**Task List:**
- [ ] Create monitoring system
- [ ] Implement decision logging
- [ ] Add statistics calculation
- [ ] Create dashboard/viewer for decisions

---

## Testing Strategy

### Test Phases

#### Phase 1: Unit Testing
- [ ] Test each tool independently
- [ ] Test tool registry
- [ ] Test prompt templates
- [ ] Mock external dependencies (LLM, APIs)

#### Phase 2: Integration Testing
- [ ] Test agent orchestrator with all tools
- [ ] Test mode switching (agent/traditional)
- [ ] Test error handling and fallbacks
- [ ] Test parallel vs sequential execution

#### Phase 3: End-to-End Testing
- [ ] Test complete flow through GUI
- [ ] Test with real queries
- [ ] Compare agent vs traditional results
- [ ] Measure performance differences

#### Phase 4: A/B Testing
- [ ] Run both systems in parallel
- [ ] Compare answer quality
- [ ] Compare execution time
- [ ] Collect user feedback

### Test Queries for Each Mode

**Document-Specific (Should use PDF only):**
- "What is the main contribution of this paper?"
- "Summarize the methodology section"
- "What datasets were used in this study?"

**General Knowledge (Should use PDF + Web):**
- "What is a transformer architecture?"
- "Explain attention mechanisms"
- "Define neural networks"

**Academic Research (Should use PDF + Academic):**
- "Latest research on transformers since 2024"
- "State of the art in attention mechanisms"
- "Survey of encoder-decoder architectures"

**Hybrid (Should use all three):**
- "Compare this paper's approach with recent BERT variants"
- "How does this methodology relate to current research trends?"

---

## Rollback Plan

### Rollback Checkpoints

Each phase has a rollback point:

**After Phase 0:**
```python
# No code changes yet, only new files
# Rollback: Delete agent/ directory
```

**After Phase 1:**
```python
# Tools created but not used
# Rollback: Keep tools, don't integrate
```

**After Phase 2:**
```python
# Agent created but not connected to main system
# Rollback: Don't use AgentRAGChain
```

**After Phase 3:**
```python
# Agent integrated with 'auto' mode
# Rollback: Set agent_mode='never' in config
```

**After Phase 4:**
```python
# Full integration
# Rollback: Toggle agent mode OFF in GUI
```

### Emergency Rollback

If critical issues occur:

1. **GUI Toggle**: Add emergency disable button
```python
# In rag_chat_panel.py
self.emergency_disable_agent_btn = QPushButton("Disable Agent (Emergency)")
self.emergency_disable_agent_btn.clicked.connect(self._emergency_disable_agent)
```

2. **Config Override**:
```toml
# In config.toml
[agent]
enabled = false  # Emergency disable
```

3. **Code Rollback**:
```python
# In agent_rag_chain.py
def __init__(self, ..., agent_mode='never'):  # Change default to 'never'
```

---

## Timeline & Resources

### Estimated Timeline (1 developer)

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| Phase 0: Preparation | 1-2 days | File structure, base classes |
| Phase 1: Tool Layer | 3-5 days | 3 tools + registry |
| Phase 2: Agent Framework | 3-4 days | Orchestrator + prompts |
| Phase 3: Decision Logic | 4-5 days | Smart decisions + integration |
| Phase 4: Integration | 2-3 days | GUI updates + config |
| Phase 5: Testing | 2-3 days | Tests + optimization |
| **TOTAL** | **15-22 days** | **Complete agent system** |

### Resource Requirements

**LLM API Usage:**
- Query analysis: ~200 tokens per query
- Strategy planning: ~300 tokens per query
- Web search decision: ~200 tokens per query
- Synthesis: ~1000 tokens per query
- **Total:** ~1700 tokens per query (~$0.001 with GPT-4o-mini)

**Development Environment:**
- Python 3.11+
- Existing dependencies + `instructor` library
- Test framework (pytest)
- LLM API access (OpenAI)

---

## Success Criteria

### Phase 1 Complete When:
- [ ] All 3 tools implemented and tested
- [ ] Tools return consistent ToolResult format
- [ ] Tools wrap existing functionality without breaking it
- [ ] Unit tests passing

### Phase 2 Complete When:
- [ ] Agent orchestrator can analyze queries
- [ ] Agent can create execution plans
- [ ] Agent can execute tool sequences
- [ ] Integration tests passing

### Phase 3 Complete When:
- [ ] Agent makes intelligent tool selection decisions
- [ ] Decisions are logged and explainable
- [ ] Performance is acceptable (<2s overhead)
- [ ] Accuracy matches or exceeds traditional system

### Phase 4 Complete When:
- [ ] GUI supports agent mode toggle
- [ ] Agent integrated with existing RAG chain
- [ ] Backward compatibility maintained
- [ ] Users can switch between modes seamlessly

### Phase 5 Complete When:
- [ ] All tests passing (>80% coverage)
- [ ] Performance optimized (caching, parallel execution)
- [ ] Monitoring dashboard available
- [ ] Documentation complete

---

## Migration Checklist

### Pre-Migration
- [ ] Backup current codebase
- [ ] Create feature branch: `feature/agent-refactor`
- [ ] Set up test environment
- [ ] Install new dependencies

### Phase 0
- [ ] Create agent module structure
- [ ] Define base tool interface
- [ ] Define data models
- [ ] Create prompt templates

### Phase 1
- [ ] Implement RAGTool
- [ ] Implement WebSearchTool
- [ ] Implement AcademicSearchTool
- [ ] Create ToolRegistry
- [ ] Test all tools independently

### Phase 2
- [ ] Implement AgentOrchestrator
- [ ] Implement query analysis
- [ ] Implement strategy planning
- [ ] Implement synthesis
- [ ] Test orchestrator

### Phase 3
- [ ] Implement smart web search decision
- [ ] Implement hybrid strategy support
- [ ] Add decision logging
- [ ] Test all decision paths

### Phase 4
- [ ] Create AgentRAGChain wrapper
- [ ] Update GUI with agent mode toggle
- [ ] Add agent settings to config
- [ ] Integration testing

### Phase 5
- [ ] Create comprehensive test suite
- [ ] Performance optimization
- [ ] Monitoring dashboard
- [ ] Documentation

### Post-Migration
- [ ] A/B testing (agent vs traditional)
- [ ] Collect user feedback
- [ ] Performance analysis
- [ ] Gradual rollout (start with power users)

---

## Next Steps

### Immediate Actions (Start Tomorrow)

1. **Create branch:**
   ```bash
   git checkout -b feature/agent-refactor
   ```

2. **Install dependencies:**
   ```bash
   pip install instructor>=0.4.0
   ```

3. **Create file structure:**
   ```bash
   mkdir -p src/desktop_pdf_translator/agent/tools
   touch src/desktop_pdf_translator/agent/__init__.py
   touch src/desktop_pdf_translator/agent/tools/__init__.py
   ```

4. **Start Phase 0:**
   - Create `base.py`
   - Create `models.py`
   - Create `prompts.py`

### Week 1: Phase 0-1
- Complete preparation
- Implement all 3 tools
- Unit test each tool

### Week 2: Phase 2-3
- Implement agent orchestrator
- Add decision logic
- Integration testing

### Week 3: Phase 4-5
- GUI integration
- Comprehensive testing
- Documentation and deployment

---

## Questions to Answer Before Starting

1. **LLM Model Choice:**
   - Use GPT-4o-mini for speed/cost? (Recommended)
   - Or GPT-4o for better reasoning?

2. **Agent Mode Default:**
   - Default to agent mode ON or OFF?
   - Provide manual override?

3. **Fallback Strategy:**
   - Always fallback to traditional on agent error?
   - Or fail fast and show error?

4. **Performance Target:**
   - What's acceptable overhead? (Current: no overhead, Agent: +1-2s)
   - Optimize for speed or accuracy?

5. **Testing Approach:**
   - A/B test with real users?
   - Or internal testing only first?

---

## Appendix: Code Migration Map

### Files to Create (New)
```
src/desktop_pdf_translator/agent/
├── __init__.py                    [NEW]
├── tools/
│   ├── __init__.py               [NEW]
│   ├── base.py                   [NEW]
│   ├── rag_tool.py              [NEW]
│   ├── web_search_tool.py       [NEW]
│   └── academic_search_tool.py  [NEW]
├── orchestrator.py               [NEW]
├── prompts.py                    [NEW]
├── models.py                     [NEW]
└── monitoring.py                 [NEW]

src/desktop_pdf_translator/rag/
└── agent_rag_chain.py            [NEW]

tests/agent/
├── __init__.py                   [NEW]
├── test_tools.py                [NEW]
├── test_orchestrator.py         [NEW]
└── conftest.py                  [NEW]
```

### Files to Modify (Existing)
```
src/desktop_pdf_translator/config/models.py         [MODIFY] Add AgentSettings
src/desktop_pdf_translator/gui/rag_chat_panel.py    [MODIFY] Add agent mode toggle
requirements.txt                                     [MODIFY] Add instructor
```

### Files to Keep Unchanged
```
src/desktop_pdf_translator/rag/rag_chain.py         [KEEP] Fallback system
src/desktop_pdf_translator/rag/web_research.py      [KEEP] Used by WebSearchTool
src/desktop_pdf_translator/rag/deep_search.py       [KEEP] Used by AcademicSearchTool
src/desktop_pdf_translator/rag/vector_store.py      [KEEP] Core infrastructure
```

---

## End of Refactoring Plan

**Last Updated:** 2025-12-22
**Status:** Ready for implementation
**Risk Level:** Medium (hybrid approach reduces risk)
**Expected Benefit:** High (better decisions, hybrid strategies, explainability)

**Questions or concerns?** Review this plan, adjust timelines, and begin Phase 0 when ready!
