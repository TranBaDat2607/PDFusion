#!/usr/bin/env python3
"""
Test script for PDFusion RAG pipeline.
Processes a PDF document and answers questions using the complete RAG system.
"""

import asyncio
import logging
import json
from pathlib import Path
from datetime import datetime
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from desktop_pdf_translator.rag import (
        ScientificPDFProcessor,
        ChromaDBManager, 
        WebResearchEngine,
        EnhancedRAGChain
    )
    from desktop_pdf_translator.config import get_settings
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you have installed all dependencies:")
    print("pip install -r requirements.txt")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


class RAGTester:
    """Test class for RAG pipeline operations."""
    
    def __init__(self):
        """Initialize RAG components."""
        self.pdf_processor = None
        self.vector_store = None
        self.web_research = None
        self.rag_chain = None
        self.document_id = None
        
    async def initialize_components(self):
        """Initialize all RAG components."""
        try:
            logger.info("Initializing RAG components...")
            
            # Initialize PDF processor
            self.pdf_processor = ScientificPDFProcessor()
            logger.info("PDF processor initialized successfully")
            
            # Initialize vector store
            self.vector_store = ChromaDBManager()
            logger.info("Vector store initialized successfully")
            
            # Initialize web research engine
            self.web_research = WebResearchEngine()
            logger.info("Web research engine initialized successfully")
            
            # Initialize RAG chain
            self.rag_chain = EnhancedRAGChain(self.vector_store, self.web_research)
            logger.info("RAG chain initialized successfully")
            
            logger.info("All RAG components initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            return False
    
    async def process_pdf_document(self, pdf_path: str):
        """
        Process PDF document and store in vector database.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Document ID for future reference
        """
        try:
            logger.info(f"Processing PDF document: {pdf_path}")
            
            # Check if file exists
            if not Path(pdf_path).exists():
                raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            
            # Process PDF document
            document_elements = await self.pdf_processor.process_document(pdf_path)
            logger.info(f"Extracted {len(document_elements)} document elements")
            
            # Store in vector database
            self.document_id = await self.vector_store.add_document(
                document_path=pdf_path,
                elements=document_elements
            )
            logger.info(f"Document stored in vector database with ID: {self.document_id}")
            
            # Get processing stats
            stats = self.pdf_processor.get_processing_stats()
            logger.info(f"Document processing statistics: {stats}")
            
            return {
                'document_id': self.document_id,
                'elements_count': len(document_elements),
                'processing_stats': stats,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"Failed to process PDF document: {e}")
            return {
                'document_id': None,
                'error': str(e),
                'status': 'failed'
            }
    
    async def answer_question(self, question: str, include_web_research: bool = True):
        """
        Answer question using RAG pipeline.
        
        Args:
            question: User's question
            include_web_research: Whether to include web research
            
        Returns:
            Complete answer with references and metrics
        """
        try:
            logger.info(f"Processing user question: {question}")
            
            if not self.document_id:
                logger.warning("No document loaded, using general knowledge only")
            
            # Answer question using RAG chain
            result = await self.rag_chain.answer_question(
                question=question,
                document_id=self.document_id,
                include_web_research=include_web_research,
                max_pdf_sources=5,
                max_web_sources=3
            )
            
            logger.info(f"Question answered successfully in {result.get('processing_time', 0):.2f} seconds")
            logger.info(f"Answer quality metrics: {result.get('quality_metrics', {})}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to answer question: {e}")
            return {
                'answer': f"Error answering question: {str(e)}",
                'error': str(e),
                'status': 'failed'
            }
    
    async def get_document_summary(self):
        """Get summary of processed document."""
        try:
            if not self.document_id:
                return {'summary': 'Không có tài liệu nào được xử lý'}
            
            summary = await self.rag_chain.summarize_document(self.document_id)
            return summary
            
        except Exception as e:
            logger.error(f"Failed to generate document summary: {e}")
            return {'summary': f'Error generating document summary: {str(e)}'}


async def run_rag_test(pdf_path: str, question: str, include_web_research: bool = True):
    """
    Run complete RAG test pipeline.
    
    Args:
        pdf_path: Path to PDF file
        question: Question to ask
        include_web_research: Whether to include web research
        
    Returns:
        Complete test results
    """
    start_time = datetime.now()
    
    # Initialize tester
    tester = RAGTester()
    
    # Initialize components
    init_success = await tester.initialize_components()
    if not init_success:
        return {
            'status': 'failed',
            'error': 'Failed to initialize RAG components',
            'timestamp': start_time.isoformat()
        }
    
    # Process PDF document
    logger.info("=" * 60)
    logger.info("STEP 1: Processing PDF Document")
    logger.info("=" * 60)
    
    pdf_result = await tester.process_pdf_document(pdf_path)
    
    # Get document summary
    logger.info("=" * 60)
    logger.info("STEP 2: Generating Document Summary")
    logger.info("=" * 60)
    
    document_summary = await tester.get_document_summary()
    
    # Answer question
    logger.info("=" * 60)
    logger.info("STEP 3: Answering Question")
    logger.info("=" * 60)
    
    qa_result = await tester.answer_question(question, include_web_research)
    
    # Compile final results
    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()
    
    final_result = {
        'test_info': {
            'pdf_path': pdf_path,
            'question': question,
            'include_web_research': include_web_research,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'total_processing_time': total_time
        },
        'pdf_processing': pdf_result,
        'document_summary': document_summary,
        'qa_result': qa_result,
        'status': 'completed'
    }
    
    logger.info("=" * 60)
    logger.info("RAG TEST COMPLETED")
    logger.info("=" * 60)
    logger.info(f"Total processing time: {total_time:.2f} seconds")
    
    return final_result


def save_results_to_json(results: dict, output_file: str = "result.json"):
    """Save test results to JSON file."""
    try:
        output_path = Path(output_file)
        
        # Create backup if file exists
        if output_path.exists():
            backup_path = output_path.with_suffix(f'.backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            output_path.rename(backup_path)
            logger.info(f"Created backup file: {backup_path}")
        
        # Save results
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Results saved to: {output_path.absolute()}")
        
    except Exception as e:
        logger.error(f"Failed to save results: {e}")


async def main():
    """Main function to run RAG test."""
    
    # ========================================
    # 🔧 CONFIGURATION VARIABLES
    # ========================================
    
    # TODO: Thay đổi đường dẫn PDF của bạn ở đây
    PDF_PATH = r"C:\Users\Admin\Downloads\CV_TranBaDat__Copy_.pdf"
    
    # TODO: Thay đổi câu hỏi của bạn ở đây
    QUESTION = "Tóm tắt nội dung chính của tài liệu này"
    
    # Có bao gồm web research không?
    INCLUDE_WEB_RESEARCH = True
    
    # File output cho kết quả
    OUTPUT_FILE = "result.json"
    
    # ========================================
    # 🚀 RUN TEST
    # ========================================
    
    print("PDFusion RAG Pipeline Test")
    print("=" * 50)
    print(f"PDF Path: {PDF_PATH}")
    print(f"Question: {QUESTION}")
    print(f"Web Research: {'Enabled' if INCLUDE_WEB_RESEARCH else 'Disabled'}")
    print(f"Output File: {OUTPUT_FILE}")
    print("=" * 50)
    
    # Validate PDF path
    if not Path(PDF_PATH).exists():
        print(f"ERROR: PDF file not found: {PDF_PATH}")
        print("Please update the PDF_PATH variable in the main() function")
        return
    
    try:
        # Run RAG test
        results = await run_rag_test(PDF_PATH, QUESTION, INCLUDE_WEB_RESEARCH)
        
        # Save results to JSON
        save_results_to_json(results, OUTPUT_FILE)
        
        # Print summary
        print("\n" + "=" * 50)
        print("TEST SUMMARY")
        print("=" * 50)
        
        if results.get('status') == 'completed':
            print("Status: SUCCESS")
            
            # PDF processing summary
            pdf_info = results.get('pdf_processing', {})
            if pdf_info.get('status') == 'success':
                print(f"Document processed: {pdf_info.get('elements_count', 0)} elements")
                print(f"Document ID: {pdf_info.get('document_id', 'N/A')}")
            
            # QA summary
            qa_info = results.get('qa_result', {})
            if 'quality_metrics' in qa_info:
                metrics = qa_info['quality_metrics']
                print(f"Confidence: {metrics.get('confidence', 0):.2%}")
                print(f"Completeness: {metrics.get('completeness', 0):.2%}")
            
            print(f"Total time: {results['test_info']['total_processing_time']:.2f} seconds")
            
        else:
            print("Status: FAILED")
            print(f"Error: {results.get('error', 'Unknown error')}")
        
        print(f"Results saved to: {OUTPUT_FILE}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        
        # Save error results
        error_results = {
            'status': 'failed',
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'test_info': {
                'pdf_path': PDF_PATH,
                'question': QUESTION
            }
        }
        save_results_to_json(error_results, OUTPUT_FILE)


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
