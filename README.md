# PDFusion - Desktop PDF Translator

A Windows desktop application for translating PDF documents while preserving formatting, with Vietnamese language priority and RAG-powered Q&A capabilities.

## Features

- **Multi-Panel Interface**: Original PDF, translated PDF, and RAG chat panel
- **Translation Services**: OpenAI GPT-4 and Google Gemini support
- **Vietnamese Priority**: Optimized for Vietnamese translations with multi-language support
- **Advanced PDF Processing**: BabelDOC integration for perfect layout preservation
- **RAG Q&A System**: Ask questions about your translated documents
- **Web Research**: Integrated academic search (Google Scholar, arXiv)
- **Async Processing**: Non-blocking translation with real-time progress
- **File Limits**: Supports PDFs up to 50 pages
- **Extensible Architecture**: Modular design for future enhancements

## Quick Start

### Automatic Installation (Recommended)
```bash
# Run the automated installer
install_dependencies.bat
```

### Manual Installation
```bash
# Create environment and install dependencies
conda create -n pdfusion-env python=3.11
conda activate pdfusion-env
python main.py
```

## Project Structure

```
desktop_pdf_translator/
├── config/
│   └── default_config.toml
├── resources/
│   └── README.md
├── src/
│   └── desktop_pdf_translator/
│       ├── config/
│       │   ├── __init__.py
│       │   ├── manager.py
│       │   └── models.py
│       ├── gui/
│       │   ├── __init__.py
│       │   ├── main_window.py
│       │   ├── widgets.py
│       │   └── worker.py
│       ├── processors/
│       │   ├── __init__.py
│       │   ├── events.py
│       │   ├── exceptions.py
│       │   └── processor.py
│       ├── translators/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── factory.py
│       │   ├── gemini_translator.py
│       │   └── openai_translator.py
│       ├── utils/
│       │   └── __init__.py
│       └── __init__.py
├── tests/
│   └── __init__.py
├── main.py
├── pyproject.toml
├── requirements.txt
├── setup_api_keys.bat
└── setup_dev.bat
```

## 📋 Installation Guide

### Prerequisites
- **Python 3.11+** (Recommended) or Python 3.10+
- **Windows 10/11** (Primary support)
- **Anaconda/Miniconda** (Recommended for environment management)
- **API Keys** for translation services (OpenAI and/or Google Gemini)

### 🚀 Method 1: Automatic Installation (Recommended)

**Step 1**: Clone the repository
```bash
git clone <repository-url>
cd PDFusion
```

**Step 2**: Run the automated installer
```bash
# This will create environment and install all dependencies
install_dependencies.bat
```

**Step 3**: Configure API keys
```bash
# Copy and edit the environment file
copy .env.example .env
notepad .env
```

**Step 4**: Run the application
```bash
conda activate pdfusion-env
python main.py
```

### 🔧 Method 2: Manual Installation

**Step 1**: Create Python environment
```bash
conda create -n pdfusion-env python=3.11
conda activate pdfusion-env
```

**Step 2**: Install dependencies in stages (to avoid conflicts)
```bash
# Core dependencies
pip install -r requirements_core.txt

# Scientific computing
pip install -r requirements_scientific.txt

# AI/ML libraries
pip install -r requirements_ai.txt

# RAG and web research (optional)
pip install -r requirements_rag.txt
```

**Step 3**: Configure API keys and run
```bash
copy .env.example .env
notepad .env
python main.py
```

### 🛠️ Method 3: Troubleshooting Installation

If you encounter dependency conflicts, use the Python installer:
```bash
conda create -n pdfusion-env python=3.11
conda activate pdfusion-env
python install_dependencies.py
```

This script will:
- ✅ Install dependencies in optimal order
- ✅ Handle conflicts automatically  
- ✅ Provide detailed error messages
- ✅ Allow partial installation if needed

## Configuration

### Default Configuration
The application uses a default configuration file located at `config/default_config.toml` with Vietnamese as the default target language.

### User Configuration
User-specific settings are stored in:
- Windows: `%LOCALAPPDATA%\DesktopPDFTranslator\config.toml`

### Environment Variables
- `OPENAI_API_KEY`: OpenAI API key for OpenAI translation service
- `GEMINI_API_KEY`: Google Gemini API key for Gemini translation service
- `OPENAI_MODEL`: (Optional) Specify OpenAI model (default: gpt-4)
- `GEMINI_MODEL`: (Optional) Specify Gemini model (default: gemini-pro)
- `DEBUG_MODE`: (Optional) Enable debug mode (default: false)
- `MAX_PAGES`: (Optional) Maximum pages to process (default: 50)
- `MAX_FILE_SIZE_MB`: (Optional) Maximum file size in MB (default: 50)

