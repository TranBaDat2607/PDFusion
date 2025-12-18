"""
Reference manager for handling PDF and web references with navigation support.
Provides clickable references that can jump to specific PDF pages or open web links.
"""

import logging
from typing import Dict, Any, Optional, Callable, List, Tuple
from pathlib import Path
import webbrowser
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PDFReference:
    """Reference to a specific location in a PDF document."""
    
    page: int
    text: str
    confidence: float
    document_id: str
    document_path: str
    chunk_id: str
    bbox: Optional[Tuple[float, float, float, float]] = None
    has_equations: bool = False
    has_tables: bool = False
    has_figures: bool = False
    
    def __str__(self) -> str:
        return f"📄 Trang {self.page}: {self.text[:100]}..."


@dataclass
class WebReference:
    """Reference to a web source."""
    
    url: str
    title: str
    snippet: str
    source_type: str
    reliability_score: float
    scraped_at: str
    
    def __str__(self) -> str:
        source_emoji = {
            'academic': '🎓',
            'wikipedia': '📖',
            'official': '🏛️',
            'news': '📰',
            'web': '🌐'
        }
        emoji = source_emoji.get(self.source_type, '🌐')
        return f"{emoji} {self.source_type.title()}: {self.snippet[:100]}..."


class ReferenceManager:
    """
    Manages references and provides navigation functionality.
    Handles both PDF references (with page jumping) and web references (with link opening).
    """
    
    def __init__(self):
        """Initialize reference manager."""
        self.pdf_viewer_callback: Optional[Callable] = None
        self.web_browser_callback: Optional[Callable] = None
        self.reference_history: List[Dict[str, Any]] = []
        
        logger.info("Reference manager initialized")
    
    def set_pdf_viewer_callback(self, callback: Callable[[int, Optional[Tuple[float, float, float, float]]], None]):
        """
        Set callback function for PDF navigation.
        
        Args:
            callback: Function that takes (page_number, bbox) and navigates to that location
        """
        self.pdf_viewer_callback = callback
        logger.info("PDF viewer callback registered")
    
    def set_web_browser_callback(self, callback: Optional[Callable[[str], None]] = None):
        """
        Set callback function for web navigation.
        
        Args:
            callback: Function that takes URL and opens it (defaults to webbrowser.open)
        """
        self.web_browser_callback = callback or webbrowser.open
        logger.info("Web browser callback registered")
    
    def create_pdf_reference(self, reference_data: Dict[str, Any]) -> PDFReference:
        """
        Create a PDF reference from reference data.
        
        Args:
            reference_data: Dictionary containing reference information
            
        Returns:
            PDFReference object
        """
        return PDFReference(
            page=reference_data.get('page', 0),
            text=reference_data.get('text', ''),
            confidence=reference_data.get('confidence', 0.0),
            document_id=reference_data.get('document_id', ''),
            document_path=reference_data.get('document_path', ''),
            chunk_id=reference_data.get('chunk_id', ''),
            bbox=reference_data.get('bbox'),
            has_equations=reference_data.get('has_equations', False),
            has_tables=reference_data.get('has_tables', False),
            has_figures=reference_data.get('has_figures', False)
        )
    
    def create_web_reference(self, reference_data: Dict[str, Any]) -> WebReference:
        """
        Create a web reference from reference data.
        
        Args:
            reference_data: Dictionary containing reference information
            
        Returns:
            WebReference object
        """
        return WebReference(
            url=reference_data.get('url', ''),
            title=reference_data.get('title', ''),
            snippet=reference_data.get('snippet', ''),
            source_type=reference_data.get('source_type', 'web'),
            reliability_score=reference_data.get('reliability_score', 0.5),
            scraped_at=reference_data.get('scraped_at', '')
        )
    
    def navigate_to_pdf_reference(self, pdf_ref: PDFReference) -> bool:
        """
        Navigate to a PDF reference location.
        
        Args:
            pdf_ref: PDF reference to navigate to
            
        Returns:
            True if navigation was successful
        """
        try:
            if not self.pdf_viewer_callback:
                logger.warning("PDF viewer callback not set")
                return False
            
            # Add to history
            self._add_to_history('pdf', pdf_ref)
            
            # Navigate to the reference
            self.pdf_viewer_callback(pdf_ref.page, pdf_ref.bbox)
            
            logger.info(f"Navigated to PDF page {pdf_ref.page}")
            return True
            
        except Exception as e:
            logger.error(f"PDF navigation failed: {e}")
            return False
    
    def navigate_to_web_reference(self, web_ref: WebReference) -> bool:
        """
        Navigate to a web reference URL.
        
        Args:
            web_ref: Web reference to navigate to
            
        Returns:
            True if navigation was successful
        """
        try:
            if not self.web_browser_callback:
                logger.warning("Web browser callback not set")
                return False
            
            # Add to history
            self._add_to_history('web', web_ref)
            
            # Open the URL
            self.web_browser_callback(web_ref.url)
            
            logger.info(f"Opened web reference: {web_ref.url}")
            return True
            
        except Exception as e:
            logger.error(f"Web navigation failed: {e}")
            return False
    
    def _add_to_history(self, ref_type: str, reference: Any):
        """Add reference to navigation history."""
        from datetime import datetime

        history_entry = {
            'type': ref_type,
            'timestamp': datetime.now().isoformat(),
            'reference': reference
        }

        self.reference_history.append(history_entry)

        # Keep only last 50 entries
        if len(self.reference_history) > 50:
            self.reference_history = self.reference_history[-50:]
    
    def format_reference_for_display(self, ref_type: str, reference_data: Dict[str, Any]) -> str:
        """
        Format a reference for display in the GUI.
        
        Args:
            ref_type: 'pdf' or 'web'
            reference_data: Reference data dictionary
            
        Returns:
            Formatted string for display
        """
        
        if ref_type == 'pdf':
            pdf_ref = self.create_pdf_reference(reference_data)
            
            # Add content indicators
            indicators = []
            if pdf_ref.has_equations:
                indicators.append("📐")
            if pdf_ref.has_tables:
                indicators.append("📊")
            if pdf_ref.has_figures:
                indicators.append("📈")
            
            indicator_str = " ".join(indicators)
            confidence_str = f"({pdf_ref.confidence:.1%})" if pdf_ref.confidence > 0 else ""
            
            return f"{str(pdf_ref)} {indicator_str} {confidence_str}".strip()
            
        elif ref_type == 'web':
            web_ref = self.create_web_reference(reference_data)
            reliability_str = f"({web_ref.reliability_score:.1%})" if web_ref.reliability_score > 0 else ""
            
            return f"{str(web_ref)} {reliability_str}".strip()
        
        return "Unknown reference type"
    
    def get_navigation_history(self) -> List[Dict[str, Any]]:
        """Get the navigation history."""
        return self.reference_history.copy()
    
    def clear_history(self):
        """Clear the navigation history."""
        self.reference_history.clear()
        logger.info("Reference history cleared")
    
