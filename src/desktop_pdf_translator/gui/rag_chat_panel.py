"""
RAG Chat Panel for PDFusion - Enhanced Q&A interface with web research integration.
Provides comprehensive answers with PDF and web references.
"""

import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, 
    QPushButton, QLabel, QScrollArea, QFrame, QSplitter,
    QGroupBox, QProgressBar, QComboBox, QCheckBox, QSpinBox,
    QMessageBox, QMenu
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QTextCursor, QTextCharFormat, QColor, QPixmap, QIcon, QAction

from ..rag import (
    ScientificPDFProcessor, ChromaDBManager, 
    WebResearchEngine, EnhancedRAGChain, ReferenceManager
)
from ..config import get_settings

logger = logging.getLogger(__name__)


class RAGWorker(QThread):
    """Worker thread for RAG processing to avoid blocking the GUI."""
    
    # Signals
    answer_ready = Signal(dict)
    error_occurred = Signal(str)
    progress_updated = Signal(str, int)
    
    def __init__(self, rag_chain: EnhancedRAGChain, question: str, 
                 document_id: Optional[str] = None, include_web: bool = True):
        super().__init__()
        self.rag_chain = rag_chain
        self.question = question
        self.document_id = document_id
        self.include_web = include_web
    
    def run(self):
        """Run RAG processing in background thread."""
        try:
            self.progress_updated.emit("Processing question...", 20)
            
            # Create event loop for async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self.progress_updated.emit("Searching in PDF...", 40)
            
            # Process the question
            result = loop.run_until_complete(
                self.rag_chain.answer_question(
                    question=self.question,
                    document_id=self.document_id,
                    include_web_research=self.include_web
                )
            )
            
            self.progress_updated.emit("Completed", 100)
            self.answer_ready.emit(result)
            
        except Exception as e:
            logger.error(f"RAG processing failed: {e}")
            self.error_occurred.emit(str(e))
        finally:
            if 'loop' in locals():
                loop.close()


