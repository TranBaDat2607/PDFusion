# 🛠️ Scripts Directory

This directory contains utility scripts for PDFusion installation, setup, and maintenance.

## 📋 Available Scripts

### Installation Scripts
- **`install_dependencies.bat`** - Automated dependency installation for Windows
- **`install_dependencies.py`** - Python-based dependency installer with conflict resolution
- **`check_dependencies.py`** - Verify installation and check for missing dependencies

### Setup Scripts
- **`setup_rag.bat`** - Setup RAG system with ChromaDB and embeddings
- **`demo_rag.py`** - Demo script to test RAG functionality

## 🚀 Usage

### Quick Installation
```bash
# Run the main installer
scripts\install_dependencies.bat
```

### Manual Dependency Check
```bash
# Check what's installed
python scripts\check_dependencies.py
```

### RAG System Setup
```bash
# Setup RAG features
scripts\setup_rag.bat
```

### Test RAG System
```bash
# Test RAG functionality
python scripts\demo_rag.py
```

## 🔧 Script Details

### install_dependencies.bat
- Creates conda environment
- Installs core dependencies
- Handles common installation issues
- Sets up environment variables

### install_dependencies.py
- Advanced dependency resolution
- Handles version conflicts
- Provides detailed error messages
- Supports partial installation

### check_dependencies.py
- Validates all required packages
- Reports version mismatches
- Suggests fixes for issues
- Tests import capabilities

### setup_rag.bat
- Installs RAG-specific dependencies
- Downloads embedding models
- Initializes ChromaDB
- Tests web research capabilities

### demo_rag.py
- Demonstrates RAG functionality
- Tests document processing
- Shows Q&A capabilities
- Validates web research integration
