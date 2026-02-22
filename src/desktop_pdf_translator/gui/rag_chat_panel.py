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
    QPushButton, QLabel, QScrollArea, QFrame,
    QGroupBox, QProgressBar, QCheckBox,
    QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QTextCursor, QTextCharFormat, QColor, QIcon


import qtawesome as qta


from ..rag import (
    ScientificPDFProcessor, ChromaDBManager,
    WebResearchEngine, EnhancedRAGChain, ReferenceManager
)
from ..config import get_settings
from .expandable_section import ExpandableSection
from .content_renderer import ContentRenderer
from .animations import FadeSlideInAnimation
from .chat_preferences import get_chat_preferences

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
    action_started = Signal(str, str)  # (action_type, description)
    action_completed = Signal(str)  # result
    action_failed = Signal(str)  # error message

    def __init__(self, rag_chain: EnhancedRAGChain, question: str,
                 document_id: Optional[str] = None, include_web: bool = True,
                 use_deep_search: bool = False):
        super().__init__()
        self.rag_chain = rag_chain
        self.question = question
        self.document_id = document_id
        self.include_web = include_web
        self.use_deep_search = use_deep_search

    def run(self):
        """Run RAG processing in background thread."""
        try:
            # Create event loop for async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Determine search type and log initial action
            if self.use_deep_search:
                self.action_started.emit("deep_search", "Starting Deep Search with multi-hop reasoning")
                self.progress_updated.emit("Starting Deep Search...", 5)
            elif self.include_web:
                self.action_started.emit("rag", "Starting RAG with web research enabled")
                self.progress_updated.emit("Starting RAG with web research...", 5)
            else:
                self.action_started.emit("rag", "Starting RAG search (PDF only)")
                self.progress_updated.emit("Starting RAG search...", 5)

            # Progress callback for deep search
            def progress_callback(data):
                if self.use_deep_search:
                    hop = data.get('hop', 0)
                    stage = data.get('hop_stage', '')
                    progress = data.get('progress', 0)
                    papers_found = data.get('papers_found', 0)

                    if stage == 'local_search':
                        self.action_started.emit("local", "Searching local ChromaDB for relevant past papers")
                        self.progress_updated.emit("Searching local knowledge base...", 5)
                    elif stage == 'searching':
                        if hop == 0:
                            self.action_completed.emit("Local search complete")
                            self.action_started.emit("api", f"Hop {hop}: Querying PubMed, Semantic Scholar, CORE")
                        self.progress_updated.emit(f"Hop {hop}: Querying academic databases...", progress)
                    elif stage == 'fetching':
                        self.action_completed.emit(f"Found {papers_found} papers")
                        self.action_started.emit("hop", f"Hop {hop}: Fetching citations and paper details")
                        self.progress_updated.emit(f"Hop {hop}: Fetching paper details...", progress)
                    elif stage == 'analyzing':
                        self.progress_updated.emit(f"Hop {hop}: Analyzing papers...", progress)
                    elif stage == 'synthesizing':
                        self.action_completed.emit(f"Multi-hop analysis complete")
                        self.action_started.emit("synthesis", "Synthesizing answer from all sources")
                        self.progress_updated.emit("Synthesizing comprehensive answer...", progress)
                    elif stage == 'completed':
                        self.action_completed.emit(f"Synthesis complete")
                    else:
                        self.progress_updated.emit(f"Hop {hop}: Processing...", progress)

            # Log actions for standard RAG
            if not self.use_deep_search:
                self.action_started.emit("search", "Generating hypothetical answer (HyDE)")
                self.progress_updated.emit("Generating search queries...", 10)

                self.action_completed.emit("HyDE query generated")
                self.action_started.emit("local", "Hybrid search: semantic + keyword matching")
                self.progress_updated.emit("Searching in PDF...", 30)

                # Log web research if enabled
                if self.include_web:
                    self.action_completed.emit("PDF search complete")
                    self.action_started.emit("web", "Searching Google, Scholar, Wikipedia, arXiv")

            # Process the question
            result = loop.run_until_complete(
                self.rag_chain.answer_question(
                    question=self.question,
                    document_id=self.document_id,
                    include_web_research=self.include_web,
                    use_deep_search=self.use_deep_search,
                    progress_callback=progress_callback if self.use_deep_search else None
                )
            )

            # Final completion
            if not self.use_deep_search:
                if self.include_web:
                    self.action_completed.emit("Web research complete")
                else:
                    self.action_completed.emit("PDF search complete")

                self.action_started.emit("synthesis", "Generating final answer with LLM")

            self.action_completed.emit(f"Answer generated successfully")

            self.progress_updated.emit("Completed", 100)
            self.answer_ready.emit(result)

        except Exception as e:
            logger.error(f"RAG processing failed: {e}")
            self.action_failed.emit(str(e))
            self.error_occurred.emit(str(e))
        finally:
            if 'loop' in locals():
                loop.close()


