"""
Scientific PDF document processor with layout preservation.
Handles equations, tables, figures, and complex scientific content.
"""

import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import fitz  # PyMuPDF

try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False
    logging.warning("Camelot not available - table extraction will be limited")

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logging.warning("PDFPlumber not available - alternative table extraction disabled")

logger = logging.getLogger(__name__)


class DocumentElement:
    """Base class for document elements."""
    
    def __init__(self, element_type: str, bbox: Tuple[float, float, float, float], 
                 page_num: int, content: Any = None):
        self.element_type = element_type
        self.bbox = bbox  # (x0, y0, x1, y1)
        self.page_num = page_num
        self.content = content
        self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.element_type,
            'bbox': self.bbox,
            'page': self.page_num,
            'content': self.content,
            'metadata': self.metadata
        }


class TextElement(DocumentElement):
    """Text element with formatting information."""
    
    def __init__(self, bbox: Tuple[float, float, float, float], page_num: int, 
                 text: str, font_info: Dict = None):
        super().__init__('text', bbox, page_num, text)
        self.font_info = font_info or {}
        self.is_title = False
        self.is_header = False
        
    def analyze_text_type(self):
        """Analyze if text is title, header, etc."""
        if self.font_info.get('size', 0) > 14:
            self.is_title = True
        elif self.font_info.get('flags', 0) & 2**4:  # Bold flag
            self.is_header = True


class EquationElement(DocumentElement):
    """Mathematical equation element."""
    
    def __init__(self, bbox: Tuple[float, float, float, float], page_num: int, 
                 image_data: bytes = None):
        super().__init__('equation', bbox, page_num)
        self.image_data = image_data
        self.latex_code = None
        self.text_description = None
        
    def extract_latex(self):
        """Extract LaTeX code from equation image (placeholder for future OCR)."""
        # Placeholder for Mathpix or similar OCR service
        # For now, generate a simple description
        self.text_description = f"Mathematical equation on page {self.page_num}"
        return self.text_description


class TableElement(DocumentElement):
    """Table element with structured data."""
    
    def __init__(self, bbox: Tuple[float, float, float, float], page_num: int):
        super().__init__('table', bbox, page_num)
        self.dataframe = None
        self.headers = []
        self.rows = []
        
    def extract_table_data(self, pdf_path: Path, page_num: int):
        """Extract structured data from table."""
        try:
            if CAMELOT_AVAILABLE:
                # Use Camelot for table extraction (requires Ghostscript)
                try:
                    tables = camelot.read_pdf(str(pdf_path), pages=str(page_num + 1))
                    if tables and len(tables) > 0:
                        self.dataframe = tables[0].df
                        self.headers = self.dataframe.columns.tolist()
                        self.rows = self.dataframe.values.tolist()
                        return
                except Exception as camelot_error:
                    logger.warning(f"Camelot table extraction failed (Ghostscript may not be installed): {camelot_error}")
                    
            if PDFPLUMBER_AVAILABLE:
                # Fallback to pdfplumber
                try:
                    with pdfplumber.open(pdf_path) as pdf:
                        page = pdf.pages[page_num]
                        tables = page.extract_tables()
                        if tables:
                            table_data = tables[0]
                            self.headers = table_data[0] if table_data else []
                            self.rows = table_data[1:] if len(table_data) > 1 else []
                            return
                except Exception as pdfplumber_error:
                    logger.warning(f"PDFPlumber table extraction failed: {pdfplumber_error}")
            
            # If both methods fail, create a simple placeholder
            logger.info(f"Table extraction not available for page {page_num}, using placeholder")
            self.headers = ["Column 1", "Column 2"]
            self.rows = [["Table data", "not extracted"]]
                        
        except Exception as e:
            logger.warning(f"Table extraction failed: {e}")
            # Create placeholder data
            self.headers = ["Data"]
            self.rows = [["Table extraction failed"]]
            
    def to_markdown(self) -> str:
        """Convert table to markdown format."""
        if not self.headers or not self.rows:
            return "| Table data could not be extracted |"
            
        markdown = "| " + " | ".join(str(h) for h in self.headers) + " |\n"
        markdown += "| " + " | ".join("---" for _ in self.headers) + " |\n"
        
        for row in self.rows:
            markdown += "| " + " | ".join(str(cell) for cell in row) + " |\n"
            
        return markdown


