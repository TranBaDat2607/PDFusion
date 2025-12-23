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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'query': self.query,
            'query_type': self.query_type.value,
            'complexity': self.complexity,
            'requires_recent_info': self.requires_recent_info,
            'requires_academic_depth': self.requires_academic_depth,
            'estimated_pdf_coverage': self.estimated_pdf_coverage,
            'keywords': self.keywords,
            'reasoning': self.reasoning,
            'timestamp': self.timestamp.isoformat()
        }


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'strategy': self.strategy.value,
            'tools_to_use': self.tools_to_use,
            'tool_sequence': self.tool_sequence,
            'reasoning': self.reasoning,
            'estimated_time': self.estimated_time,
            'confidence': self.confidence,
            'rag_params': self.rag_params,
            'web_search_params': self.web_search_params,
            'academic_search_params': self.academic_search_params
        }


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
            'query_complexity': self.analysis.complexity,
            'strategy': self.plan.strategy.value,
            'tools_executed': self.tools_executed,
            'execution_time': self.total_time,
            'estimated_time': self.plan.estimated_time,
            'reasoning': self.plan.reasoning,
            'confidence': self.plan.confidence,
            'analysis': self.analysis.to_dict(),
            'plan': self.plan.to_dict()
        }
