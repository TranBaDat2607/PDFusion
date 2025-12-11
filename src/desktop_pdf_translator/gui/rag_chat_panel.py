"""
RAG Chat Panel for PDFusion - Enhanced Q&A interface with web research integration.
Provides comprehensive answers with PDF and web references.
"""

import logging
import asyncio
import time
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


class DocumentProcessorWorker(QThread):
    """Worker thread for document processing with detailed progress tracking."""

    # Signals
    processing_completed = Signal(str, int)  # (document_id, num_chunks)
    processing_failed = Signal(str)  # (error_message)
    progress_updated = Signal(dict)  # (progress_data)

    def __init__(self, document_path: Path, vector_store, document_id: str):
        super().__init__()
        self.document_path = document_path
        self.vector_store = vector_store
        self.document_id = document_id
        self._cancelled = False
        self._start_time = None

    def cancel(self):
        """Request cancellation of document processing."""
        self._cancelled = True
        logger.info("Document processing cancellation requested")

    def run(self):
        """Run document processing in background thread with detailed progress."""
        try:
            import time
            from ..rag import ScientificPDFProcessor

            self._start_time = time.time()

            # Create event loop for async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Stage 1: Check if already processed (10%)
            self._emit_progress(
                stage="Checking database...",
                progress=10,
                message="Verifying if document is already processed"
            )

            if self._cancelled:
                return

            existing_chunks = loop.run_until_complete(
                self.vector_store.search_by_document(self.document_id)
            )

            if existing_chunks:
                self._emit_progress(
                    stage="Already processed",
                    progress=100,
                    message=f"Document already in database ({len(existing_chunks)} chunks)"
                )
                self.processing_completed.emit(self.document_id, len(existing_chunks))
                return

            # Stage 2: Initialize processor (20%)
            self._emit_progress(
                stage="Initializing processor...",
                progress=20,
                message="Preparing PDF extraction tools"
            )

            if self._cancelled:
                return

            processor = ScientificPDFProcessor()

            # Stage 3: Extract PDF content (30-60%)
            self._emit_progress(
                stage="Extracting PDF content...",
                progress=30,
                message="Reading PDF pages, tables, and figures",
                detail="This may take 20-40 seconds for large documents"
            )

            if self._cancelled:
                return

            # Process PDF with progress updates
            chunks = loop.run_until_complete(processor.process_pdf(self.document_path))

            if not chunks:
                raise Exception("Failed to extract content from PDF")

            if self._cancelled:
                return

            # Stage 4: Chunking and embedding (60-80%)
            self._emit_progress(
                stage="Creating chunks...",
                progress=60,
                message=f"Processing {len(chunks)} text chunks",
                detail="Generating semantic embeddings for search"
            )

            if self._cancelled:
                return

            # Stage 5: Save to vector database (80-95%)
            self._emit_progress(
                stage="Saving to database...",
                progress=80,
                message=f"Indexing {len(chunks)} chunks in vector store",
                detail="Building search index"
            )

            if self._cancelled:
                return

            success = loop.run_until_complete(
                self.vector_store.add_document_chunks(
                    chunks=chunks,
                    document_id=self.document_id,
                    document_path=str(self.document_path)
                )
            )

            if not success:
                raise Exception("Failed to save document to database")

            # Stage 6: Complete (100%)
            elapsed_time = time.time() - self._start_time
            self._emit_progress(
                stage="Processing complete",
                progress=100,
                message=f"Document ready ({len(chunks)} chunks)",
                detail=f"Completed in {elapsed_time:.1f} seconds"
            )

            self.processing_completed.emit(self.document_id, len(chunks))
            logger.info(f"Document processed successfully in {elapsed_time:.1f}s: {self.document_path}")

        except Exception as e:
            error_msg = f"Document processing error: {str(e)}"
            logger.error(error_msg)
            self.processing_failed.emit(error_msg)
        finally:
            if 'loop' in locals():
                loop.close()

    def _emit_progress(self, stage: str, progress: int, message: str, detail: str = ""):
        """Emit progress update with detailed information."""
        if self._cancelled:
            return

        elapsed = time.time() - self._start_time if self._start_time else 0

        # Estimate remaining time (simple linear projection)
        if progress > 0 and progress < 100:
            estimated_total = (elapsed / progress) * 100
            eta = estimated_total - elapsed
        else:
            eta = 0

        progress_data = {
            'stage': stage,
            'progress': progress,
            'message': message,
            'detail': detail,
            'elapsed_time': elapsed,
            'eta': eta,
            'can_cancel': True
        }

        self.progress_updated.emit(progress_data)


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

        # Worker threads
        self.rag_worker = None
        self.document_processor_worker = None
        
        self.setup_ui()
        self.initialize_rag_system()
        
        logger.info("RAG Chat Panel initialized")
    
    def setup_ui(self):
        """Setup the chat panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Header - compact
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel("🤖 AI Chat")
        title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Settings button
        settings_btn = QPushButton("⚙️")
        settings_btn.setMaximumSize(25, 25)
        settings_btn.clicked.connect(self.show_settings)
        header_layout.addWidget(settings_btn)
        
        layout.addLayout(header_layout)
        
        # Chat history area - chiếm phần lớn không gian
        self.chat_history = ChatHistoryWidget()
        layout.addWidget(self.chat_history, stretch=1)
        
        # Input area - compact, chiếm ít không gian
        input_widget = self.create_input_area()
        layout.addWidget(input_widget)
        
        # Progress section - enhanced with detailed information
        progress_widget = self._create_progress_section()
        layout.addWidget(progress_widget)

        # Status label - compact
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #666; font-size: 8pt;")
        self.status_label.setMaximumHeight(20)
        layout.addWidget(self.status_label)
    
    def _create_progress_section(self) -> QWidget:
        """Create enhanced progress section with detailed information and cancel button."""
        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        progress_layout.setSpacing(3)

        # Progress container (hidden by default)
        self.progress_container = QFrame()
        self.progress_container.setFrameStyle(QFrame.StyledPanel)
        self.progress_container.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 8px;
            }
        """)
        self.progress_container.setVisible(False)

        container_layout = QVBoxLayout(self.progress_container)
        container_layout.setSpacing(5)

        # Stage label (main activity)
        self.stage_label = QLabel("Processing...")
        self.stage_label.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.stage_label.setStyleSheet("color: #2196F3;")
        container_layout.addWidget(self.stage_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(20)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 3px;
                text-align: center;
                background-color: white;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
        """)
        container_layout.addWidget(self.progress_bar)

        # Detail label (what's happening)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #666; font-size: 8pt;")
        container_layout.addWidget(self.detail_label)

        # Time and cancel row
        time_cancel_layout = QHBoxLayout()

        # Time info label
        self.time_label = QLabel("Elapsed: 0s | ETA: --")
        self.time_label.setStyleSheet("color: #666; font-size: 8pt;")
        time_cancel_layout.addWidget(self.time_label)

        time_cancel_layout.addStretch()

        # Cancel button
        self.cancel_processing_btn = QPushButton("❌ Cancel")
        self.cancel_processing_btn.setMaximumWidth(80)
        self.cancel_processing_btn.setMaximumHeight(25)
        self.cancel_processing_btn.clicked.connect(self._cancel_document_processing)
        self.cancel_processing_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        time_cancel_layout.addWidget(self.cancel_processing_btn)

        container_layout.addLayout(time_cancel_layout)

        progress_layout.addWidget(self.progress_container)

        return progress_widget

    def create_input_area(self) -> QWidget:
        """Create the input area widget - compact layout."""
        
        input_widget = QWidget()
        layout = QVBoxLayout(input_widget)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(3)
        
        # Options row - compact
        options_layout = QHBoxLayout()
        options_layout.setSpacing(5)
        
        # Web research checkbox
        self.web_research_cb = QCheckBox("🌐 Web")
        self.web_research_cb.setChecked(True)
        self.web_research_cb.setToolTip("Include information from internet in answers")
        options_layout.addWidget(self.web_research_cb)
        
        # Clear button - compact
        clear_btn = QPushButton("🗑️ Clear")
        clear_btn.setMaximumWidth(80)
        clear_btn.clicked.connect(self.clear_chat_history)
        options_layout.addWidget(clear_btn)
        
        options_layout.addStretch()
        
        layout.addLayout(options_layout)
        
        # Question input - compact
        input_layout = QHBoxLayout()
        input_layout.setSpacing(5)
        
        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("Ask a question...")
        self.question_input.returnPressed.connect(self.ask_question)
        self.question_input.setMaximumHeight(30)
        input_layout.addWidget(self.question_input)
        
        # Ask button - compact
        self.ask_button = QPushButton("📤 Ask")
        self.ask_button.clicked.connect(self.ask_question)
        self.ask_button.setDefault(True)
        self.ask_button.setMaximumWidth(80)
        self.ask_button.setMaximumHeight(30)
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
        Process a document for RAG (extract and index content) with enhanced progress tracking.

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

        # Cancel any existing processing
        if self.document_processor_worker and self.document_processor_worker.isRunning():
            self.document_processor_worker.cancel()
            self.document_processor_worker.wait(1000)

        # Generate document ID
        document_id = str(document_path.stem)

        # Show progress UI
        self.progress_container.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stage_label.setText("Starting...")
        self.detail_label.setText("")
        self.time_label.setText("Elapsed: 0s | ETA: --")

        # Create and start worker
        self.document_processor_worker = DocumentProcessorWorker(
            document_path=document_path,
            vector_store=self.vector_store,
            document_id=document_id
        )

        # Connect signals
        self.document_processor_worker.progress_updated.connect(self._on_document_progress)
        self.document_processor_worker.processing_completed.connect(self._on_document_completed)
        self.document_processor_worker.processing_failed.connect(self._on_document_failed)

        # Store document path for completion handler
        self.current_document_path = document_path

        # Start processing
        self.document_processor_worker.start()

        logger.info(f"Started document processing: {document_path}")

    def _on_document_progress(self, progress_data: Dict[str, Any]):
        """Handle document processing progress updates."""
        stage = progress_data.get('stage', '')
        progress = progress_data.get('progress', 0)
        message = progress_data.get('message', '')
        detail = progress_data.get('detail', '')
        elapsed = progress_data.get('elapsed_time', 0)
        eta = progress_data.get('eta', 0)

        # Update UI elements
        self.stage_label.setText(f"⚙️ {stage}")
        self.progress_bar.setValue(int(progress))
        self.detail_label.setText(message)

        # Format time display
        elapsed_str = f"{int(elapsed)}s"
        eta_str = f"{int(eta)}s" if eta > 0 else "--"
        self.time_label.setText(f"Elapsed: {elapsed_str} | ETA: {eta_str}")

        # Update status label if detail is provided
        if detail:
            self.status_label.setText(detail)
        else:
            self.status_label.setText(message)

    def _on_document_completed(self, document_id: str, num_chunks: int):
        """Handle successful document processing completion."""
        # Set current document
        if self.current_document_path:
            self.set_current_document(self.current_document_path, document_id)

        # Hide progress UI after a short delay
        QTimer.singleShot(2000, lambda: self.progress_container.setVisible(False))

        # Update status
        self.status_label.setText(
            f"✅ Document ready: {self.current_document_path.name} ({num_chunks} chunks)"
        )

        logger.info(f"Document processing completed: {document_id} ({num_chunks} chunks)")

    def _on_document_failed(self, error_message: str):
        """Handle document processing failure."""
        # Hide progress UI
        self.progress_container.setVisible(False)

        # Update status
        self.status_label.setText(f"❌ Processing failed")

        # Show error dialog
        QMessageBox.warning(self, "Processing Error", error_message)

        logger.error(f"Document processing failed: {error_message}")

    def _cancel_document_processing(self):
        """Cancel ongoing document processing."""
        if self.document_processor_worker and self.document_processor_worker.isRunning():
            # Request cancellation
            self.document_processor_worker.cancel()

            # Update UI
            self.stage_label.setText("⚠️ Cancelling...")
            self.cancel_processing_btn.setEnabled(False)

            # Wait for worker to finish
            QTimer.singleShot(1000, self._finalize_cancellation)

            logger.info("Document processing cancellation requested")

    def _finalize_cancellation(self):
        """Finalize cancellation and clean up UI."""
        if self.document_processor_worker:
            self.document_processor_worker.wait(2000)

        # Hide progress UI
        self.progress_container.setVisible(False)

        # Re-enable cancel button for next time
        self.cancel_processing_btn.setEnabled(True)

        # Update status
        self.status_label.setText("⚠️ Processing cancelled by user")

        logger.info("Document processing cancelled")

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
