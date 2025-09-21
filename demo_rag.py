#!/usr/bin/env python3
"""
Demo script for PDFusion RAG + Web Research system.
Demonstrates the capabilities of the enhanced Q&A system.
"""

import asyncio
import logging
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from desktop_pdf_translator.rag import (
        ScientificPDFProcessor,
        ChromaDBManager, 
        WebResearchEngine,
        EnhancedRAGChain
    )
    from desktop_pdf_translator.config import get_settings
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you have installed all dependencies:")
    print("pip install -r requirements.txt")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def demo_pdf_processing():
    """Demo PDF processing with scientific content extraction."""
    print("\n" + "="*60)
    print("🔬 DEMO: Scientific PDF Processing")
    print("="*60)
    
    processor = ScientificPDFProcessor()
    
    # This would process a real PDF file
    print("✓ PDF processor initialized")
    print("  - Supports equations, tables, figures")
    print("  - Preserves document layout")
    print("  - Extracts structured content")
    
    stats = processor.get_processing_stats()
    print(f"📊 Processing stats: {stats}")


async def demo_vector_store():
    """Demo vector store operations."""
    print("\n" + "="*60)
    print("🗄️ DEMO: Vector Store with ChromaDB")
    print("="*60)
    
    try:
        vector_store = ChromaDBManager()
        print("✓ ChromaDB initialized successfully")
        
        # Get collection stats
        stats = vector_store.get_collection_stats()
        print(f"📊 Collection stats:")
        for key, value in stats.items():
            print(f"  - {key}: {value}")
        
        # Demo search (would work with real data)
        print("\n🔍 Search capabilities:")
        print("  - Semantic similarity search")
        print("  - Hybrid search (semantic + keyword)")
        print("  - Metadata filtering")
        print("  - Cross-lingual search support")
        
    except Exception as e:
        print(f"❌ Vector store error: {e}")
        print("Make sure ChromaDB is properly installed")


async def demo_web_research():
    """Demo web research capabilities."""
    print("\n" + "="*60)
    print("🌐 DEMO: Web Research Engine")
    print("="*60)
    
    try:
        web_research = WebResearchEngine()
        print("✓ Web research engine initialized")
        
        print("\n🔍 Research capabilities:")
        print("  - Google Search integration")
        print("  - Google Scholar for academic papers")
        print("  - Wikipedia knowledge base")
        print("  - arXiv scientific papers")
        print("  - Content scraping and validation")
        print("  - Source reliability scoring")
        
        # Demo research (commented out to avoid actual web requests)
        # sources = await web_research.research_topic("quantum computing")
        # print(f"Found {len(sources)} sources")
        
    except Exception as e:
        print(f"❌ Web research error: {e}")


async def demo_rag_chain():
    """Demo complete RAG chain."""
    print("\n" + "="*60)
    print("🤖 DEMO: Enhanced RAG Chain")
    print("="*60)
    
    try:
        # Initialize components
        vector_store = ChromaDBManager()
        web_research = WebResearchEngine()
        rag_chain = EnhancedRAGChain(vector_store, web_research)
        
        print("✓ RAG chain initialized successfully")
        
        print("\n🎯 RAG capabilities:")
        print("  - Combines PDF knowledge with web research")
        print("  - Multilingual Q&A (Vietnamese priority)")
        print("  - Reference tracking with page navigation")
        print("  - Quality metrics and confidence scoring")
        print("  - Context-aware responses")
        
        # Demo question (would work with real documents)
        demo_questions = [
            "Tóm tắt nội dung chính của tài liệu",
            "Giải thích các khái niệm quan trọng",
            "So sánh với thông tin từ internet",
            "Tìm các nghiên cứu liên quan"
        ]
        
        print("\n❓ Example questions:")
        for i, question in enumerate(demo_questions, 1):
            print(f"  {i}. {question}")
        
    except Exception as e:
        print(f"❌ RAG chain error: {e}")