class MessageBubble(QWidget):
    """Compact message bubble for user questions (max 75% width, right-aligned)."""

    def __init__(self, question: str):
        super().__init__()
        self.question = question
        self.setup_ui()

    def setup_ui(self):
        """Setup the compact user message bubble with dynamic width."""
        from PySide6.QtWidgets import QSizePolicy

        # Main layout with right alignment
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 4)

        # Add stretch to push bubble to the right
        main_layout.addStretch()

        # Bubble frame - simple minimal style with dynamic width
        self.bubble_frame = QFrame()
        self.bubble_frame.setObjectName("userBubble")
        self.bubble_frame.setStyleSheet("""
            QFrame#userBubble {
                background-color: #F5F5F5;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
            }
        """)

        self.bubble_frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        bubble_layout = QVBoxLayout(self.bubble_frame)
        bubble_layout.setContentsMargins(14, 10, 14, 10)

        # Question text (no header, clean bubble)
        self.question_label = QLabel(self.question)
        self.question_label.setWordWrap(True)
        self.question_label.setFont(QFont("Segoe UI", 9))
        self.question_label.setStyleSheet("color: #424242; background: transparent; border: none;")
        self.question_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.question_label.setMinimumWidth(100)  # Minimum readable width

        bubble_layout.addWidget(self.question_label)

        main_layout.addWidget(self.bubble_frame)

    def showEvent(self, event):
        """Adjust bubble width when widget is shown and parent size is known."""
        super().showEvent(event)

        if not self.parent():
            return

        scroll_area = self.parent().parent()
        if scroll_area and hasattr(scroll_area, 'viewport'):
            available_width = scroll_area.viewport().width()
        else:
            available_width = self.parent().width()

        if available_width <= 0:
            return

        # 14px left + 14px right from layout margins
        h_padding = 28
        max_bubble_width = int(available_width * 0.75)

        # Compute the natural single-line text width so the HBoxLayout+stretch
        # cannot squeeze the frame below the text's actual needed width.
        fm = QFontMetrics(self.question_label.font())
        natural_width = fm.horizontalAdvance(self.question) + h_padding
        natural_width = max(natural_width, 100 + h_padding)  # respect label's minWidth
        natural_width = min(natural_width, max_bubble_width)  # cap at 75%

        # Constrain the frame (not the label) so the layout accounts for padding
        self.bubble_frame.setMinimumWidth(natural_width)
        self.bubble_frame.setMaximumWidth(max_bubble_width)