class ReferenceWidget(QFrame):
    """Widget for displaying a single reference with click functionality."""
    
    reference_clicked = Signal(str, dict)  # (type, reference_data)
    
    def __init__(self, ref_type: str, reference_data: Dict[str, Any], 
                 reference_manager: ReferenceManager):
        super().__init__()
        self.ref_type = ref_type
        self.reference_data = reference_data
        self.reference_manager = reference_manager
        
        self.setFrameStyle(QFrame.Box)
        self.setStyleSheet("""
            ReferenceWidget {
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 5px;
                margin: 2px;
                background-color: #f9f9f9;
            }
            ReferenceWidget:hover {
                background-color: #e9e9e9;
                border-color: #007acc;
            }
        """)
        
        self.setup_ui()
        self.setCursor(Qt.PointingHandCursor)
    
    def setup_ui(self):
        """Setup the reference widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        
        # Format reference text
        display_text = self.reference_manager.format_reference_for_display(
            self.ref_type, self.reference_data
        )
        
        # Main text
        text_label = QLabel(display_text)
        text_label.setFont(QFont("Segoe UI", 9))
        
        # Additional info for PDF references
        if self.ref_type == 'pdf':
            confidence = self.reference_data.get('confidence', 0.0)
            if confidence > 0:
                confidence_label = QLabel(f"Confidence: {confidence:.1%}")
                confidence_label.setStyleSheet("color: #666; font-size: 8pt;")
                layout.addWidget(confidence_label)
        
        # Additional info for web references
        elif self.ref_type == 'web':
            reliability = self.reference_data.get('reliability_score', 0.0)
            if reliability > 0:
                reliability_label = QLabel(f"Reliability: {reliability:.1%}")
                reliability_label.setStyleSheet("color: #666; font-size: 8pt;")
                layout.addWidget(reliability_label)
        
        layout.addWidget(text_label)
    
    def mousePressEvent(self, event):
        """Handle mouse click on reference."""
        if event.button() == Qt.LeftButton:
            self.reference_clicked.emit(self.ref_type, self.reference_data)
        super().mousePressEvent(event)


class ChatHistoryWidget(QScrollArea):
    """Widget for displaying chat history with references."""
    
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Main widget and layout
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setAlignment(Qt.AlignTop)
        
        self.setWidget(self.content_widget)
        
        # Chat history
        self.chat_items = []
    
    def add_question(self, question: str):
        """Add a user question to the chat history."""
        
        question_frame = QFrame()
        question_frame.setStyleSheet("""
            QFrame {
                background-color: #e3f2fd;
                border-radius: 10px;
                padding: 10px;
                margin: 5px;
            }
        """)
        
        layout = QVBoxLayout(question_frame)
        
        # Question header
        header = QLabel("❓ Question:")
        header.setFont(QFont("Segoe UI", 9, QFont.Bold))
        layout.addWidget(header)
        
        # Question text
        question_text = QLabel(question)
        question_text.setWordWrap(True)
        question_text.setFont(QFont("Segoe UI", 9))
        layout.addWidget(question_text)
        
        self.content_layout.addWidget(question_frame)
        self.chat_items.append(('question', question_frame))
        
        # Scroll to bottom
        QTimer.singleShot(100, self._scroll_to_bottom)
    
    def add_answer(self, answer_data: Dict[str, Any], reference_manager: ReferenceManager):
        """Add an answer with references to the chat history."""
        
        answer_frame = QFrame()
        answer_frame.setStyleSheet("""
            QFrame {
                background-color: #f1f8e9;
                border-radius: 10px;
                padding: 10px;
                margin: 5px;
            }
        """)
        
        layout = QVBoxLayout(answer_frame)
        
        # Answer header
        header = QLabel("🤖 Answer:")
        header.setFont(QFont("Segoe UI", 9, QFont.Bold))
        layout.addWidget(header)
        
        # Answer text
        answer_text = QTextEdit()
        answer_text.setPlainText(answer_data.get('answer', ''))
        answer_text.setReadOnly(True)
        answer_text.setMaximumHeight(200)
        answer_text.setFont(QFont("Segoe UI", 9))
        layout.addWidget(answer_text)
        
        # References section
        pdf_refs = answer_data.get('pdf_references', [])
        web_refs = answer_data.get('web_references', [])
        
        if pdf_refs or web_refs:
            refs_group = QGroupBox("📚 References:")
            refs_layout = QVBoxLayout(refs_group)
            
            # PDF references
            if pdf_refs:
                pdf_label = QLabel("📄 PDF Sources:")
                pdf_label.setFont(QFont("Segoe UI", 8, QFont.Bold))
                refs_layout.addWidget(pdf_label)
                
                for ref_data in pdf_refs[:3]:  # Show top 3 PDF refs
                    ref_widget = ReferenceWidget('pdf', ref_data, reference_manager)
                    ref_widget.reference_clicked.connect(self._handle_reference_click)
                    refs_layout.addWidget(ref_widget)
            
            # Web references
            if web_refs:
                web_label = QLabel("🌐 Web Sources:")
                web_label.setFont(QFont("Segoe UI", 8, QFont.Bold))
                refs_layout.addWidget(web_label)
                
                for ref_data in web_refs[:3]:  # Show top 3 web refs
                    ref_widget = ReferenceWidget('web', ref_data, reference_manager)
                    ref_widget.reference_clicked.connect(self._handle_reference_click)
                    refs_layout.addWidget(ref_widget)
            
            layout.addWidget(refs_group)
        
        # Quality metrics
        quality = answer_data.get('quality_metrics', {})
        if quality:
            confidence = quality.get('confidence', 0.0)
            completeness = quality.get('completeness', 0.0)
            
            metrics_label = QLabel(
                f"📊 Quality - Confidence: {confidence:.1%}, "
                f"Completeness: {completeness:.1%}"
            )
            metrics_label.setStyleSheet("color: #666; font-size: 8pt;")
            layout.addWidget(metrics_label)
        
        self.content_layout.addWidget(answer_frame)
        self.chat_items.append(('answer', answer_frame))
        
        # Scroll to bottom
        QTimer.singleShot(100, self._scroll_to_bottom)
    
    def _handle_reference_click(self, ref_type: str, reference_data: Dict[str, Any]):
        """Handle reference click - emit signal to parent."""
        self.parent().handle_reference_click(ref_type, reference_data)
    
    def _scroll_to_bottom(self):
        """Scroll to the bottom of the chat history."""
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def clear_history(self):
        """Clear all chat history."""
        for _, widget in self.chat_items:
            widget.deleteLater()
        self.chat_items.clear()


class RAGChatPanel(QWidget):
    """
    Main RAG Chat Panel widget for PDFusion.
    Provides Q&A interface with PDF and web research integration.
    """
    
    # Signals
    pdf_navigation_requested = Signal(int, object)  # (page, bbox)
    web_link_requested = Signal(str)  # (url)
    
    def __init__(self, pdf_viewer=None):
        super().__init__()
        self.pdf_viewer = pdf_viewer
        self.settings = get_settings()
        
        # RAG components
        self.vector_store = None
        self.web_research = None
        self.rag_chain = None
        self.reference_manager = None
        
        # Current document
        self.current_document_id = None
        self.current_document_path = None
        
        # Worker thread
        self.rag_worker = None
        
        self.setup_ui()
        self.initialize_rag_system()
        
        logger.info("RAG Chat Panel initialized")
    
    def setup_ui(self):
        """Setup the chat panel UI."""
        layout = QVBoxLayout(self)
        
        # Header
        header_layout = QHBoxLayout()
        
        title_label = QLabel("🤖 AI Chat - Smart Q&A")
        title_label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Settings button
        settings_btn = QPushButton("⚙️")
        settings_btn.setMaximumSize(30, 30)
        settings_btn.clicked.connect(self.show_settings)
        header_layout.addWidget(settings_btn)
        
        layout.addLayout(header_layout)
        
        # Main splitter
        splitter = QSplitter(Qt.Vertical)
        
        # Chat history area
        self.chat_history = ChatHistoryWidget()
        splitter.addWidget(self.chat_history)
        
        # Input area
        input_widget = self.create_input_area()
        splitter.addWidget(input_widget)
        
        # Set splitter proportions
        splitter.setSizes([400, 150])
        
        layout.addWidget(splitter)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #666; font-size: 8pt;")
        layout.addWidget(self.status_label)
    
    def create_input_area(self) -> QWidget:
        """Create the input area widget."""
        
        input_widget = QWidget()
        layout = QVBoxLayout(input_widget)
        
        # Options row
        options_layout = QHBoxLayout()
        
        # Web research checkbox
        self.web_research_cb = QCheckBox("Web Search")
        self.web_research_cb.setChecked(True)
        self.web_research_cb.setToolTip("Include information from internet in answers")
        options_layout.addWidget(self.web_research_cb)
        
        options_layout.addStretch()
        
        # Clear button
        clear_btn = QPushButton("🗑️ Clear History")
        clear_btn.clicked.connect(self.clear_chat_history)
        options_layout.addWidget(clear_btn)
        
        layout.addLayout(options_layout)
        
        # Question input
        input_layout = QHBoxLayout()
        
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("Enter your question...")
        self.question_input.returnPressed.connect(self.ask_question)
        input_layout.addWidget(self.question_input)
        
        # Ask button
        self.ask_button = QPushButton("Ask")
        self.ask_button.clicked.connect(self.ask_question)
        self.ask_button.setDefault(True)
        input_layout.addWidget(self.ask_button)
        
        layout.addLayout(input_layout)
        
        return input_widget
    
    def initialize_rag_system(self):
        """Initialize the RAG system components."""
        try:
            # Initialize vector store
            self.vector_store = ChromaDBManager()
            
            # Initialize web research
            self.web_research = WebResearchEngine()
            
            # Initialize RAG chain
            self.rag_chain = EnhancedRAGChain(self.vector_store, self.web_research)
            
            # Initialize reference manager
            self.reference_manager = ReferenceManager()
            self.reference_manager.set_pdf_viewer_callback(self._navigate_to_pdf)
            self.reference_manager.set_web_browser_callback(self._open_web_link)
            
            self.status_label.setText("RAG system ready")
            logger.info("RAG system initialized successfully")
            
        except Exception as e:
            error_msg = f"RAG system initialization error: {str(e)}"
            self.status_label.setText(error_msg)
            logger.error(error_msg)
            
            # Show error message
            QMessageBox.warning(self, "Initialization Error", error_msg)
    
    def set_current_document(self, document_path: Path, document_id: str = None):
        """
        Set the current document for RAG processing.
        
        Args:
            document_path: Path to the PDF document
            document_id: Optional document ID (will be generated if not provided)
        """
        self.current_document_path = document_path
        self.current_document_id = document_id or str(document_path.stem)
        
        self.status_label.setText(f"Current document: {document_path.name}")
        logger.info(f"Current document set: {document_path}")
    
    def process_document(self, document_path: Path):
        """
        Process a document for RAG (extract and index content).
        
        Args:
            document_path: Path to the PDF document
        """
        # Check if widget is enabled (RAG is on)
        if not self.isEnabled():
            logger.info(f"RAG is disabled, skipping document processing: {document_path}")
            return
            
        if not self.vector_store:
            QMessageBox.warning(self, "Error", "RAG system not initialized")
            return
        
        try:
            self.status_label.setText("Processing document...")
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(10)
            
            # Generate document ID
            document_id = str(document_path.stem)
            
            # Check if document is already processed
            existing_chunks = asyncio.run(self.vector_store.search_by_document(document_id))
            if existing_chunks:
                logger.info(f"Document {document_id} already processed, using existing data")
                self.set_current_document(document_path, document_id)
                self.progress_bar.setVisible(False)
                self.status_label.setText(f"Document already available: {document_path.name}")
                return
            
            self.progress_bar.setValue(30)
            self.status_label.setText("Extracting PDF content...")
            
            # Process PDF document
            processor = ScientificPDFProcessor()
            chunks = asyncio.run(processor.process_pdf(document_path))
            
            if not chunks:
                raise Exception("Cannot extract content from PDF")
            
            self.progress_bar.setValue(60)
            self.status_label.setText("Saving to database...")
            
            # Add chunks to vector store
            success = asyncio.run(self.vector_store.add_document_chunks(
                chunks=chunks,
                document_id=document_id,
                document_path=str(document_path)
            ))
            
            if not success:
                raise Exception("Cannot save document to database")
            
            self.progress_bar.setValue(90)
            
            # Set as current document
            self.set_current_document(document_path, document_id)
            
            self.progress_bar.setValue(100)
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"Document processed: {document_path.name} ({len(chunks)} chunks)")
            
            logger.info(f"Document processed successfully: {document_path} ({len(chunks)} chunks)")
            
        except Exception as e:
            error_msg = f"Document processing error: {str(e)}"
            self.status_label.setText(error_msg)
            self.progress_bar.setVisible(False)
            logger.error(error_msg)
            
            QMessageBox.warning(self, "Processing Error", error_msg)
    
    def ask_question(self):
        """Process user question."""
        question = self.question_input.text().strip()
        if not question:
            return
        
        if not self.rag_chain:
            QMessageBox.warning(self, "Error", "RAG system not ready")
            return
        
        # Add question to chat history
        self.chat_history.add_question(question)
        
        # Clear input
        self.question_input.clear()
        
        # Always use current document scope (no scope selection)
        document_id = self.current_document_id
        
        # Start processing
        self._start_rag_processing(question, document_id, self.web_research_cb.isChecked())
    
    
    def _start_rag_processing(self, question: str, document_id: Optional[str], include_web: bool):
        """Start RAG processing in background thread."""
        
        # Disable input during processing
        self.ask_button.setEnabled(False)
        self.question_input.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
        # Start worker thread
        self.rag_worker = RAGWorker(self.rag_chain, question, document_id, include_web)
        self.rag_worker.answer_ready.connect(self._handle_answer_ready)
        self.rag_worker.error_occurred.connect(self._handle_error)
        self.rag_worker.progress_updated.connect(self._update_progress)
        self.rag_worker.start()
    
    def _handle_answer_ready(self, answer_data: Dict[str, Any]):
        """Handle when RAG processing is complete."""
        
        # Add answer to chat history
        self.chat_history.add_answer(answer_data, self.reference_manager)
        
        # Re-enable input
        self.ask_button.setEnabled(True)
        self.question_input.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        # Update status
        processing_time = answer_data.get('processing_time', 0)
        sources_used = answer_data.get('sources_used', {})
        total_sources = sources_used.get('pdf_sources', 0) + sources_used.get('web_sources', 0)
        
        self.status_label.setText(f"Completed in {processing_time:.1f}s - {total_sources} sources")
        
        logger.info(f"Answer generated successfully in {processing_time:.1f}s")
    
    def _handle_error(self, error_message: str):
        
        # Re-enable input
        self.ask_button.setEnabled(True)
        self.question_input.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        # Show error
        self.status_label.setText(f"Error: {error_message}")
        QMessageBox.warning(self, "Processing Error", f"Cannot answer question:\n{error_message}")
        
        logger.error(f"RAG processing error: {error_message}")
    
    def _update_progress(self, message: str, progress: int):
        """Update progress bar and status."""
        self.status_label.setText(message)
        self.progress_bar.setValue(progress)
    
    def handle_reference_click(self, ref_type: str, reference_data: Dict[str, Any]):
        """Handle reference click from chat history."""
        
        if ref_type == 'pdf':
            pdf_ref = self.reference_manager.create_pdf_reference(reference_data)
            success = self.reference_manager.navigate_to_pdf_reference(pdf_ref)
            if success:
                # Emit signal for PDF navigation
                self.pdf_navigation_requested.emit(pdf_ref.page, pdf_ref.bbox)
        
        elif ref_type == 'web':
            web_ref = self.reference_manager.create_web_reference(reference_data)
            success = self.reference_manager.navigate_to_web_reference(web_ref)
            if success:
                # Emit signal for web navigation
                self.web_link_requested.emit(web_ref.url)
    
    def _navigate_to_pdf(self, page: int, bbox: Optional[tuple] = None):
        """Navigate to PDF page (callback for reference manager)."""
        if self.pdf_viewer:
            # This will be implemented when integrating with the main window
            pass
    
    def _open_web_link(self, url: str):
        """Open web link (callback for reference manager)."""
        import webbrowser
        webbrowser.open(url)
    
    def clear_chat_history(self):
        """Clear the chat history."""
        self.chat_history.clear_history()
        if self.reference_manager:
            self.reference_manager.clear_history()
        self.status_label.setText("History cleared")
    
    def show_settings(self):
        """Show RAG settings dialog."""
        # Placeholder for settings dialog
        QMessageBox.information(self, "Settings", "RAG settings will be added in future version")
    
    def get_rag_stats(self) -> Dict[str, Any]:
        """Get RAG system statistics."""
        stats = {}
        
        if self.vector_store:
            stats.update(self.vector_store.get_collection_stats())
        
        if self.reference_manager:
            history = self.reference_manager.get_navigation_history()
            stats['navigation_history'] = len(history)
        
        stats['current_document'] = self.current_document_path.name if self.current_document_path else None
        
        return stats
    
    def set_rag_disabled_message(self):
        """Display message when RAG is disabled."""
        # Disable input controls
        self.question_input.setEnabled(False)
        self.ask_button.setEnabled(False)
        self.web_research_cb.setEnabled(False)
        
        self.status_label.setText("RAG disabled")
    
    def set_rag_enabled_message(self):
        """Display message when RAG is re-enabled."""
        # Re-enable input controls
        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self.web_research_cb.setEnabled(True)
        
        self.status_label.setText("RAG ready")
