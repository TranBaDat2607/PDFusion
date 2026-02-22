# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDFusion is a Windows desktop application for translating PDF documents (defaulting to Vietnamese) while preserving layout/formatting. It uses BabelDOC as the translation pipeline engine and integrates an optional RAG (Retrieval-Augmented Generation) Q&A system for chatting with translated documents.

## Running the Application

```bash
# Activate conda environment first
conda activate pdffusion

# Run the app
python main.py
```

**External system dependencies required:**
- Ghostscript (for BabelDOC PDF processing)
- Tesseract OCR (optional, for scanned documents)

**Environment setup:**
```bash
conda create -n pdffusion python=3.11.14
conda activate pdffusion
pip install -r requirements.txt

# For RAG features:
pip install "pdfusion[rag]"

# For advanced PDF processing (OCR, tables):
pip install "pdfusion[advanced]"
```

**API key configuration** — create a `.env` file in the project root:
```
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

At least one translation service key is required. Optional env vars: `OPENAI_MODEL`, `GEMINI_MODEL`, `DEBUG_MODE`, `MAX_PAGES`, `MAX_FILE_SIZE_MB`.

## Architecture

### Module Layout (`src/desktop_pdf_translator/`)

| Module | Responsibility |
|---|---|
| `config/` | `ConfigManager` loads/saves TOML + env vars; `AppSettings` is the Pydantic model |
| `gui/` | PySide6 UI: `MainWindow`, `TranslationWorker`, `RAGChatPanel`, PDF viewers, dialogs |
| `processors/` | `PDFProcessor` — async generator pipeline wrapping BabelDOC |
| `translators/` | `BaseTranslator`, `OpenAITranslator`, `GeminiTranslator`, `TranslatorFactory` |
| `rag/` | ChromaDB vector store, `EnhancedRAGChain`, deep academic search, web research |
| `utils/` | API key encryption/decryption |

### Translation Flow

```
GUI (MainWindow)
  → TranslationWorker (QThread — bridges async to Qt signals)
    → PDFProcessor.process_pdf() (async generator)
      → validates file (PyMuPDF/fitz)
      → TranslatorFactory.create_translator() → OpenAITranslator / GeminiTranslator
      → babeldoc.async_translate() (BabelDOC pipeline)
      → yields ProgressEvent / ErrorEvent / CompletionEvent
  → emits Qt signals: progress_updated, translation_completed, translation_failed
Output: translated_pdfs/<name>.no_watermark.<lang>.mono.pdf
```

**Important:** `TranslationWorker` creates a fresh `asyncio` event loop per thread to run the async `PDFProcessor` from synchronous Qt threads.

### RAG Chat Flow

```
PDF loaded → RAGChatPanel.process_document()
  → DocumentProcessor chunks PDF (PyMuPDF)
  → ChromaDBManager stores embeddings (paraphrase-multilingual-MiniLM-L12-v2)

User query → EnhancedRAGChain.answer_question()
  → HyDE: generate hypothetical answer for better retrieval
  → Dual hybrid_search (original query + HyDE answer)
  → _add_surrounding_context (adjacent chunks)
  → _rerank_results (multi-signal scoring: semantic, keyword, page position, section type)
  → Web research via WebResearchEngine (optional)
  → LLM synthesis → Vietnamese answer
```

**Deep Search** (optional, for academic papers):
- Multi-hop citation graph traversal using Semantic Scholar, PubMed, CORE, arXiv APIs
- Papers cached in SQLite at `data/paper_cache/papers.db`
- Configurable in `config/default_config.toml` under `[deep_search]`

### Configuration System

- **Runtime config** stored at `~/AppData/Local/PDFusion/config.toml`
- **Default/example config** at `config/default_config.toml`
- API keys are **encrypted** (AES) before writing to disk; decrypted on load
- `.env` file (project root) is auto-loaded and overrides TOML config
- Singleton access: `get_config_manager()` / `get_settings()` from `config/__init__.py`

### GUI Layout

`MainWindow` has a three-panel horizontal `QSplitter`:
1. **Left**: Original PDF viewer (`PDFViewer`)
2. **Middle**: Translated PDF viewer (`PDFViewer`)
3. **Right**: RAG Chat panel (`RAGChatPanel`) — toggleable via toolbar "RAG: ON/OFF" button

Top toolbar contains: file browse, source/target language selectors, service/model selectors, API key input/validate, Translate/Cancel buttons, RAG toggle.

### Translator Pattern

All translators extend `BaseTranslator` and must implement `translate()` and `validate_configuration()`. They are registered in `TranslatorFactory._translators` dict. The translator instances are passed directly into BabelDOC's `TranslationConfig` — BabelDOC calls the translator internally during PDF processing.

## Key Files

- `main.py` — entry point; checks dependencies, loads config, starts GUI
- `src/desktop_pdf_translator/gui/main_window.py` — all main UI logic
- `src/desktop_pdf_translator/processors/processor.py` — BabelDOC integration
- `src/desktop_pdf_translator/rag/rag_chain.py` — RAG pipeline with HyDE + reranking
- `src/desktop_pdf_translator/rag/deep_search.py` — multi-hop academic search
- `src/desktop_pdf_translator/config/manager.py` — config load/save with encryption
- `config/default_config.toml` — reference for all available settings

## Logs

Application logs are written to `~/AppData/Local/PDFusion/logs/app.log`.