class MessagePanel(QFrame):
    """Wide message panel for assistant responses (95% width, left-aligned)."""

    reference_clicked = Signal(str, dict)  # (type, reference_data)

    def __init__(self, answer_data: Dict[str, Any], reference_manager: ReferenceManager):
        super().__init__()
        self.answer_data = answer_data
        self.reference_manager = reference_manager
        self.content_renderer = ContentRenderer()  # Initialize content renderer
        self.setup_ui()

    def setup_ui(self):
        """Setup the assistant message panel with dynamic width."""
        from PySide6.QtWidgets import QSizePolicy

        self.setObjectName("assistantPanel")
        self.setStyleSheet("""
            QFrame#assistantPanel {
                background-color: #F5F5F5;
                border-left: 4px solid #4CAF50;
                border-top: 1px solid #E0E0E0;
                border-right: 1px solid #E0E0E0;
                border-bottom: 1px solid #E0E0E0;
                border-radius: 8px;
                margin: 8px 0px;
            }
        """)

        # Set size policy for dynamic sizing - Preferred allows natural sizing
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        # Don't set fixed maximum width here - will be set in showEvent
        self.setMinimumWidth(200)  # Minimum readable width

        # Main layout with proper margins - simplified to show only content
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(0)

        # CONTENT SECTION ONLY - no references, no metadata
        content_container = self._create_content_section()
        main_layout.addWidget(content_container)

    def showEvent(self, event):
        """Adjust maximum width when widget is shown and parent size is known."""
        super().showEvent(event)

        # Now we can safely get parent width and set reasonable maximum
        if self.parent():
            # Get the scroll area's viewport width
            scroll_area = self.parent().parent()
            if scroll_area and hasattr(scroll_area, 'viewport'):
                available_width = scroll_area.viewport().width()
            else:
                available_width = self.parent().width()

            # Set maximum to 95% of available width
            max_width = int(available_width * 0.95)
            self.setMaximumWidth(max_width)

            # Also update content browser maximum width
            if hasattr(self, 'content_browser'):
                self.content_browser.setMaximumWidth(max_width - 50)  # Account for margins


    def _create_content_section(self) -> QWidget:
        """Create the main content section with rendered answer."""
        from PySide6.QtWidgets import QTextBrowser, QSizePolicy

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        # Action summary - shows what tools were used
        action_summary = self._create_action_summary()
        if action_summary:
            content_layout.addWidget(action_summary)

        # Content browser
        self.content_browser = QTextBrowser()
        self.content_browser.setReadOnly(True)
        self.content_browser.setFrameStyle(QFrame.NoFrame)
        self.content_browser.setOpenExternalLinks(True)
        self.content_browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
            }
        """)

        # Render answer content with rich formatting
        answer_text = self.answer_data.get('answer', '')
        try:
            rendered_html = self.content_renderer.render(answer_text)
            self.content_browser.setHtml(rendered_html)
        except Exception as e:
            logger.error(f"Content rendering failed: {e}")
            self.content_browser.setPlainText(answer_text)

        # Set size policy - Preferred for natural sizing based on content
        self.content_browser.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        # Set word wrap mode to optimize for dynamic width
        from PySide6.QtGui import QTextOption
        self.content_browser.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)

        # Set minimum width only - maximum will be set in showEvent
        self.content_browser.setMinimumWidth(200)  # Minimum readable width

        # Disable scrollbars for clean look - content should fit naturally
        self.content_browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Dynamic height adjustment - scales to content
        self.content_browser.document().documentLayout().documentSizeChanged.connect(
            self._adjust_content_size
        )

        # Initial height calculation
        self._adjust_content_size()

        content_layout.addWidget(self.content_browser)

        return content_widget

    def _create_action_summary(self) -> Optional[QWidget]:
        """Create expandable summary showing PDF chunks, web links, and papers used."""
        search_type = self.answer_data.get('search_type', 'standard')
        pdf_references = self.answer_data.get('pdf_references', [])
        web_references = self.answer_data.get('web_references', [])

        # Container widget
        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(4)

        sections_added = 0

        # PDF Search section (expandable to show chunks)
        if pdf_references and search_type != 'deep_search':
            pdf_section = ExpandableSection(
                title=f"PDF Search ({len(pdf_references)} chunks)",
                items=pdf_references,
                item_type='pdf'
            )
            pdf_section.item_clicked.connect(lambda t, d: self.reference_clicked.emit(t, d))
            summary_layout.addWidget(pdf_section)
            sections_added += 1

        # Web Search section (expandable to show links)
        if web_references and search_type != 'deep_search':
            web_section = ExpandableSection(
                title=f"Web Search ({len(web_references)} sources)",
                items=web_references,
                item_type='web'
            )
            web_section.item_clicked.connect(lambda t, d: self.reference_clicked.emit(t, d))
            summary_layout.addWidget(web_section)
            sections_added += 1

        # Deep Search section (expandable to show papers)
        if search_type == 'deep_search':
            quality_metrics = self.answer_data.get('quality_metrics', {})
            total_papers = quality_metrics.get('total_papers', 0)
            total_hops = quality_metrics.get('total_hops', 0)

            # Combine local papers (pdf_references) + academic papers (web_references)
            all_papers = []

            # Local papers from ChromaDB
            if pdf_references:
                for ref in pdf_references:
                    all_papers.append({
                        'type': 'local',
                        'title': ref.get('title', 'Local Paper'),
                        'source': ref.get('source', 'ChromaDB')
                    })

            # Academic papers from APIs
            if web_references:
                for ref in web_references:
                    all_papers.append({
                        'type': 'academic',
                        'title': ref.get('title', 'Unknown'),
                        'authors': ref.get('authors', ''),
                        'year': ref.get('year', ''),
                        'source': ref.get('source', 'unknown'),
                        'url': ref.get('url', '')
                    })

            deep_section = ExpandableSection(
                title=f"Deep Search ({total_papers} papers, {total_hops} hops)",
                items=all_papers,
                item_type='deep_search'
            )
            deep_section.item_clicked.connect(lambda t, d: self.reference_clicked.emit(t, d))
            summary_layout.addWidget(deep_section)
            sections_added += 1

        return summary_widget if sections_added > 0 else None

    def _adjust_content_size(self):
        """Dynamically adjust content browser size to fit document height."""
        if hasattr(self, 'content_browser'):
            # Get document size
            doc_size = self.content_browser.document().size()
            doc_height = doc_size.height()

            # Vertical: Set height to fit content exactly
            preferred_height = int(doc_height + 10)
            self.content_browser.setMinimumHeight(preferred_height)
            self.content_browser.setMaximumHeight(preferred_height)

            # Horizontal width is handled by showEvent - don't interfere here



class ReferenceWidget(QFrame):
    """Widget for displaying a single reference with enhanced preview and visual indicators."""

    reference_clicked = Signal(str, dict)  # (type, reference_data)

    def __init__(self, ref_type: str, reference_data: Dict[str, Any],
                 reference_manager: ReferenceManager):
        super().__init__()
        self.ref_type = ref_type
        self.reference_data = reference_data
        self.reference_manager = reference_manager

        # Get confidence/reliability score for color coding
        self.score = self._get_quality_score()

        self.setFrameStyle(QFrame.Box)
        self._apply_styling()

        self.setup_ui()
        self.setCursor(Qt.PointingHandCursor)

        # Setup hover tooltip with context preview
        self._setup_tooltip()

    def _get_quality_score(self) -> float:
        """Get quality score for this reference (not used anymore)."""
        return 0.5  # Default neutral score

    def _apply_styling(self):
        """Apply neutral styling to reference widget."""
        # Simplified styling without confidence-based color coding
        if self.ref_type == 'pdf':
            indicator_color = "#2196F3"  # Blue for PDF
            bg_color = "#f5f9ff"
            hover_bg = "#e3f2fd"
        else:  # web
            indicator_color = "#FF9800"  # Orange for Web
            bg_color = "#fff8f0"
            hover_bg = "#fff3e0"

        border_color = "#E0E0E0"

        self.setStyleSheet(f"""
            ReferenceWidget {{
                border-left: 4px solid {indicator_color};
                border-top: 1px solid {border_color};
                border-right: 1px solid {border_color};
                border-bottom: 1px solid {border_color};
                border-radius: 5px;
                padding: 5px;
                margin: 2px;
                background-color: {bg_color};
            }}
            ReferenceWidget:hover {{
                background-color: {hover_bg};
                border-top: 2px solid {indicator_color};
                border-right: 2px solid {indicator_color};
                border-bottom: 2px solid {indicator_color};
            }}
        """)

    def _setup_tooltip(self):
        """Setup rich tooltip with context preview."""
        tooltip_parts = []

        # Add type indicator
        if self.ref_type == 'pdf':
            tooltip_parts.append("PDF Reference")
        elif self.ref_type == 'web':
            tooltip_parts.append("Web Reference")

        # Add source information
        if self.ref_type == 'pdf':
            page = self.reference_data.get('page', 'N/A')
            tooltip_parts.append(f"Page: {page}")

        elif self.ref_type == 'web':
            url = self.reference_data.get('url', '')
            if url:
                # Show domain only
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                tooltip_parts.append(f"Source: {domain}")

        # Add content preview (shorter - first 80 chars)
        content = self.reference_data.get('content', '') or self.reference_data.get('text', '')
        if content:
            preview = content[:80].strip()
            if len(content) > 80:
                preview += "..."
            tooltip_parts.append(f"\n{preview}")

        # Add click instruction
        tooltip_parts.append("\nClick for details")

        # Set the tooltip
        tooltip_text = "\n".join(tooltip_parts)
        self.setToolTip(tooltip_text)

    def setup_ui(self):
        """Setup the reference widget UI with enhanced visual indicators."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        # Simplified quality indicator - removed emoji, keep just the badge below

        # Main content area
        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)

        # Format reference text
        display_text = self.reference_manager.format_reference_for_display(
            self.ref_type, self.reference_data
        )

        # Main text with better height limit
        text_label = QLabel(display_text)
        text_label.setFont(QFont("Segoe UI", 9))
        text_label.setWordWrap(True)
        text_label.setMaximumHeight(80)  # Increased from 40 to 80
        content_layout.addWidget(text_label)

        # Simple badge without confidence
        badge_layout = QHBoxLayout()
        badge_layout.setSpacing(5)

        if self.ref_type == 'pdf':
            page = self.reference_data.get('page', 'N/A')

            # Page badge only
            page_badge = QLabel(f"Page {page}")
            page_badge.setStyleSheet("""
                background-color: #2196F3;
                color: white;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 8pt;
                font-weight: bold;
            """)
            badge_layout.addWidget(page_badge)

        elif self.ref_type == 'web':
            source_type = self.reference_data.get('source_type', 'web')

            # Source type badge only
            source_badge = QLabel(source_type.title())
            source_badge.setStyleSheet("""
                background-color: #FF9800;
                color: white;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 8pt;
                font-weight: bold;
            """)
            badge_layout.addWidget(source_badge)

        badge_layout.addStretch()
        content_layout.addLayout(badge_layout)

        layout.addLayout(content_layout, 1)  # Content takes most space

        # Arrow indicator for click action
        arrow_label = QLabel("→")
        arrow_label.setStyleSheet("color: #999; font-size: 14pt; font-weight: bold;")
        layout.addWidget(arrow_label)

    def enterEvent(self, event):
        """Handle mouse enter - could show preview popup."""
        super().enterEvent(event)
        # Future enhancement: Show preview dialog on hover

    def mousePressEvent(self, event):
        """Handle mouse click on reference."""
        if event.button() == Qt.LeftButton:
            self.reference_clicked.emit(self.ref_type, self.reference_data)
        super().mousePressEvent(event)


