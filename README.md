# PDFusion - Vietnamese-Optimized PDF Translator

PDFusion is a Windows desktop application designed for translating PDF documents while preserving formatting and layout. The application prioritizes Vietnamese language translation and integrates advanced AI-powered features including RAG (Retrieval-Augmented Generation) for intelligent document Q&A.

## Table of Contents

- [Installation](#installation)

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