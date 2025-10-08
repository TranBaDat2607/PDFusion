#!/usr/bin/env python3
"""
Test script for PDFusion Document Processing only.
Processes a PDF document and extracts structured elements ready for vector store.
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
    from desktop_pdf_translator.rag import ScientificPDFProcessor
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


class DocumentProcessingTester:
    """Test class for document processing operations."""
    
    def __init__(self):
        """Initialize document processor."""
        self.pdf_processor = None
        
    async def initialize_processor(self):
        """Initialize PDF processor."""
        try:
            logger.info("Initializing PDF processor...")
            self.pdf_processor = ScientificPDFProcessor()
            logger.info("PDF processor initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize PDF processor: {e}")
            return False
    
    async def process_pdf_document(self, pdf_path: str):
        """
        Process PDF document and extract structured elements.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Structured document elements ready for vector store
        """
        try:
            logger.info(f"Processing PDF document: {pdf_path}")
            
            # Check if file exists
            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            
            logger.info(f"File size: {pdf_file.stat().st_size / 1024:.2f} KB")
            
            # Process PDF document
            document_elements = await self.pdf_processor.process_pdf(pdf_file)
            logger.info(f"Extracted {len(document_elements)} document elements")
            
            # Get processing statistics
            stats = self.pdf_processor.get_processing_stats()
            logger.info(f"Document processing statistics: {stats}")
            
            # Analyze extracted elements
            element_analysis = self._analyze_elements(document_elements)
            logger.info(f"Element analysis: {element_analysis}")
            
            return {
                'document_path': pdf_path,
                'elements_count': len(document_elements),
                'elements': document_elements,
                'processing_stats': stats,
                'element_analysis': element_analysis,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"Failed to process PDF document: {e}")
            return {
                'document_path': pdf_path,
                'elements_count': 0,
                'elements': [],
                'error': str(e),
                'status': 'failed'
            }
    
    def _analyze_elements(self, elements):
        """Analyze extracted elements for statistics."""
        analysis = {
            'total_elements': len(elements),
            'text_elements': 0,
            'equation_elements': 0,
            'table_elements': 0,
            'figure_elements': 0,
            'pages_processed': set(),
            'total_text_length': 0,
            'chunks_with_equations': 0,
            'chunks_with_tables': 0,
            'chunks_with_figures': 0
        }
        
        for element in elements:
            # Count by chunk type
            if isinstance(element, dict):
                # This is a chunk
                analysis['pages_processed'].add(element.get('page', 0))
                analysis['total_text_length'] += len(element.get('text', ''))
                
                metadata = element.get('metadata', {})
                if metadata.get('has_equations', False):
                    analysis['chunks_with_equations'] += 1
                if metadata.get('has_tables', False):
                    analysis['chunks_with_tables'] += 1
                if metadata.get('has_figures', False):
                    analysis['chunks_with_figures'] += 1
                
                # Count individual elements within chunk
                chunk_elements = element.get('elements', [])
                for elem in chunk_elements:
                    elem_type = elem.get('type', '')
                    if elem_type == 'text':
                        analysis['text_elements'] += 1
                    elif elem_type == 'equation':
                        analysis['equation_elements'] += 1
                    elif elem_type == 'table':
                        analysis['table_elements'] += 1
                    elif elem_type == 'figure':
                        analysis['figure_elements'] += 1
        
        analysis['pages_processed'] = len(analysis['pages_processed'])
        analysis['avg_text_per_chunk'] = (
            analysis['total_text_length'] / len(elements) if elements else 0
        )
        
        return analysis


async def run_document_processing_test(pdf_path: str):
    """
    Run document processing test.
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        Complete processing results
    """
    start_time = datetime.now()
    
    # Initialize tester
    tester = DocumentProcessingTester()
    
    # Initialize processor
    init_success = await tester.initialize_processor()
    if not init_success:
        return {
            'status': 'failed',
            'error': 'Failed to initialize PDF processor',
            'timestamp': start_time.isoformat()
        }
    
    # Process PDF document
    logger.info("=" * 60)
    logger.info("DOCUMENT PROCESSING TEST")
    logger.info("=" * 60)
    
    processing_result = await tester.process_pdf_document(pdf_path)
    
    # Compile final results
    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()
    
    final_result = {
        'test_info': {
            'pdf_path': pdf_path,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'total_processing_time': total_time
        },
        'processing_result': processing_result,
        'status': 'completed'
    }
    
    logger.info("=" * 60)
    logger.info("DOCUMENT PROCESSING TEST COMPLETED")
    logger.info("=" * 60)
    logger.info(f"Total processing time: {total_time:.2f} seconds")
    
    return final_result


def save_results_to_json(results: dict, output_file: str = "document_processing_result.json"):
    """Save processing results to JSON file."""
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


def print_processing_summary(results: dict):
    """Print a summary of processing results."""
    print("\n" + "=" * 60)
    print("DOCUMENT PROCESSING SUMMARY")
    print("=" * 60)
    
    if results.get('status') == 'completed':
        processing_info = results.get('processing_result', {})
        
        if processing_info.get('status') == 'success':
            print("Status: SUCCESS")
            print(f"Document: {processing_info.get('document_path', 'N/A')}")
            print(f"Elements extracted: {processing_info.get('elements_count', 0)}")
            
            # Element analysis
            analysis = processing_info.get('element_analysis', {})
            if analysis:
                print(f"Pages processed: {analysis.get('pages_processed', 0)}")
                print(f"Text elements: {analysis.get('text_elements', 0)}")
                print(f"Equation elements: {analysis.get('equation_elements', 0)}")
                print(f"Table elements: {analysis.get('table_elements', 0)}")
                print(f"Figure elements: {analysis.get('figure_elements', 0)}")
                print(f"Chunks with equations: {analysis.get('chunks_with_equations', 0)}")
                print(f"Chunks with tables: {analysis.get('chunks_with_tables', 0)}")
                print(f"Chunks with figures: {analysis.get('chunks_with_figures', 0)}")
                print(f"Average text per chunk: {analysis.get('avg_text_per_chunk', 0):.1f} chars")
            
            # Processing stats
            stats = processing_info.get('processing_stats', {})
            if stats:
                print(f"Processing statistics: {stats}")
            
            print(f"Total time: {results['test_info']['total_processing_time']:.2f} seconds")
            
        else:
            print("Status: PROCESSING FAILED")
            print(f"Error: {processing_info.get('error', 'Unknown error')}")
    else:
        print("Status: TEST FAILED")
        print(f"Error: {results.get('error', 'Unknown error')}")


async def main():
    """Main function to run document processing test."""
    
    # ========================================
    # CONFIGURATION VARIABLES
    # ========================================
    
    # TODO: Thay đổi đường dẫn PDF của bạn ở đây
    PDF_PATH = r"C:\Users\Admin\Downloads\CV_TranBaDat__Copy_.pdf"
    
    # File output cho kết quả
    OUTPUT_FILE = "document_processing_result.json"
    
    # ========================================
    # RUN TEST
    # ========================================
    
    print("PDFusion Document Processing Test")
    print("=" * 50)
    print(f"PDF Path: {PDF_PATH}")
    print(f"Output File: {OUTPUT_FILE}")
    print("=" * 50)
    
    # Validate PDF path
    if not Path(PDF_PATH).exists():
        print(f"ERROR: PDF file not found: {PDF_PATH}")
        print("Please update the PDF_PATH variable in the main() function")
        return
    
    try:
        # Run document processing test
        results = await run_document_processing_test(PDF_PATH)
        
        # Save results to JSON
        save_results_to_json(results, OUTPUT_FILE)
        
        # Print summary
        print_processing_summary(results)
        
        print(f"\nResults saved to: {OUTPUT_FILE}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        
        # Save error results
        error_results = {
            'status': 'failed',
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'test_info': {
                'pdf_path': PDF_PATH
            }
        }
        save_results_to_json(error_results, OUTPUT_FILE)


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