class ChatHistoryWidget(QScrollArea):
    """Widget for displaying chat history with references and performance optimization."""

    # Maximum messages to keep in view (for performance)
    MAX_VISIBLE_MESSAGES = 100

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Main widget and layout
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setAlignment(Qt.AlignTop)

        self.setWidget(self.content_widget)

        # Chat history (all messages)
        self.chat_items = []

        # Performance: Track archived messages (hidden from view)
        self.archived_items = []

        # Get preferences
        self.preferences = get_chat_preferences()
    
    def add_question(self, question: str):
        """Add a user question to the chat history using MessageBubble with animation."""

        # Hide empty state when first question is added
        if hasattr(self.parent(), 'empty_state_label'):
            self.parent().empty_state_label.setVisible(False)

        # Create compact message bubble (right-aligned, max 65% width)
        message_bubble = MessageBubble(question)

        self.content_layout.addWidget(message_bubble)
        self.chat_items.append(('question', message_bubble))

        # Apply fade + slide in animation (from right for user messages)
        if self.preferences.show_animations:
            QTimer.singleShot(10, lambda: FadeSlideInAnimation.apply(message_bubble, direction="right", duration=250))

        # Performance: Archive old messages if limit exceeded
        self._check_and_archive_messages()

        # Scroll to bottom
        if self.preferences.auto_scroll:
            QTimer.singleShot(150, self._scroll_to_bottom)
    
    def add_answer(self, answer_data: Dict[str, Any], reference_manager: ReferenceManager):
        """Add an answer with references to the chat history using MessagePanel with animation."""

        # Create wide message panel (left-aligned, 95% width)
        message_panel = MessagePanel(answer_data, reference_manager)
        message_panel.reference_clicked.connect(self._handle_reference_click)

        self.content_layout.addWidget(message_panel)
        self.chat_items.append(('answer', message_panel))

        # Apply fade + slide in animation (from left for assistant messages)
        if self.preferences.show_animations:
            QTimer.singleShot(10, lambda: FadeSlideInAnimation.apply(message_panel, direction="left", duration=300))

        # Performance: Archive old messages if limit exceeded
        self._check_and_archive_messages()

        # Scroll to bottom
        if self.preferences.auto_scroll:
            QTimer.singleShot(350, self._scroll_to_bottom)
    
    def _handle_reference_click(self, ref_type: str, reference_data: Dict[str, Any]):
        """Handle reference click - emit signal to parent."""
        self.parent().handle_reference_click(ref_type, reference_data)

    def _check_and_archive_messages(self):
        """Archive old messages if we exceed the maximum visible limit (for performance)."""
        if len(self.chat_items) > self.MAX_VISIBLE_MESSAGES:
            # Calculate how many to archive
            num_to_archive = len(self.chat_items) - self.MAX_VISIBLE_MESSAGES

            # Archive oldest messages
            for i in range(num_to_archive):
                msg_type, widget = self.chat_items[i]

                # Hide widget (but keep in memory)
                widget.setVisible(False)

                # Move to archived list
                self.archived_items.append((msg_type, widget))

            # Remove from active list
            self.chat_items = self.chat_items[num_to_archive:]

            logger.info(f"Archived {num_to_archive} old messages for performance")

    def _scroll_to_bottom(self):
        """Scroll to the bottom of the chat history."""
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def clear_history(self):
        """Clear all chat history including archived messages."""
        # Clear active messages
        for _, widget in self.chat_items:
            widget.deleteLater()
        self.chat_items.clear()

        # Clear archived messages
        for _, widget in self.archived_items:
            widget.deleteLater()
        self.archived_items.clear()

        # Show empty state again
        if hasattr(self.parent(), 'empty_state_label'):
            self.parent().empty_state_label.setVisible(True)

        logger.info("Chat history cleared (including archived messages)")


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

        # Initialize icons
        self.icons = self._init_icons()

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

    def _init_icons(self) -> Dict[str, QIcon]:
        """Initialize QtAwesome icons for reuse throughout the panel."""
        icons = {}

        # Primary colors
        primary_blue = '#2196F3'
        success_green = '#4CAF50'
        neutral_gray = '#666'

        # Button icons
        icons['web'] = qta.icon('fa5s.globe', color=primary_blue)
        icons['trash'] = qta.icon('fa5s.trash-alt', color='#f44336')
        icons['send'] = qta.icon('fa5s.paper-plane', color=primary_blue)
        icons['settings'] = qta.icon('fa5s.cog', color=neutral_gray)
        icons['robot'] = qta.icon('fa5s.robot', color=primary_blue)
        icons['question'] = qta.icon('fa5s.question-circle', color=primary_blue)
        icons['book'] = qta.icon('fa5s.book', color=neutral_gray)
        icons['check'] = qta.icon('fa5s.check-circle', color=success_green)
        icons['error'] = qta.icon('fa5s.exclamation-circle', color='#f44336')
        icons['cancel'] = qta.icon('fa5s.times-circle', color='#f44336')

        return icons
    
    def setup_ui(self):
        """Setup the chat panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # Header - compact
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel("AI Chat")
        title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        header_layout.addWidget(title_label)

        # Document indicator
        self.doc_label = QLabel("No document loaded")
        self.doc_label.setStyleSheet("color: #666; font-size: 8pt; font-style: italic;")
        header_layout.addWidget(self.doc_label)

        header_layout.addStretch()

        # Settings button (disabled until implemented)
        settings_btn = QPushButton()
        settings_btn.setIcon(self.icons.get('settings'))
        settings_btn.setMaximumSize(28, 28)
        settings_btn.setEnabled(False)
        settings_btn.setToolTip("Settings (coming soon)")
        header_layout.addWidget(settings_btn)
        
        layout.addLayout(header_layout)
        
        # Chat history area - chiếm phần lớn không gian
        self.chat_history = ChatHistoryWidget()
        layout.addWidget(self.chat_history, stretch=1)

        # Enhanced empty state message (shown when no chat history)
        self.empty_state_label = QLabel()
        self.empty_state_label.setText(
            "<div style='text-align: center;'>"
            "<span style='font-size: 32pt; color: #2196F3;'>💬</span><br><br>"
            "<b style='font-size: 12pt; color: #333;'>Welcome to AI Chat!</b><br><br>"
            "<span style='color: #666; font-size: 10pt;'>"
            "Ask questions about your PDF documents with AI-powered assistance.<br>"
            "Get comprehensive answers with references and citations.<br><br>"
            "</span>"
            "<span style='color: #2196F3; font-size: 9pt;'>"
            "• Markdown & LaTeX support<br>"
            "• Code highlighting<br>"
            "• Table & formula rendering<br>"
            "• Web research integration<br><br>"
            "</span>"
            "<i style='color: #999; font-size: 9pt;'>Load a PDF and start asking questions using the quick actions below</i>"
            "</div>"
        )
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setStyleSheet("""
            QLabel {
                padding: 60px 40px;
                background-color: #FAFAFA;
                border: 2px dashed #E0E0E0;
                border-radius: 10px;
                margin: 20px;
            }
        """)
        self.empty_state_label.setTextFormat(Qt.RichText)
        self.chat_history.content_layout.addWidget(self.empty_state_label)
        
        # Input area - compact, chiếm ít không gian
        input_widget = self.create_input_area()
        layout.addWidget(input_widget)

        # Unified progress section - used for both document processing and Q&A
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
        self.stage_label.setStyleSheet("color: #2196F3; font-weight: bold;")
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
        self.cancel_processing_btn = QPushButton(" Cancel")
        self.cancel_processing_btn.setIcon(qta.icon('fa5s.times-circle', color='white'))
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
        """Create the input area widget with quick actions and keyboard shortcuts."""

        input_widget = QWidget()
        layout = QVBoxLayout(input_widget)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)

        # Options row - compact
        options_layout = QHBoxLayout()
        options_layout.setSpacing(5)

        # Web research checkbox - unchecked by default (user opts in)
        self.web_research_cb = QCheckBox(" Web Research")
        self.web_research_cb.setIcon(self.icons.get('web'))
        self.web_research_cb.setChecked(False)  # Default OFF - PDF only
        self.web_research_cb.setToolTip("Enable web search to supplement PDF content (may be slower)")
        options_layout.addWidget(self.web_research_cb)

        # Deep Search button - prominent purple styling
        self.deep_search_btn = QPushButton(" Deep Search")
        try:
            import qtawesome as qta
            self.deep_search_btn.setIcon(qta.icon('fa5s.search-plus', color='#9C27B0'))
        except:
            pass  # Fall back to no icon if qtawesome not available
        self.deep_search_btn.setMaximumHeight(25)
        self.deep_search_btn.setToolTip("Deep search across academic databases (Ctrl+D)")
        self.deep_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #E1BEE7;
                border: 2px solid #9C27B0;
                border-radius: 4px;
                padding: 3px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #CE93D8;
            }
        """)
        self.deep_search_btn.setCheckable(True)  # Toggle button
        self.deep_search_btn.setChecked(False)  # Default OFF
        self.deep_search_btn.clicked.connect(self._toggle_deep_search_mode)
        options_layout.addWidget(self.deep_search_btn)

        # Clear button - compact
        clear_btn = QPushButton(" Clear")
        clear_btn.setIcon(self.icons.get('trash'))
        clear_btn.setMaximumWidth(80)
        clear_btn.setToolTip("Clear chat history (Ctrl+L)")
        clear_btn.clicked.connect(self.clear_chat_history)
        options_layout.addWidget(clear_btn)

        options_layout.addStretch()

        layout.addLayout(options_layout)

        # Question input - compact
        input_layout = QHBoxLayout()
        input_layout.setSpacing(5)

        self.question_input = QLineEdit()
        self.question_input.setPlaceholderText("Ask a question about the document...")
        self.question_input.returnPressed.connect(self.ask_question)
        self.question_input.setMaximumHeight(30)
        input_layout.addWidget(self.question_input)

        # Ask button - compact
        self.ask_button = QPushButton(" Ask")
        self.ask_button.setIcon(self.icons.get('send'))
        self.ask_button.clicked.connect(self.ask_question)
        self.ask_button.setDefault(True)
        self.ask_button.setMaximumWidth(80)
        self.ask_button.setMaximumHeight(30)
        self.ask_button.setToolTip("Send question (Enter or Ctrl+Enter)")
        input_layout.addWidget(self.ask_button)

        layout.addLayout(input_layout)

        # Setup keyboard shortcuts
        self._setup_keyboard_shortcuts()

        return input_widget

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for the chat panel."""
        from PySide6.QtGui import QShortcut, QKeySequence

        # Ctrl+Enter: Send question (alternative to Return)
        shortcut_send = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut_send.activated.connect(self.ask_question)

        # Ctrl+K: Clear input field
        shortcut_clear_input = QShortcut(QKeySequence("Ctrl+K"), self)
        shortcut_clear_input.activated.connect(lambda: self.question_input.clear())

        # Ctrl+L: Clear chat history
        shortcut_clear_history = QShortcut(QKeySequence("Ctrl+L"), self)
        shortcut_clear_history.activated.connect(self.clear_chat_history)

        # Ctrl+D: Toggle Deep Search
        shortcut_deep_search = QShortcut(QKeySequence("Ctrl+D"), self)
        shortcut_deep_search.activated.connect(lambda: self.deep_search_btn.setChecked(not self.deep_search_btn.isChecked()))
        shortcut_deep_search.activated.connect(self._toggle_deep_search_mode)

        logger.info("Keyboard shortcuts configured")

    def _show_keyboard_shortcuts(self):
        """Display keyboard shortcuts help dialog."""
        shortcuts_text = """
