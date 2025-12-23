"""
Agent-enhanced RAG chain using LangGraph.
Agent-only mode - no fallback to traditional.
"""

import logging
from typing import Dict, Any, Optional

from .rag_chain import EnhancedRAGChain
from ..agent.agent import LangGraphAgent

logger = logging.getLogger(__name__)


class AgentRAGChain(EnhancedRAGChain):
    """
    RAG chain with LangGraph agent orchestration.
    Agent-only mode - uses LangGraph for all decisions.

    The agent uses 3 tools:
    1. rag_tool - PDF/local document search (fast)
    2. web_search_tool - Web + Wikipedia (fast)
    3. academic_search_tool - Deep academic papers (slow)
    """

    def __init__(
        self,
        vector_store,
        web_research,
        agent_mode='always',
        llm_model='gpt-4o-mini'
    ):
        """
        Initialize agent-enhanced RAG chain.

        Args:
            vector_store: ChromaDB manager
            web_research: Web research engine
            agent_mode: 'always' (agent required) or 'never' (traditional only)
            llm_model: Model for agent (gpt-4o-mini for speed/cost)
        """
        # Initialize parent (for core retrieval methods)
        super().__init__(vector_store, web_research)

        self.agent_mode = agent_mode
        self.llm_model = llm_model
        self.agent = None

        # Initialize LangGraph agent
        if agent_mode == 'always':
            logger.info("[AGENT_RAG] Initializing LangGraph agent (agent-only mode)...")

            # Get LLM client from translator
            if not hasattr(self, 'translator') or not self.translator:
                raise ValueError("Translator not initialized - required for agent")

            if not hasattr(self.translator, 'client'):
                raise ValueError("Translator has no LLM client - required for agent")

            llm_client = self.translator.client
            logger.info("[AGENT_RAG] Using translator's OpenAI client")

            # Create LangGraph agent
            self.agent = LangGraphAgent(
                rag_chain=self,
                web_research=web_research,
                deep_search=self.deep_search_engine,
                llm_client=llm_client,
                model=llm_model,
                enable_monitoring=True
            )

            logger.info(f"[AGENT_RAG] LangGraph agent initialized (model: {llm_model})")
        else:
            logger.info("[AGENT_RAG] Agent mode: never - using traditional only")

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
        Answer question using LangGraph agent.

        Args:
            question: User's question
            document_id: Specific document to search
            include_web_research: Allow web search (passed to agent as preference)
            max_pdf_sources: Max PDF sources (not used by agent)
            max_web_sources: Max web sources (not used by agent)
            use_deep_search: Allow deep search (passed to agent as preference)
            progress_callback: Progress callback (not used by agent)

        Returns:
            Answer with sources
        """
        if self.agent_mode == 'always':
            # Agent-only mode
            logger.info("═══ USING LANGGRAPH AGENT ═══")

            document_name = self._get_document_name(document_id)

            result = await self.agent.answer_question(
                question=question,
                document_id=document_id,
                document_name=document_name,
                user_preferences={
                    'include_web_research': include_web_research,
                    'use_deep_search': use_deep_search
                }
            )

            logger.info("═══ LANGGRAPH AGENT COMPLETE ═══")
            return result
        else:
            # Traditional mode
            logger.info("═══ USING TRADITIONAL RAG ═══")
            return await super().answer_question(
                question=question,
                document_id=document_id,
                include_web_research=include_web_research,
                max_pdf_sources=max_pdf_sources,
                max_web_sources=max_web_sources,
                use_deep_search=use_deep_search,
                progress_callback=progress_callback
            )

    def _get_document_name(self, document_id: Optional[str]) -> str:
        """Get document name from ID."""
        if not document_id:
            return "Unknown Document"
        return document_id.replace('_', ' ').title()

    def set_agent_mode(self, mode: str):
        """
        Set agent mode.

        Args:
            mode: 'always' or 'never'
        """
        self.agent_mode = 'always' if mode == 'always' else 'never'
        logger.info(f"[AGENT_RAG] Mode: {self.agent_mode}")

    def enable_agent(self):
        """Enable agent mode."""
        self.agent_mode = 'always'

    def disable_agent(self):
        """Disable agent mode."""
        self.agent_mode = 'never'

    def get_agent_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        if not self.agent or not self.agent.monitor:
            return {'agent_enabled': False}

        stats = self.agent.monitor.get_decision_stats(last_n=100)
        stats['agent_enabled'] = True
        stats['mode'] = self.agent_mode
        stats['model'] = self.llm_model

        return stats
