# 🚀 PDFusion - Intelligent PDF Translator

A professional Windows desktop application for translating PDF documents while preserving formatting, featuring Vietnamese language optimization and AI-powered Q&A capabilities.

## ✨ Key Features

### 📄 **Advanced PDF Translation**
- **Layout Preservation**: Maintains original document formatting using BabelDOC
- **Multi-language Support**: Optimized for Vietnamese with 50+ language support
- **Smart Processing**: Handles text, equations, tables, and figures
- **Batch Processing**: Up to 50 pages per document

### 🤖 **AI-Powered Translation**
- **OpenAI GPT-4**: Premium translation quality
- **Google Gemini**: Alternative high-quality service
- **Context-Aware**: Maintains document context and terminology
- **Async Processing**: Non-blocking translation with real-time progress

### 🧠 **RAG Q&A System**
- **Document Intelligence**: Ask questions about translated content
- **Web Research**: Integrated academic search (Google Scholar, arXiv, Wikipedia)
- **Cross-lingual**: Ask in Vietnamese, search in English
- **Reference Navigation**: Click citations to jump to specific pages

### 🎨 **Professional Interface**
- **Multi-Panel Layout**: Original PDF, translated PDF, and chat panel
- **Modern UI**: Built with PySide6 for native Windows experience
- **Progress Tracking**: Real-time translation progress and status
- **Extensible Design**: Modular architecture for future enhancements

## 🚀 Quick Start

### Method 1: Automatic Installation (Recommended)
```bash
# Clone and install
git clone <repository-url>
cd PDFusion
scripts\install_dependencies.bat
```

### Method 2: Manual Installation
```bash
# Create environment
conda create -n pdfusion-env python=3.11
conda activate pdfusion-env
pip install -e .
```

### Method 3: Development Setup
```bash
# For developers
pip install -e .[dev]
```

### Method 4: Feature-specific Installation
```bash
# Basic translation only
pip install -e .

# With RAG features
pip install -e .[rag]

# With advanced PDF processing
pip install -e .[advanced]

# All features
pip install -e .[all]
```

## 📁 Project Structure

```
PDFusion/
├── 📄 pyproject.toml          # Modern Python project configuration
├── 📄 README.md               # This file
├── 📄 .env.example            # Environment template
├── 📄 main.py                 # Application entry point
├── 📂 src/                    # Source code
│   └── desktop_pdf_translator/
│       ├── 📂 config/         # Configuration management
│       ├── 📂 gui/            # User interface components
│       ├── 📂 processors/     # PDF processing pipeline
│       ├── 📂 translators/    # Translation service adapters
│       ├── 📂 rag/            # RAG and Q&A system
│       └── 📂 utils/          # Utility functions
├── 📂 config/                 # Configuration files
├── 📂 docs/                   # Documentation
├── 📂 tests/                  # Test suites
├── 📂 scripts/                # Installation and setup scripts
└── 📂 resources/              # Static resources
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

### 🧠 RAG Q&A System

The RAG (Retrieval-Augmented Generation) system combines knowledge from your translated PDF with web research to provide comprehensive answers.

#### **How to Use:**

1. **After translation**, use the RAG chat panel (right side)
2. **Ask questions** in Vietnamese or English:
   ```
   🔬 Scientific/Technical:
   - "Giải thích thuật toán này hoạt động như thế nào?"
   - "So sánh phương pháp này với các nghiên cứu khác"
   - "Ứng dụng thực tế của công nghệ này là gì?"
   
   📊 Data Analysis:
   - "Tóm tắt kết quả thí nghiệm trong bảng 3"
   - "Ý nghĩa của biểu đồ ở trang 15 là gì?"
   - "Mối quan hệ giữa các biến số được trình bày như thế nào?"
   
   🌐 Extended Research:
   - "Tìm thêm thông tin về chủ đề này trên internet"
   - "Có nghiên cứu nào mới hơn về vấn đề này không?"
   - "So sánh với tiêu chuẩn quốc tế hiện tại"
   ```

3. **Enable Web Research** for enhanced answers with academic sources
4. **Navigate References**: Click citations to jump to specific pages or open web links

#### **Features:**
- **Cross-lingual**: Ask in Vietnamese, search in English documents
- **Multi-source**: Combines PDF content with Google Scholar, arXiv, Wikipedia
- **Smart Citations**: Accurate references with page navigation
- **Quality Metrics**: Confidence and completeness scores

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
   **Solution**: Use the Python installer
   ```bash
   python scripts/install_dependencies.py
   ```

2. **Missing Dependencies**:
   ```
   ImportError: No module named 'desktop_pdf_translator'
   ```
   **Solution**: Install in development mode
   ```bash
   pip install -e .
   ```

3. **API Key Not Set**:
   ```
   Missing API key for translation service
   ```
   **Solution**: Configure API keys in `.env` file:
   ```bash
   copy .env.example .env
   # Edit .env and add your keys
   ```

4. **ChromaDB Issues** (RAG features):
   ```
   ChromaDB initialization failed
   ```
   **Solution**: Install RAG dependencies
   ```bash
   pip install -e .[rag]
   ```

5. **BabelDOC Conflicts**:
   ```
   Cannot install babeldoc
   ```
   **Solution**: Use modern numpy version
   ```bash
   pip install numpy>=2.0.2
   pip install -e .
   ```

### Quick Fixes

**Check Installation**:
```bash
python scripts/check_dependencies.py
```

**Reset Environment**:
```bash
conda deactivate
conda remove -n pdfusion-env --all
conda create -n pdfusion-env python=3.11
conda activate pdfusion-env
pip install -e .
```

**Test Installation**:
```bash
python -c "import desktop_pdf_translator; print('✅ Installation OK')"
```

### Logs and Support
- **Logs**: `%LOCALAPPDATA%\DesktopPDFTranslator\logs\app.log`
- **Issues**: Create GitHub issue with log files
- **Documentation**: See `docs/` directory for detailed guides

## 🔮 Future Enhancements

- **Cross-platform Support**: macOS and Linux compatibility
- **Advanced PDF Editing**: Enhanced document manipulation
- **Batch Processing**: Multiple PDF processing capabilities
- **Custom Glossaries**: User-defined translation dictionaries
- **Plugin System**: Third-party extension support
- **API Server**: RESTful API for integration
- **Mobile App**: Companion mobile application

## 📚 Documentation

- **[Installation Guide](docs/INSTALLATION.md)**: Detailed setup instructions
- **[Architecture](docs/ARCHITECTURE.md)**: Technical system overview
- **[RAG System](docs/RAG_README.md)**: AI-powered Q&A documentation
- **[Changelog](CHANGELOG.md)**: Version history and updates

## 🤝 Contributing

We welcome contributions! Please feel free to submit issues, feature requests, or pull requests.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **BabelDOC**: Advanced PDF processing and layout preservation
- **PySide6**: Modern cross-platform GUI framework
- **OpenAI & Google**: AI translation services
- **ChromaDB**: Vector database for RAG functionality
- **LangChain**: RAG framework and AI orchestration
- **PyMuPDF**: Comprehensive PDF handling library