<b>Keyboard Shortcuts:</b><br><br>

<b>Input Controls:</b><br>
• <code>Enter</code> or <code>Ctrl+Enter</code> - Send question<br>
• <code>Ctrl+K</code> - Clear input field<br>
• <code>Ctrl+L</code> - Clear chat history<br>
        """

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Keyboard Shortcuts")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(shortcuts_text)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.exec()

    def _toggle_deep_search_mode(self):
        """Toggle deep search mode on/off."""
        is_enabled = self.deep_search_btn.isChecked()

        if is_enabled:
            # Enable deep search mode - active purple styling
            self.deep_search_btn.setStyleSheet("""
                QPushButton {
                    background-color: #9C27B0;
                    color: white;
                    border: 2px solid #7B1FA2;
                    border-radius: 4px;
                    padding: 3px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #8E24AA;
                }
            """)
            self.question_input.setPlaceholderText(
                "Ask a research question - Deep Search will explore academic papers..."
            )
            self.status_label.setText("Deep Search mode enabled - searches across multiple papers")
            logger.info("Deep Search mode enabled")
        else:
            # Disable deep search mode - light purple styling
            self.deep_search_btn.setStyleSheet("""
                QPushButton {
                    background-color: #E1BEE7;
                    border: 2px solid #9C27B0;
                    border-radius: 4px;
                    padding: 3px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #CE93D8;
                }
            """)
            self.question_input.setPlaceholderText("Ask a question about the document...")
            self.status_label.setText("Deep Search mode disabled")
            logger.info("Deep Search mode disabled")


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

        # Update document indicator in header
        self.doc_label.setText(f"{document_path.name}")
        self.doc_label.setStyleSheet("color: #2196F3; font-size: 8pt; font-weight: bold;")

        self.status_label.setText(f"Ready to answer questions about {document_path.name}")
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
            f"Document ready: {self.current_document_path.name} ({num_chunks} chunks)"
        )

        logger.info(f"Document processing completed: {document_id} ({num_chunks} chunks)")

    def _on_document_failed(self, error_message: str):
        """Handle document processing failure."""
        # Hide progress UI
        self.progress_container.setVisible(False)

        # Update status
        self.status_label.setText(f"Processing failed")

        # Show error dialog
        QMessageBox.warning(self, "Processing Error", error_message)

        logger.error(f"Document processing failed: {error_message}")

    def _cancel_document_processing(self):
        """Cancel ongoing document processing."""
        if self.document_processor_worker and self.document_processor_worker.isRunning():
            # Request cancellation
            self.document_processor_worker.cancel()

            # Update UI
            self.stage_label.setText("Cancelling...")
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
        self.status_label.setText("Processing cancelled by user")

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

        # Always use current document scope (no scope selection)
        document_id = self.current_document_id

        # Check if deep search is enabled
        use_deep_search = self.deep_search_btn.isChecked()

        # Start processing
        self._start_rag_processing(question, document_id, self.web_research_cb.isChecked(), use_deep_search)
    
    
    def _start_rag_processing(self, question: str, document_id: Optional[str],
                             include_web: bool, use_deep_search: bool = False):
        """Start RAG processing in background thread."""

        # Disable input during processing
        self.ask_button.setEnabled(False)
        self.question_input.setEnabled(False)

        # Show unified progress container (same as doc processing)
        self.progress_container.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.cancel_processing_btn.setVisible(False)  # No cancel for quick Q&A

        # Start worker thread
        self.rag_worker = RAGWorker(self.rag_chain, question, document_id, include_web, use_deep_search)
        self.rag_worker.answer_ready.connect(self._handle_answer_ready)
        self.rag_worker.error_occurred.connect(self._handle_error)
        self.rag_worker.progress_updated.connect(self._update_qa_progress)
        # Connect action log signals
        self.rag_worker.action_started.connect(self._handle_action_started)
        self.rag_worker.action_completed.connect(self._handle_action_completed)
        self.rag_worker.action_failed.connect(self._handle_action_failed)
        self.rag_worker.start()
    
    def _handle_answer_ready(self, answer_data: Dict[str, Any]):
        """Handle when RAG processing is complete."""

        # Add answer to chat history
        self.chat_history.add_answer(answer_data, self.reference_manager)

        # Clear input on success
        self.question_input.clear()

        # Check search type and update status
        search_type = answer_data.get('search_type', 'standard')
        sources_used = answer_data.get('sources_used', {})
        processing_time = answer_data.get('processing_time', 0)

        if search_type == 'deep_search':
            # Deep search completed
            quality_metrics = answer_data.get('quality_metrics', {})
            total_papers = quality_metrics.get('total_papers', 0)
            total_hops = quality_metrics.get('total_hops', 0)
            self.status_label.setText(f"Deep Search completed: {total_papers} papers, {total_hops} hops, {processing_time:.1f}s")
        else:
            # Standard RAG
            web_sources = sources_used.get('web_sources', 0)
            if self.web_research_cb.isChecked() and web_sources == 0:
                self.status_label.setText("Web research unavailable - using PDF only")
            else:
                total_sources = sources_used.get('pdf_sources', 0) + web_sources
                self.status_label.setText(f"Completed in {processing_time:.1f}s - {total_sources} sources")

        # Re-enable input
        self.ask_button.setEnabled(True)
        self.question_input.setEnabled(True)

        # Hide unified progress container
        QTimer.singleShot(500, lambda: self.progress_container.setVisible(False))

        logger.info(f"Answer generated successfully in {answer_data.get('processing_time', 0):.1f}s")
    
    def _handle_error(self, error_message: str):

        # Re-enable input (keep question for retry!)
        self.ask_button.setEnabled(True)
        self.question_input.setEnabled(True)

        # Hide unified progress container
        self.progress_container.setVisible(False)

        # Show error with retry instruction
        self.status_label.setText(f"Error - Question kept for retry")
        QMessageBox.warning(
            self,
            "Processing Error",
            f"Cannot answer question:\n{error_message}\n\nThe question is kept in the input field. Fix the issue and try again."
        )

        logger.error(f"RAG processing error: {error_message}")
    
    def _update_qa_progress(self, message: str, progress: int):
        """Update progress for Q&A processing (using unified progress UI)."""
        self.stage_label.setText(f"{message}")
        self.progress_bar.setValue(progress)
        self.detail_label.setText("Processing your question...")
        self.time_label.setText("")  # No ETA for quick Q&A

    def _handle_action_started(self, action_type: str, description: str):
        """Handle when a new action starts (for progress display only)."""
        pass  # Actions now shown as summary in answer bubble

    def _handle_action_completed(self, result: str):
        """Handle when an action completes (for progress display only)."""
        pass  # Actions now shown as summary in answer bubble

    def _handle_action_failed(self, error: str):
        """Handle when an action fails (for progress display only)."""
        pass  # Actions now shown as summary in answer bubble

    def handle_reference_click(self, ref_type: str, reference_data: Dict[str, Any]):
        """Handle reference click from chat history with visual feedback."""

        if ref_type == 'pdf':
            page = reference_data.get('page', 'N/A')
            self.status_label.setText(f"Navigating to page {page}...")

            pdf_ref = self.reference_manager.create_pdf_reference(reference_data)
            success = self.reference_manager.navigate_to_pdf_reference(pdf_ref)
            if success:
                # Emit signal for PDF navigation
                self.pdf_navigation_requested.emit(pdf_ref.page, pdf_ref.bbox)
                # Show success feedback
                QTimer.singleShot(1000, lambda: self.status_label.setText(f"Showing page {page}"))
            else:
                self.status_label.setText(f"Could not navigate to page {page}")

        elif ref_type == 'web':
            url = reference_data.get('url', '')
            from urllib.parse import urlparse
            domain = urlparse(url).netloc if url else 'link'
            self.status_label.setText(f"Opening {domain}...")

            web_ref = self.reference_manager.create_web_reference(reference_data)
            success = self.reference_manager.navigate_to_web_reference(web_ref)
            if success:
                # Emit signal for web navigation
                self.web_link_requested.emit(web_ref.url)
                # Show success feedback
                QTimer.singleShot(1000, lambda: self.status_label.setText(f"Opened {domain}"))
            else:
                self.status_label.setText(f"Could not open link")
    
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
        """Clear the chat history with confirmation."""
        # Ask for confirmation
        reply = QMessageBox.question(
            self,
            "Clear Chat History",
            "Are you sure you want to clear all chat history?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.chat_history.clear_history()
            if self.reference_manager:
                self.reference_manager.clear_history()
            self.status_label.setText("History cleared")
            logger.info("Chat history cleared by user")

    def set_rag_disabled_message(self):
        """Display message when RAG is disabled."""
        self.question_input.setEnabled(False)
        self.ask_button.setEnabled(False)
        self.web_research_cb.setEnabled(False)
        self.status_label.setText("RAG disabled")

    def set_rag_enabled_message(self):
        """Display message when RAG is re-enabled."""
        self.question_input.setEnabled(True)
        self.ask_button.setEnabled(True)
        self.web_research_cb.setEnabled(True)
        self.status_label.setText("RAG ready")