## 📖 Usage Guide

### Basic PDF Translation

1. **Launch the application**:
   ```bash
   conda activate pdfusion-env
   python main.py
   ```

2. **Load PDF**: Click "Browse" or use "File" → "Open PDF" to select a PDF file

3. **Configure Translation**:
   - Source language (Auto-detect by default)
   - Target language (Vietnamese by default)
   - Translation service (OpenAI or Gemini)

4. **Start Translation**: Click "Translate" to begin processing

5. **Monitor Progress**: View real-time progress in the status bar and progress panel

6. **View Results**: The translated PDF appears in the right panel when complete

7. **Save Output**: Translated files are automatically saved in the `translated_pdfs` directory

### RAG Q&A System

1. **After translation**, use the RAG chat panel (right side) to ask questions about your document

2. **Ask Questions**: Type questions like:
   - "What is the main topic of this document?"
   - "Summarize the key findings"
   - "What are the conclusions?"

3. **Web Research**: Enable web research for enhanced answers with academic sources

4. **Reference Navigation**: Click on references to jump to specific pages in the PDF

## Translation Services

### OpenAI
- Requires `OPENAI_API_KEY` environment variable
- Uses GPT-4 model by default (configurable)
- Supports advanced translation features

### Google Gemini
- Requires `GEMINI_API_KEY` environment variable
- Uses gemini-pro model by default (configurable)
- Alternative translation service with competitive quality

## Development

### Project Structure
```
src/desktop_pdf_translator/
├── config/
│   ├── __init__.py
│   ├── manager.py
│   └── models.py
├── gui/
│   ├── __init__.py
│   ├── main_window.py
│   ├── widgets.py
│   └── worker.py
├── processors/
│   ├── __init__.py
│   ├── events.py
│   ├── exceptions.py
│   └── processor.py
├── translators/
│   ├── __init__.py
│   ├── base.py
│   ├── factory.py
│   ├── gemini_translator.py
│   └── openai_translator.py
├── utils/
│   └── __init__.py
└── __init__.py
```

### Key Components
- **Main Window**: `src/gui/main_window.py` - Main application window with three-panel layout
- **PDF Processor**: `src/processors/processor.py` - Core PDF processing pipeline with BabelDOC integration
- **Translators**: `src/translators/` - Adapter classes for different translation services
- **Configuration**: `src/config/` - Settings management with TOML files and environment variables

### Running Tests
```bash
# Run tests (when implemented)
python -m pytest tests/
```

## 🔧 Troubleshooting

### Common Installation Issues

1. **Dependency Resolution Error**:
   ```
   ERROR: resolution-too-deep
   ```
   **Solution**: Use staged installation
   ```bash
   python install_dependencies.py
   ```

2. **QAction Import Error**:
   ```
   cannot import name 'QAction' from 'PySide6.QtWidgets'
   ```
   **Solution**: This is fixed in the current version. Reinstall PySide6:
   ```bash
   pip install --force-reinstall PySide6>=6.7.0
   ```

3. **BabelDOC Conflicts**:
   ```
   Cannot install babeldoc and numpy<2.0.0
   ```
   **Solution**: Use the optimized requirements files that specify numpy>=2.0.2

4. **Missing Dependencies**:
   ```
   ImportError: No module named 'PySide6'
   ```
   **Solution**: Install core dependencies first:
   ```bash
   pip install -r requirements_core.txt
   ```

5. **API Key Not Set**:
   ```
   Missing API key for openai
   ```
   **Solution**: Configure API keys in `.env` file:
   ```bash
   copy .env.example .env
   # Edit .env and add your keys
   ```

### Installation Order Issues

If you get conflicts, install in this exact order:
```bash
pip install numpy>=2.0.2
pip install typing-extensions>=4.5.0
pip install pydantic>=2.5.0 pydantic-settings>=2.1.0
pip install tenacity>=9.0.0
pip install PyMuPDF>=1.25.1
pip install babeldoc>=0.4.11
pip install -r requirements_core.txt
```

### Logs
Application logs are stored in:
- Windows: `%LOCALAPPDATA%\DesktopPDFTranslator\logs\app.log`

## Future Enhancements

- Cross-platform support (macOS, Linux)
- RAG-based PDF chat system integration
- Additional translation services
- Advanced PDF editing features
- Batch processing capabilities
- Custom glossary support

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- BabelDOC for advanced PDF processing capabilities
- PySide6 for the GUI framework
- OpenAI and Google for translation APIs
- PyMuPDF (fitz) for PDF handling