def demo_gui_features():
    """Demo GUI integration features."""
    print("\n" + "="*60)
    print("🖥️ DEMO: GUI Integration Features")
    print("="*60)
    
    print("✓ RAG Chat Panel features:")
    print("  - Interactive Q&A interface")
    print("  - Real-time web research toggle")
    print("  - Clickable references with page navigation")
    print("  - Chat history with context")
    print("  - Quick question templates")
    print("  - Progress tracking for long operations")
    
    print("\n✓ PDF Navigation:")
    print("  - Jump to specific pages from references")
    print("  - Highlight relevant sections")
    print("  - Support for equations, tables, figures")
    
    print("\n✓ Web Integration:")
    print("  - Open external links in browser")
    print("  - Source reliability indicators")
    print("  - Academic vs general web sources")


async def demo_vietnamese_features():
    """Demo Vietnamese language features."""
    print("\n" + "="*60)
    print("🇻🇳 DEMO: Vietnamese Language Features")
    print("="*60)
    
    print("✓ Vietnamese optimization:")
    print("  - Vietnamese text chunking and processing")
    print("  - Cross-lingual search (Vietnamese ↔ English)")
    print("  - Vietnamese font preservation")
    print("  - Proper sentence boundary detection")
    
    print("\n✓ Supported scenarios:")
    print("  - Hỏi bằng tiếng Việt, tìm trong PDF tiếng Anh")
    print("  - Trả lời song ngữ với trích dẫn")
    print("  - Tóm tắt tài liệu bằng tiếng Việt")
    print("  - Giải thích khái niệm khoa học bằng tiếng Việt")


def check_dependencies():
    """Check if all required dependencies are available."""
    print("\n" + "="*60)
    print("📦 DEPENDENCY CHECK")
    print("="*60)
    
    dependencies = {
        "Core RAG": [
            ("chromadb", "ChromaDB vector database"),
            ("sentence_transformers", "Sentence transformers for embeddings"),
            ("langchain", "LangChain framework")
        ],
        "PDF Processing": [
            ("PyMuPDF", "PyMuPDF (fitz) for PDF handling"),
            ("camelot", "Camelot for table extraction"),
            ("pdfplumber", "PDFPlumber for alternative processing")
        ],
        "Web Research": [
            ("googlesearch", "Google search integration"),
            ("bs4", "BeautifulSoup for web scraping"),
            ("requests", "HTTP requests library")
        ],
        "NLP & Scientific": [
            ("spacy", "spaCy for NLP processing"),
            ("sympy", "SymPy for mathematical expressions"),
            ("matplotlib", "Matplotlib for equation rendering")
        ]
    }
    
    all_good = True
    
    for category, deps in dependencies.items():
        print(f"\n📂 {category}:")
        for module_name, description in deps:
            try:
                __import__(module_name)
                print(f"  ✓ {description}")
            except ImportError:
                print(f"  ❌ {description} - Missing")
                all_good = False
    
    if all_good:
        print(f"\n🎉 All dependencies are installed!")
    else:
        print(f"\n⚠️  Some dependencies are missing. Install with:")
        print("pip install -r requirements.txt")
    
    return all_good


async def main():
    """Main demo function."""
    print("🚀 PDFusion RAG + Web Research Demo")
    print("Enhanced Q&A system for translated PDFs")
    
    # Check dependencies first
    deps_ok = check_dependencies()
    
    if not deps_ok:
        print("\n❌ Cannot run full demo due to missing dependencies")
        return
    
    # Run demos
    await demo_pdf_processing()
    await demo_vector_store()
    await demo_web_research()
    await demo_rag_chain()
    demo_gui_features()
    await demo_vietnamese_features()
    
    print("\n" + "="*60)
    print("✅ DEMO COMPLETED")
    print("="*60)
    print("\n📝 Next steps:")
    print("1. Install any missing dependencies")
    print("2. Set up API keys (OPENAI_API_KEY, GEMINI_API_KEY)")
    print("3. Run the main application: python main.py")
    print("4. Load a PDF and try the chat features!")
    
    print("\n💡 Tips:")
    print("- Use Vietnamese questions for best results")
    print("- Enable web research for comprehensive answers")
    print("- Click on references to navigate to PDF pages")
    print("- Try asking about scientific concepts or technical terms")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
