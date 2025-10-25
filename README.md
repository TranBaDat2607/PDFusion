# PDFusion - Vietnamese-Optimized PDF Translator

PDFusion is a Windows desktop application designed for translating PDF documents while preserving formatting and layout. The application prioritizes Vietnamese language translation and integrates advanced AI-powered features including RAG (Retrieval-Augmented Generation) for intelligent document Q&A.

## Table of Contents

- [Installation](#installation)
- [Application Flow](#application-flow)
- [Main Features](#main-features)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Usage](#usage)
- [TO-DOs](#to-dos)

---

## Installation

### Prerequisites

- **Python Version**: 3.11.14 (recommended)
- **Operating System**: Windows 10/11
- **External Dependencies**:
  - Ghostscript (for PDF processing)
  - Tesseract OCR (optional, for scanned document processing)

### Step 1: Clone or Download the Project

```bash
cd /path/to/your/projects
git clone <repository-url>
cd PDFusion
```

### Step 2: Create Conda Environment

It is highly recommended to use a conda environment to avoid dependency conflicts.

```bash
conda create -n pdffusion python=3.11.14
```

Activate the conda environment:

```bash
conda activate pdffusion
```

### Step 3: Install Dependencies

Install all required packages using the requirements.txt file:

```bash
pip install -r requirements.txt
```

### Step 4: Configure API Keys

Create a `.env` file in the project root directory and add your API keys:

```bash
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Google Gemini Configuration
GEMINI_API_KEY=your_gemini_api_key_here
```

Note: You need at least one translation service API key (OpenAI or Google Gemini) for the application to function.

### Step 5: Run the Application

```bash
python main.py
```

The application will:
1. Check all dependencies
2. Validate API key configuration
3. Launch the GUI window

---

## Application Flow

### Overall Architecture

PDFusion follows a modular architecture with clear separation of concerns:

```
User Interface (PySide6 GUI)
    |
    v
Main Window Controller
    |
    +---> Translation Worker (QThread)
    |         |
    |         v
    |     PDF Processor
    |         |
    |         +---> File Validation
    |         +---> Translator Factory
    |         +---> BabelDOC Integration
    |         +---> Progress Events
    |
    +---> RAG Chat Panel (Optional)
              |
              v
          RAG Worker (QThread)
              |
              +---> Document Processor
              +---> Vector Store (ChromaDB)
              +---> Web Research Engine
              +---> RAG Chain
```

### Translation Workflow

1. **File Selection**: User selects a PDF file through the GUI
2. **File Validation**: System validates file format, size, and page count
3. **Service Configuration**: User selects translation service (OpenAI/Gemini), source/target languages
4. **Translation Initialization**: 
   - Create TranslationWorker thread
   - Initialize PDF Processor
   - Create appropriate Translator instance via Factory pattern
5. **BabelDOC Processing**:
   - Configure BabelDOC with layout preservation settings
   - Process PDF asynchronously
   - Stream progress events to GUI
6. **Result Generation**:
   - Generate monolingual translated PDF
   - Save to output directory
   - Display in GUI viewer
7. **Optional RAG Processing**: If RAG is enabled, index the document for Q&A

### RAG (Chat) Workflow

1. **Document Processing**:
   - Extract text and structure from PDF
   - Split into semantic chunks
   - Generate embeddings using sentence-transformers
   - Store in ChromaDB vector database
2. **Question Processing**:
   - User submits question
   - System searches PDF chunks using vector similarity
   - Optionally performs web research for supplementary information
3. **Answer Generation**:
   - Combine PDF context and web results
   - Generate comprehensive answer using LLM
   - Provide references with confidence scores
   - Display clickable references for navigation

### Event-Driven Progress Tracking

The application uses an event-driven architecture for progress tracking:

- **ProcessingEvent**: Base event class
- **ProgressEvent**: Updates on processing stages
- **CompletionEvent**: Final result with file paths
- **ErrorEvent**: Error information with recovery options

Events flow from worker threads to GUI via Qt signals/slots mechanism.

---

## Main Features

### Core Translation Features

1. **Multi-Service Translation Support**
   - OpenAI GPT-4.1 integration
   - Google Gemini 1.5 Flash integration
   - Easy service switching via GUI
   - API key validation

2. **Layout Preservation**
   - BabelDOC integration for accurate layout preservation
   - Maintains fonts, formatting, and structure
   - Supports complex PDF layouts
   - Configurable output modes

3. **Vietnamese Language Optimization**
   - Vietnamese set as default target language
   - Optimized prompts for Vietnamese translation quality
   - Vietnamese-specific text processing

4. **File Handling**
   - Support for PDF files up to 50 pages (configurable)
   - File size validation
   - Progress tracking during processing
   - Output directory management

### GUI Features

1. **Three-Panel Layout**
   - Left panel: Original PDF viewer
   - Middle panel: Translated PDF viewer
   - Right panel: RAG Chat interface

2. **Comprehensive Toolbar**
   - File browser
   - Language selection (source/target)
   - Service selection (OpenAI/Gemini)
   - Model selection
   - API key management
   - Quick validation

3. **Real-Time Progress Tracking**
   - Visual progress bar
   - Stage-by-stage status updates
   - Cancellation support
   - Processing time display

4. **PDF Viewer**
   - Zoom controls
   - Page navigation
   - Side-by-side comparison
   - Export capabilities

### RAG (AI Chat) Features

1. **Intelligent Q&A System**
   - Context-aware answers from PDF content
   - Multi-source information synthesis
   - Confidence scoring
   - Reference tracking

2. **Web Research Integration**
   - Optional web search for supplementary information
   - Source reliability scoring
   - Citation management
   - Toggle on/off functionality

3. **Vector Database**
   - ChromaDB for efficient semantic search
   - Persistent storage
   - Document chunking with metadata
   - Fast retrieval

4. **Interactive References**
   - Clickable PDF references (navigate to page)
   - Clickable web references (open in browser)
   - Confidence and reliability scores
   - Source preview

5. **Chat Management**
   - Conversation history
   - Clear history function
   - Question/answer threading
   - Timestamp tracking

### Advanced Features

1. **Configuration Management**
   - TOML-based configuration
   - Environment variable support
   - Encrypted API key storage
   - User preferences persistence

2. **Error Handling**
   - Graceful error recovery
   - User-friendly error messages
   - Detailed logging
   - Service validation

3. **Async Processing**
   - Non-blocking GUI
   - Background worker threads
   - Event-driven architecture
   - Cancellation support

4. **Extensibility**
   - Factory pattern for translators
   - Plugin-ready architecture
   - Modular component design
   - Easy service integration

## Configuration

### Default Configuration

The application uses TOML-based configuration with sensible defaults:

- **Translation Service**: OpenAI (fallback to Gemini)
- **Source Language**: Auto-detect
- **Target Language**: Vietnamese
- **Max Pages**: 50
- **Max File Size**: 100 MB
- **RAG**: Disabled by default

## Usage

### Basic Translation Workflow

1. Launch the application: `python main.py`
2. Click "Browse" to select a PDF file
3. Choose source language (or use Auto-detect)
4. Choose target language (default: Vietnamese)
5. Select translation service (OpenAI or Gemini)
6. Click "Translate" to start the process
7. Monitor progress in the status bar and progress panel
8. View the translated PDF in the middle panel when complete

### Using RAG Chat

1. Enable RAG by clicking the "RAG: OFF" button (turns to "RAG: ON")
2. Load or translate a PDF document
3. The document will be automatically processed for Q&A
4. Type your question in the input field at the bottom right
5. Toggle "Web Search" if you want additional web information
6. Click "Ask" or press Enter
7. View the answer with references in the chat panel
8. Click on references to navigate to PDF pages or web sources

### Cancelling Translation

- Click the "Cancel" button in the toolbar
- The translation will stop gracefully
- Partial results may be available

---

## TO-DOs