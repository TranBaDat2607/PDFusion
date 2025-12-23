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
    "needs_web_search": true,
    "reasoning": "Brief explanation (1-2 sentences)",
    "confidence": 0.85
}}
"""


SYNTHESIS_PROMPT = """
You are synthesizing information from multiple sources to answer a question.

QUESTION: {question}

AVAILABLE SOURCES:

{sources_summary}

CRITICAL REQUIREMENTS:
1. Answer in English (clear, professional, academic style)
2. ONLY use information from the sources provided above
3. DO NOT make up or hallucinate sources that are not listed
4. Cite sources properly:
   - PDF sources: "According to the PDF (Page X)..."
   - Web sources: "According to [source type]..."
   - Academic papers: "Research by [authors] ([year])..."
5. If sources conflict, acknowledge different perspectives
6. Be comprehensive yet concise
7. If sources are insufficient, state what information is missing

Generate a well-structured answer with proper citations.
"""
