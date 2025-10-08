#!/usr/bin/env python3
"""
Dependency checker for PDFusion RAG system.
Checks all required packages and provides installation instructions.
"""

import sys
import importlib
from pathlib import Path

def check_package(package_name, description="", install_cmd=""):
    """Check if a package is available."""
    try:
        importlib.import_module(package_name)
        print(f"✅ {package_name:<25} - {description}")
        return True
    except ImportError:
        print(f"❌ {package_name:<25} - {description}")
        if install_cmd:
            print(f"   Install: {install_cmd}")
        return False

def main():
    """Main dependency check."""
    print("🔍 PDFusion RAG Dependencies Check")
    print("=" * 60)
    
    all_good = True
    
    # Core GUI dependencies
    print("\n📱 Core GUI Dependencies:")
    deps = [
        ("PySide6", "Qt GUI framework", "pip install PySide6"),
        ("pydantic", "Data validation", "pip install pydantic"),
        ("tomlkit", "TOML configuration", "pip install tomlkit"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # PDF processing
    print("\n📄 PDF Processing:")
    deps = [
        ("fitz", "PyMuPDF for PDF handling", "pip install PyMuPDF"),
        ("PIL", "Pillow for image processing", "pip install Pillow"),
        ("camelot", "Table extraction", "pip install camelot-py[cv]"),
        ("pdfplumber", "Alternative PDF processing", "pip install pdfplumber"),
        ("pdf2image", "PDF to image conversion", "pip install pdf2image"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # RAG Core
    print("\n🤖 RAG Core:")
    deps = [
        ("chromadb", "Vector database", "pip install chromadb"),
        ("sentence_transformers", "Embeddings", "pip install sentence-transformers"),
        ("langchain", "RAG framework", "pip install langchain"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # Web Research
    print("\n🌐 Web Research:")
    deps = [
        ("googlesearch", "Google search", "pip install googlesearch-python"),
        ("bs4", "Web scraping", "pip install beautifulsoup4"),
        ("requests", "HTTP requests", "pip install requests"),
        ("newspaper", "Article extraction", "pip install newspaper3k"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # NLP & Scientific
    print("\n🔬 NLP & Scientific:")
    deps = [
        ("spacy", "NLP processing", "pip install spacy"),
        ("underthesea", "Vietnamese NLP", "pip install underthesea"),
        ("sympy", "Symbolic math", "pip install sympy"),
        ("matplotlib", "Plotting", "pip install matplotlib"),
        ("pandas", "Data processing", "pip install pandas"),
        ("numpy", "Numerical computing", "pip install numpy"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # Translation services
    print("\n🔄 Translation Services:")
    deps = [
        ("openai", "OpenAI API", "pip install openai"),
        ("google.generativeai", "Google Gemini", "pip install google-generativeai"),
    ]
    
    for pkg, desc, cmd in deps:
        if not check_package(pkg, desc, cmd):
            all_good = False
    
    # Optional but recommended
    print("\n⭐ Optional (Recommended):")
    deps = [
        ("tiktoken", "Token counting", "pip install tiktoken"),
        ("faiss", "Fast similarity search", "pip install faiss-cpu"),
        ("rank_bm25", "BM25 search", "pip install rank-bm25"),
    ]
    
    for pkg, desc, cmd in deps:
        check_package(pkg, desc, cmd)  # Don't affect all_good status
    
    # Summary
    print("\n" + "=" * 60)
    if all_good:
        print("🎉 All required dependencies are installed!")
        print("\n✅ You can now run:")
        print("   python demo_rag.py    # Run demo")
        print("   python main.py        # Run main application")
    else:
        print("⚠️  Some required dependencies are missing.")
        print("\n📦 Quick install all dependencies:")
        print("   pip install -r requirements.txt")
        print("\n🔧 Or install missing packages individually using commands above.")
    
    # Check Python version
    print(f"\n🐍 Python version: {sys.version}")
    if sys.version_info < (3, 10):
        print("⚠️  Python 3.10+ recommended for best compatibility")
    
    # Check project structure
    print(f"\n📁 Project structure check:")
    required_dirs = [
        "src/desktop_pdf_translator/rag",
        "src/desktop_pdf_translator/gui",
        "src/desktop_pdf_translator/config",
        "src/desktop_pdf_translator/processors",
        "src/desktop_pdf_translator/translators",
    ]
    
    project_root = Path(__file__).parent
    for dir_path in required_dirs:
        full_path = project_root / dir_path
        if full_path.exists():
            print(f"✅ {dir_path}")
        else:
            print(f"❌ {dir_path} - Missing")
            all_good = False
    
    return 0 if all_good else 1

if __name__ == "__main__":
    sys.exit(main())
