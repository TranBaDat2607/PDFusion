"""
LangGraph-based agent for intelligent RAG orchestration.
Clean, simple implementation using LangGraph's built-in features.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

from .tools import get_tools, initialize_tools
from .prompts import SYNTHESIS_PROMPT
from .monitoring import AgentMonitor

logger = logging.getLogger(__name__)


class LangGraphAgent:
    """
    LangGraph-based agent for RAG system.

    Much simpler than custom implementation:
    - Uses LangGraph's create_react_agent
    - Tools defined with @tool decorator
    - Automatic tool selection and execution
    - Built-in reasoning and planning
    """

    def __init__(
        self,
        rag_chain,
        web_research,
        deep_search,
        llm_client,
        model: str = "gpt-4o-mini",
        enable_monitoring: bool = True
    ):
        """
        Initialize LangGraph agent.

        Args:
            rag_chain: Existing EnhancedRAGChain
            web_research: Existing WebResearchEngine
            deep_search: Existing DeepSearchEngine
            llm_client: OpenAI client
            model: LLM model for agent (gpt-4o-mini recommended)
            enable_monitoring: Enable decision monitoring
        """
        self.model = model
        self.llm_client = llm_client

        # Initialize tools with existing components
        initialize_tools(rag_chain, web_research, deep_search)

        # Create LangChain LLM
        self.llm = ChatOpenAI(
            model=model,
            temperature=0.3,
            api_key=llm_client.api_key if hasattr(llm_client, 'api_key') else None
        )

        # System prompt for agent
        self.system_prompt = """You are an intelligent research assistant that helps answer questions using multiple tools.

You have access to 3 tools:

1. **rag_tool**: Search local PDF documents (FAST ~2s)
   - Use FIRST for any question
   - Best for document-specific questions

2. **web_search_tool**: Search web + Wikipedia (FAST ~5s)
   - Use when PDF doesn't have enough info
   - Best for general knowledge, definitions, context

3. **academic_search_tool**: Deep academic search (SLOW ~30s)
   - Use ONLY for research questions needing academic depth
   - WARNING: Very slow, use sparingly

STRATEGY:
1. ALWAYS start with rag_tool to search the PDF
2. Check if PDF results are sufficient (num_sources >= 3, avg_score >= 0.6)
3. If PDF insufficient:
   - For general questions: Add web_search_tool
   - For research questions: Add academic_search_tool
   - For complex questions: Use multiple tools
4. Synthesize answer from all sources

