# Desktop PDF Translator

A Windows desktop application for translating PDF documents while preserving formatting, with support for Vietnamese language priority.

## Features

- **Multi-Panel Interface**: Original PDF, translated PDF, and future RAG chat integration
- **Translation Services**: OpenAI and Google Gemini support
- **Vietnamese Priority**: Optimized for Vietnamese translations with English, Japanese, and Chinese support
- **File Size Limits**: Supports PDFs up to 50 pages
- **Async Processing**: Non-blocking translation with progress tracking
- **Extensible Architecture**: Designed for future enhancements and RAG integration
- **BabelDOC Integration**: Advanced PDF layout preservation using BabelDOC

## Quick Start

### Development Mode
```bash
python main.py
```

### Installation (Future)
```bash
# Will be available as .exe installer
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

## Setup Instructions

### 1. Prerequisites
- Python 3.10 or higher
- Windows operating system (currently Windows-focused)
- API keys for translation services (OpenAI and/or Google Gemini)

### 2. Installation Steps

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd desktop_pdf_translator
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API keys**:
   You have two options to configure API keys:
   
   **Option A: Using .env file (Recommended for development)**
   ```bash
   # Copy the example file
   copy .env.example .env
   
   # Edit .env file and add your API keys
   notepad .env
   ```
   
   **Option B: Using environment variables**
   ```bash
   # Windows Command Prompt
   set OPENAI_API_KEY=your_openai_api_key_here
   set GEMINI_API_KEY=your_gemini_api_key_here
   
   # Windows PowerShell
   $env:OPENAI_API_KEY="your_openai_api_key_here"
   $env:GEMINI_API_KEY="your_gemini_api_key_here"
   ```
   
   **Option C: Using setup script**
   ```bash
   setup_api_keys.bat
   ```

4. **Run the application**:
   ```bash
   python main.py
   ```

### 3. Docker Setup (Alternative)

If you prefer to run the application in a Docker container:

1. **Install Docker Desktop**:
   - Download from https://www.docker.com/products/docker-desktop
   - Install and start Docker Desktop

2. **Build and run with Docker**:
   ```bash
   # Build the Docker image
   docker build -t desktop-pdf-translator .
   
   # Run the container
   docker run -it --rm \
     -e OPENAI_API_KEY=your_openai_api_key_here \
     -e GEMINI_API_KEY=your_gemini_api_key_here \
     desktop-pdf-translator
   ```

3. **Using Docker Compose** (Recommended):
   ```bash
   # Build and run with docker-compose
   docker-compose up --build
   ```

4. **Configuration with Docker**:
   Create a `.env` file with your API keys:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
   
   Then run:
   ```bash
   docker-compose --env-file .env up --build
   ```

5. **Important Notes for Docker**:
   - The application is a GUI desktop app, which requires special handling in Docker
   - For full GUI functionality, you may need to configure X11 forwarding on Linux
   - On Windows and macOS, Docker Desktop provides limited GUI support
   - API keys must be provided via environment variables
   - Logs are written to `/app/logs` inside the container

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

## Usage

1. Launch the application with `python main.py`
2. Click "Browse" or use "File" → "Open PDF" to select a PDF file
3. Choose source language (Auto-detect by default) and target language (Vietnamese by default)
4. Select translation service (OpenAI or Gemini)
5. Click "Translate" to start the translation process
6. View progress in the status bar and detailed progress panel
7. The translated PDF will appear in the right panel when complete
8. Translated files are saved in the `translated_pdfs` directory

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

## Troubleshooting

### Common Issues

1. **Missing Dependencies**:
   ```
   ImportError: No module named 'PySide6'
   ```
   Solution: Run `pip install -r requirements.txt`

2. **API Key Not Set**:
   ```
   Missing API key for openai
   ```
   Solution: Configure API keys using one of the methods in the Setup section

3. **BabelDOC Not Available**:
   ```
   BabelDOC not available - some features will be limited
   ```
   Solution: Install BabelDOC with `pip install babeldoc` (if available)

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