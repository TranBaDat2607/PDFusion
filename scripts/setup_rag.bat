@echo off
echo ========================================
echo PDFusion RAG Setup Script
echo ========================================
echo.

echo 📦 Installing core dependencies...
pip install PySide6 pydantic tomlkit python-dotenv

echo.
echo 📄 Installing PDF processing dependencies...
pip install PyMuPDF Pillow pdf2image
pip install camelot-py pdfplumber

echo.
echo 🤖 Installing RAG core dependencies...
pip install chromadb sentence-transformers langchain

echo.
echo 🌐 Installing web research dependencies...
pip install googlesearch-python beautifulsoup4 requests newspaper3k

echo.
echo 🔬 Installing NLP and scientific dependencies...
pip install spacy underthesea sympy matplotlib pandas numpy

echo.
echo 🔄 Installing translation services...
pip install openai google-generativeai

echo.
echo ⭐ Installing optional dependencies...
pip install tiktoken faiss-cpu rank-bm25

echo.
echo ✅ Installation completed!
echo.
echo 🔍 Checking dependencies...
python check_dependencies.py

echo.
echo 🚀 Setup completed! You can now run:
echo    python demo_rag.py    # Run demo
echo    python main.py        # Run main application
echo.
pause