Be efficient - don't call tools unnecessarily!
"""

        # Create ReAct agent with tools
        tools = get_tools()
        self.agent = create_react_agent(
            model=self.llm,
            tools=tools,
            state_modifier=self.system_prompt
        )

        # Monitoring
        self.monitor = AgentMonitor() if enable_monitoring else None

        logger.info(f"[AGENT] LangGraph agent initialized (model: {model})")

    async def answer_question(
        self,
        question: str,
        document_id: Optional[str] = None,
        document_name: str = "Unknown",
        user_preferences: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Answer question using LangGraph agent.

        Args:
            question: User's question
            document_id: Current document ID
            document_name: Document name for context
            user_preferences: User preferences (web enabled, etc.)

        Returns:
            Answer with sources and agent reasoning
        """
        start_time = datetime.now()

        try:
            logger.info(f"[AGENT] Processing: {question[:100]}...")

            # Prepare context for agent
            context_msg = f"Current document: {document_name}"
            if document_id:
                context_msg += f"\nDocument ID: {document_id}"

            # Create messages
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=f"{context_msg}\n\nQuestion: {question}")
            ]

            # Invoke agent - LangGraph handles tool selection and execution!
            logger.info("[AGENT] Invoking LangGraph agent...")
            response = await self.agent.ainvoke({"messages": messages})

            # Extract final answer from agent messages
            final_message = response["messages"][-1]
            answer = final_message.content

            # Extract tool calls from agent's execution
            tools_used = []
            tool_results = {}

            for msg in response["messages"]:
                # Check if message is a tool call
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        tool_name = tool_call['name']
                        if tool_name not in tools_used:
                            tools_used.append(tool_name)

                # Check if message is a tool result
                if hasattr(msg, 'name') and msg.name:
                    tool_results[msg.name] = msg.content

            execution_time = (datetime.now() - start_time).total_seconds()

            logger.info(f"[AGENT] Completed in {execution_time:.2f}s")
            logger.info(f"[AGENT] Tools used: {tools_used}")

            # Format response for UI compatibility
            return self._format_response(
                answer=answer,
                tools_used=tools_used,
                tool_results=tool_results,
                question=question,
                execution_time=execution_time
            )

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"[AGENT] Execution failed: {e}", exc_info=True)

            return {
                'answer': f"Agent execution failed: {str(e)}",
                'pdf_references': [],
                'web_references': [],
                'quality_metrics': {'confidence': 0.0},
                'processing_time': execution_time,
                'sources_used': {'pdf_sources': 0, 'web_sources': 0},
                'search_type': 'agent_error',
                'error': str(e)
            }

    def _format_response(
        self,
        answer: str,
        tools_used: list,
        tool_results: dict,
        question: str,
        execution_time: float
    ) -> Dict[str, Any]:
        """
        Format LangGraph agent response for UI compatibility.

        Returns:
            Response dictionary matching existing system format
        """
        # Parse tool results to extract references
        pdf_references = []
        web_references = []

        # Extract RAG tool results
        if 'rag_tool' in tool_results:
            try:
                import json
                rag_data = json.loads(tool_results['rag_tool']) if isinstance(tool_results['rag_tool'], str) else tool_results['rag_tool']

                for source in rag_data.get('sources', []):
                    pdf_references.append({
                        'page': source.get('metadata', {}).get('page', 0),
                        'text': source.get('text', ''),
                        'confidence': source.get('rerank_score', 0.0),
                        'document_id': source.get('metadata', {}).get('document_id', ''),
                        'document_path': source.get('metadata', {}).get('document_path', ''),
                        'chunk_id': source.get('metadata', {}).get('chunk_id', '')
                    })
            except Exception as e:
                logger.warning(f"Failed to parse RAG results: {e}")

        # Extract web tool results
        if 'web_search_tool' in tool_results:
            try:
                import json
                web_data = json.loads(tool_results['web_search_tool']) if isinstance(tool_results['web_search_tool'], str) else tool_results['web_search_tool']

                for source in web_data.get('sources', []):
                    web_references.append({
                        'url': source.get('url', ''),
                        'title': source.get('title', ''),
                        'snippet': source.get('content', '')[:200],
                        'source_type': source.get('source_type', 'web'),
                        'reliability_score': source.get('reliability', 0.5)
                    })
            except Exception as e:
                logger.warning(f"Failed to parse web results: {e}")

        # Extract academic tool results
        if 'academic_search_tool' in tool_results:
            try:
                import json
                academic_data = json.loads(tool_results['academic_search_tool']) if isinstance(tool_results['academic_search_tool'], str) else tool_results['academic_search_tool']

                for paper in academic_data.get('papers', []):
                    web_references.append({
                        'url': '',  # Papers might not have URLs
                        'title': paper.get('title', ''),
                        'snippet': paper.get('abstract', ''),
                        'source_type': 'academic',
                        'source': paper.get('source', 'unknown'),
                        'authors': paper.get('authors', ''),
                        'year': paper.get('year', '')
                    })
            except Exception as e:
                logger.warning(f"Failed to parse academic results: {e}")

        return {
            'answer': answer,
            'pdf_references': pdf_references,
            'web_references': web_references,
            'quality_metrics': {
                'confidence': 0.85,  # LangGraph agent generally reliable
                'tools_used': tools_used
            },
            'processing_time': execution_time,
            'sources_used': {
                'pdf_sources': len(pdf_references),
                'web_sources': len(web_references)
            },
            'search_type': 'langgraph_agent',
            'web_search_used': 'web_search_tool' in tools_used or 'academic_search_tool' in tools_used,
            'timestamp': datetime.now().isoformat(),
            'agent_reasoning': {
                'tools_executed': tools_used,
                'framework': 'langgraph',
                'model': self.model
            }
        }
