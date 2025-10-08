# 📦 Installation Guide

This guide provides detailed installation instructions for PDFusion.

## 🚀 Quick Installation

### Method 1: Automatic Installation (Recommended)
```bash
# Clone and install
git clone <repository-url>
cd PDFusion
scripts\install_dependencies.bat
```

### Method 2: Using pip
```bash
# Basic installation
pip install -e .

# With RAG features
pip install -e .[rag]

# With all features
pip install -e .[all]

# Development setup
pip install -e .[dev]
```

## 🔧 Manual Installation

### Prerequisites
- **Python 3.11+** (Recommended) or Python 3.10+
- **Windows 10/11** (Primary support)
- **Git** for cloning the repository

### Step-by-Step Installation

1. **Create Python environment**
   ```bash
   conda create -n pdfusion-env python=3.11
   conda activate pdfusion-env
   ```

2. **Clone repository**
   ```bash
   git clone <repository-url>
   cd PDFusion
   ```

3. **Install dependencies**
   ```bash
   pip install -e .
   ```

4. **Configure API keys**
   ```bash
   copy .env.example .env
   # Edit .env file with your API keys
   ```

5. **Run the application**
   ```bash
   python main.py
   ```

## 🛠️ Troubleshooting

### Common Issues

#### Dependency Conflicts
If you encounter dependency resolution errors:
```bash
python scripts/install_dependencies.py
```

#### Missing API Keys
```bash
# Set environment variables
set OPENAI_API_KEY=your_key_here
set GEMINI_API_KEY=your_key_here
```

#### Import Errors
Make sure you're in the correct environment:
```bash
conda activate pdfusion-env
python -c "import desktop_pdf_translator; print('OK')"
```

## 📋 Environment Variables

Required API keys:
- `OPENAI_API_KEY`: OpenAI API key for translation
- `GEMINI_API_KEY`: Google Gemini API key for translation

Optional settings:
- `DEBUG_MODE`: Enable debug logging (default: false)
- `MAX_PAGES`: Maximum pages per PDF (default: 50)
- `MAX_FILE_SIZE_MB`: Maximum file size (default: 50)

## 🔍 Verification

Test your installation:
```bash
# Check dependencies
python scripts/check_dependencies.py

# Run demo
python scripts/demo_rag.py

# Launch application
python main.py
```