class FigureElement(DocumentElement):
    """Figure/image element with caption."""
    
    def __init__(self, bbox: Tuple[float, float, float, float], page_num: int, 
                 image_data: bytes = None):
        super().__init__('figure', bbox, page_num)
        self.image_data = image_data
        self.caption = ""
        self.description = ""
        
    def extract_caption(self, page_text: str):
        """Extract figure caption from surrounding text."""
        # Simple heuristic to find captions
        lines = page_text.split('\n')
        for i, line in enumerate(lines):
            if any(keyword in line.lower() for keyword in ['figure', 'fig.', 'hình']):
                # Take this line and potentially the next one
                caption_parts = [line.strip()]
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if len(next_line) > 10 and not next_line[0].isupper():
                        caption_parts.append(next_line)
                self.caption = ' '.join(caption_parts)
                break


class ScientificPDFProcessor:
    """
    Advanced PDF processor for scientific documents.
    Preserves layout and extracts structured content.
    """
    
    def __init__(self):
        self.elements = []
        self.page_layouts = {}
        
    async def process_pdf(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """
        Process PDF and extract structured elements.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            List of processed document chunks with metadata
        """
        logger.info(f"Processing scientific PDF: {pdf_path}")
        
        try:
            # Open PDF document
            doc = fitz.open(pdf_path)
            
            # Process each page
            for page_num in range(len(doc)):
                page = doc[page_num]
                await self._process_page(page, page_num, pdf_path)
                
            doc.close()
            
            # Create contextual chunks
            chunks = await self._create_contextual_chunks()
            
            logger.info(f"Processed {len(self.elements)} elements into {len(chunks)} chunks")
            return chunks
            
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            raise
    
    async def _process_page(self, page: fitz.Page, page_num: int, pdf_path: Path):
        """Process a single page and extract elements."""
        
        # Get page text with formatting
        text_dict = page.get_text("dict")
        page_text = page.get_text()
        
        # Extract text elements
        await self._extract_text_elements(text_dict, page_num)
        
        # Extract images (potential equations and figures)
        await self._extract_images(page, page_num, page_text)
        
        # Extract tables
        await self._extract_tables(page, page_num, pdf_path)
        
        # Store page layout
        self.page_layouts[page_num] = {
            'width': page.rect.width,
            'height': page.rect.height,
            'text': page_text
        }
    
    async def _extract_text_elements(self, text_dict: Dict, page_num: int):
        """Extract and analyze text elements."""
        
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
                
            for line in block["lines"]:
                for span in line["spans"]:
                    bbox = tuple(span["bbox"])
                    text = span["text"].strip()
                    
                    if len(text) < 2:  # Skip very short text
                        continue
                        
                    font_info = {
                        'font': span.get('font', ''),
                        'size': span.get('size', 0),
                        'flags': span.get('flags', 0),
                        'color': span.get('color', 0)
                    }
                    
                    element = TextElement(bbox, page_num, text, font_info)
                    element.analyze_text_type()
                    self.elements.append(element)
    
    async def _extract_images(self, page: fitz.Page, page_num: int, page_text: str):
        """Extract images that might be equations or figures."""
        
        image_list = page.get_images()
        
        for img_index, img in enumerate(image_list):
            try:
                # Get image data
                xref = img[0]
                pix = fitz.Pixmap(page.parent, xref)
                
                if pix.n - pix.alpha < 4:  # GRAY or RGB
                    img_data = pix.tobytes("png")
                    
                    # Get image rectangle
                    img_rect = page.get_image_rects(xref)[0] if page.get_image_rects(xref) else None
                    
                    if img_rect:
                        bbox = tuple(img_rect)
                        
                        # Heuristic to classify as equation or figure
                        width = bbox[2] - bbox[0]
                        height = bbox[3] - bbox[1]
                        aspect_ratio = width / height if height > 0 else 1
                        
                        if height < 100 and aspect_ratio > 2:
                            # Likely an equation
                            element = EquationElement(bbox, page_num, img_data)
                            element.extract_latex()
                        else:
                            # Likely a figure
                            element = FigureElement(bbox, page_num, img_data)
                            element.extract_caption(page_text)
                            
                        self.elements.append(element)
                
                pix = None
                
            except Exception as e:
                logger.warning(f"Failed to process image {img_index} on page {page_num}: {e}")
    
    async def _extract_tables(self, page: fitz.Page, page_num: int, pdf_path: Path):
        """Extract table elements."""
        
        # Simple table detection based on text layout
        # This is a basic implementation - can be enhanced with ML models
        
        text_dict = page.get_text("dict")
        potential_tables = []
        
        # Look for text patterns that suggest tables
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
                
            lines = block["lines"]
            if len(lines) >= 3:  # At least 3 rows for a table
                # Check if lines have similar structure (multiple columns)
                column_counts = []
                for line in lines:
                    spans = line["spans"]
                    # Count potential columns based on spacing
                    x_positions = [span["bbox"][0] for span in spans]
                    unique_x = len(set(round(x, -1) for x in x_positions))  # Round to nearest 10
                    column_counts.append(unique_x)
                
                # If most lines have similar column count, it might be a table
                if len(set(column_counts)) <= 2 and max(column_counts) >= 2:
                    bbox = (
                        min(span["bbox"][0] for line in lines for span in line["spans"]),
                        min(span["bbox"][1] for line in lines for span in line["spans"]),
                        max(span["bbox"][2] for line in lines for span in line["spans"]),
                        max(span["bbox"][3] for line in lines for span in line["spans"])
                    )
                    
                    table_element = TableElement(bbox, page_num)
                    table_element.extract_table_data(pdf_path, page_num)
                    self.elements.append(table_element)
    
    async def _create_contextual_chunks(self) -> List[Dict[str, Any]]:
        """Create contextual chunks that preserve document structure."""
        
        chunks = []
        
        # Group elements by page
        pages = {}
        for element in self.elements:
            if element.page_num not in pages:
                pages[element.page_num] = []
            pages[element.page_num].append(element)
        
        # Process each page
        for page_num, page_elements in pages.items():
            # Sort elements by vertical position
            page_elements.sort(key=lambda e: e.bbox[1])
            
            # Create chunks with context
            current_chunk = {
                'text': '',
                'page': page_num,
                'elements': [],
                'metadata': {
                    'has_equations': False,
                    'has_tables': False,
                    'has_figures': False,
                    'section_type': 'content'
                }
            }
            
            for element in page_elements:
                if isinstance(element, TextElement):
                    current_chunk['text'] += element.content + ' '
                    
                    # Check if this should start a new chunk (new section)
                    if element.is_title or element.is_header:
                        if current_chunk['text'].strip():
                            chunks.append(current_chunk.copy())
                        
                        # Start new chunk
                        current_chunk = {
                            'text': element.content + ' ',
                            'page': page_num,
                            'elements': [element.to_dict()],
                            'metadata': {
                                'has_equations': False,
                                'has_tables': False,
                                'has_figures': False,
                                'section_type': 'header' if element.is_header else 'title'
                            }
                        }
                        continue
                
                elif isinstance(element, EquationElement):
                    current_chunk['metadata']['has_equations'] = True
                    if element.text_description:
                        current_chunk['text'] += f" [EQUATION: {element.text_description}] "
                
                elif isinstance(element, TableElement):
                    current_chunk['metadata']['has_tables'] = True
                    markdown_table = element.to_markdown()
                    current_chunk['text'] += f" [TABLE: {markdown_table}] "
                
                elif isinstance(element, FigureElement):
                    current_chunk['metadata']['has_figures'] = True
                    if element.caption:
                        current_chunk['text'] += f" [FIGURE: {element.caption}] "
                
                current_chunk['elements'].append(element.to_dict())
                
                # Split chunk if it gets too long
                if len(current_chunk['text']) > 1000:
                    chunks.append(current_chunk.copy())
                    current_chunk = {
                        'text': '',
                        'page': page_num,
                        'elements': [],
                        'metadata': {
                            'has_equations': False,
                            'has_tables': False,
                            'has_figures': False,
                            'section_type': 'content'
                        }
                    }
            
            # Add final chunk if not empty
            if current_chunk['text'].strip():
                chunks.append(current_chunk)
        
        # Add chunk IDs and clean up text
        for i, chunk in enumerate(chunks):
            chunk['chunk_id'] = f"chunk_{i}"
            chunk['text'] = chunk['text'].strip()
            
            # Add context from surrounding chunks
            if i > 0:
                chunk['previous_context'] = chunks[i-1]['text'][:100]
            if i < len(chunks) - 1:
                chunk['next_context'] = chunks[i+1]['text'][:100]
        
        return chunks
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """Get statistics about processed document."""
        
        stats = {
            'total_elements': len(self.elements),
            'text_elements': len([e for e in self.elements if isinstance(e, TextElement)]),
            'equations': len([e for e in self.elements if isinstance(e, EquationElement)]),
            'tables': len([e for e in self.elements if isinstance(e, TableElement)]),
            'figures': len([e for e in self.elements if isinstance(e, FigureElement)]),
            'pages_processed': len(self.page_layouts)
        }
        
        return stats
