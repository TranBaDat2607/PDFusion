"""
Custom widgets for the desktop PDF translator GUI.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QComboBox, QGroupBox, QTextEdit, QProgressBar, QFileDialog,
    QFrame, QScrollArea, QGridLayout, QSpinBox, QCheckBox,
    QSlider, QLineEdit, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QEvent
from PySide6.QtGui import QPixmap, QFont, QPalette, QImage, QPainter, QTextCursor
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class PDFGraphicsView(QGraphicsView):
    """Custom graphics view with zoom support."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAlignment(Qt.AlignCenter)
    
    def wheelEvent(self, event):
        """Handle mouse wheel events for zooming with Ctrl key."""
        if event.modifiers() == Qt.ControlModifier:
            # Zoom in/out with Ctrl + mouse wheel
            if event.angleDelta().y() > 0:
                self.parent().zoom_in()
            else:
                self.parent().zoom_out()
            event.accept()
        else:
            # Normal scrolling without Ctrl
            super().wheelEvent(event)


class PDFViewer(QWidget):
    """Simple PDF viewer widget with PyMuPDF rendering."""
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self.current_file: Optional[Path] = None
        self.doc = None
        self.current_page = 0
        self.zoom_factor = 1.0
        self.base_dpi = 150.0
    
    def _setup_ui(self):
        """Setup PDF viewer UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Graphics view for PDF content
        self.graphics_view = PDFGraphicsView(self)
        self.graphics_scene = QGraphicsScene()
        self.graphics_view.setScene(self.graphics_scene)
        layout.addWidget(self.graphics_view)
        
        # Control buttons
        button_layout = QHBoxLayout()
        
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(self.previous_page)
        self.prev_btn.setEnabled(False)
        
        self.page_label = QLabel("Page 1 of 1")
        self.page_label.setAlignment(Qt.AlignCenter)
        
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self.next_page)
        self.next_btn.setEnabled(False)
        
        self.zoom_in_btn = QPushButton("Zoom In +")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        
        self.zoom_out_btn = QPushButton("Zoom Out -")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        
        self.fit_width_btn = QPushButton("Fit Width")
        self.fit_width_btn.clicked.connect(self.fit_width)
        
        button_layout.addWidget(self.prev_btn)
        button_layout.addWidget(self.page_label)
        button_layout.addWidget(self.next_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.zoom_in_btn)
        button_layout.addWidget(self.zoom_out_btn)
        button_layout.addWidget(self.fit_width_btn)
        
        layout.addLayout(button_layout)
    
    def load_pdf(self, file_path: Path) -> bool:
        """Load PDF file for viewing."""
        try:
            if not file_path.exists():
                return False
            
            # Close previous document if open
            if self.doc:
                self.doc.close()
            
            self.current_file = file_path
            self.doc = fitz.open(file_path)
            self.current_page = 0
            self.zoom_factor = 1.0
            
            # Render first page
            self.render_page()
            
            # Update navigation buttons
            self.update_navigation()
            
            return True
            
        except Exception as e:
            logger.exception(f"Error loading PDF: {e}")
            # Show error in placeholder
            self.show_error(f"Error loading PDF: {e}")
            return False
    
    def render_page(self):
        """Render current page to the viewer with proper resolution based on zoom."""
        if not self.doc or self.current_page >= len(self.doc):
            return
        
        try:
            # Calculate DPI based on zoom factor
            render_dpi = self.base_dpi * self.zoom_factor
            
            # Get page
            page = self.doc[self.current_page]
            
            # Render page to pixmap with appropriate DPI
            mat = fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            
            # Convert to QImage
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            
            # Convert to QPixmap
            pixmap = QPixmap.fromImage(img)
            
            # Clean up
            pix = None
            
            # Display pixmap
            self.graphics_scene.clear()
            self.graphics_scene.addPixmap(pixmap)
            self.graphics_scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
            
            # Reset view to 1:1 scale (since we rendered at the correct resolution)
            self.graphics_view.resetTransform()
            
            # Update page label
            self.page_label.setText(f"Page {self.current_page + 1} of {len(self.doc)}")
            
        except Exception as e:
            logger.exception(f"Error rendering page: {e}")
            self.show_error(f"Error rendering page: {e}")
    
    def show_error(self, message: str):
        """Show error message in the viewer."""
        self.graphics_scene.clear()
        error_text = self.graphics_scene.addText(message)
        error_text.setDefaultTextColor(Qt.red)
    
    def next_page(self):
        """Go to next page."""
        if self.doc and self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.render_page()
            self.update_navigation()
    
    def previous_page(self):
        """Go to previous page."""
        if self.current_page > 0:
            self.current_page -= 1
            self.render_page()
            self.update_navigation()
    
    def update_navigation(self):
        """Update navigation button states."""
        if not self.doc:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < len(self.doc) - 1)
        self.page_label.setText(f"Page {self.current_page + 1} of {len(self.doc)}")
    
    def zoom_in(self):
        """Zoom in on the PDF."""
        self.zoom_factor *= 1.2
        self.render_page()
    
    def zoom_out(self):
        """Zoom out on the PDF."""
        self.zoom_factor /= 1.2
        if self.zoom_factor < 0.2:  # Minimum zoom level
            self.zoom_factor = 0.2
        self.render_page()
    
    def fit_width(self):
        """Fit the PDF to the width of the view."""
        if self.graphics_scene.items():
            pixmap_item = self.graphics_scene.items()[0]
            if isinstance(pixmap_item, QGraphicsPixmapItem):
                pixmap = pixmap_item.pixmap()
                if not pixmap.isNull():
                    view_width = self.graphics_view.viewport().width() - 20  # Some margin
                    current_width = pixmap.width()
                    self.zoom_factor = view_width / current_width
                    self.render_page()
    
    def clear(self):
        """Clear the PDF viewer."""
        self.current_file = None
        if self.doc:
            self.doc.close()
            self.doc = None
        self.graphics_scene.clear()
        self.page_label.setText("Page 1 of 1")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.zoom_factor = 1.0


class ProgressPanel(QWidget):
    """Panel for showing translation progress."""
    
    cancel_requested = Signal()
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self.is_active = False
    
    def _setup_ui(self):
        """Setup progress panel UI."""
        layout = QVBoxLayout(self)
        
        # Progress group
        progress_group = QGroupBox("Translation Progress")
        progress_layout = QVBoxLayout(progress_group)
        
        # Overall progress
        self.overall_progress = QProgressBar()
        self.overall_progress.setVisible(False)
        progress_layout.addWidget(self.overall_progress)
        
        # Status text
        self.status_text = QTextEdit()
        self.status_text.setMaximumHeight(150)
        self.status_text.setReadOnly(True)
        self.status_text.setVisible(False)
        progress_layout.addWidget(self.status_text)
        
        # Cancel button
        self.cancel_btn = QPushButton("❌ Cancel Translation")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_requested)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
        """)
        progress_layout.addWidget(self.cancel_btn)
        
        layout.addWidget(progress_group)
        layout.addStretch()
    
    def start_translation(self):
        """Start showing progress."""
        self.is_active = True
        self.overall_progress.setVisible(True)
        self.status_text.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.status_text.clear()
        self.status_text.append("Translation started...")
    
    def update_progress(self, event_data: Dict[str, Any]):
        """Update progress display."""
        if not self.is_active:
            return
        
        # Update progress bar
        progress = event_data.get('progress_percent', 0)
        self.overall_progress.setValue(int(progress))
        
        # Update status text
        message = event_data.get('message', '')
        if message:
            self.status_text.append(f"[{progress:.1f}%] {message}")
            
        # Auto-scroll to bottom
        cursor = self.status_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.status_text.setTextCursor(cursor)
    
    def complete_translation(self):
        """Mark translation as complete."""
        self.is_active = False
        self.overall_progress.setValue(100)
        self.status_text.append("✅ Translation completed successfully!")
        
        # Hide cancel button
        self.cancel_btn.setVisible(False)
        
        # Auto-hide after 5 seconds
        QTimer.singleShot(5000, self.reset)
    
    def reset(self):
        """Reset progress panel."""
        self.is_active = False
        self.overall_progress.setVisible(False)
        self.overall_progress.setValue(0)
        self.status_text.setVisible(False)
        self.status_text.clear()
        self.cancel_btn.setVisible(False)


class RAGChatPanel(QWidget):
    """Panel for RAG chat functionality with PDF documents."""
    
    def __init__(self):
        super().__init__()
        self._setup_ui()
        self.document_context_mode = False  # False = general, True = document-specific
        self.current_page_reference = None  # For future page reference functionality
    
    def _setup_ui(self):
        """Setup RAG chat panel UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Header with title and controls
        header_layout = QHBoxLayout()
        
        header = QLabel("💬 Chat with PDF")
        header.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                padding: 5px;
            }
        """)
        header_layout.addWidget(header)
        header_layout.addStretch()
        
        # Document context toggle button
        self.context_toggle_btn = QPushButton("🌐 General Context")
        self.context_toggle_btn.setCheckable(True)
        self.context_toggle_btn.setToolTip("Toggle between general and document-specific context")
        self.context_toggle_btn.clicked.connect(self.toggle_document_context)
        header_layout.addWidget(self.context_toggle_btn)
        
        # Clear chat button
        self.clear_btn = QPushButton("🗑️ Clear Chat")
        self.clear_btn.setToolTip("Clear conversation history")
        self.clear_btn.clicked.connect(self.clear_chat)
        header_layout.addWidget(self.clear_btn)
        
        layout.addLayout(header_layout)
        
        # Chat display area
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText(
            "Ask questions about your PDF document...\n\n"
            "Toggle 'Document Context' to switch between general and document-specific Q&A.\n"
            "Features:\n"
            "• Ask questions about the PDF content\n"  
            "• Get context-aware answers\n"
            "• Page-specific referencing (coming soon)"
        )
        layout.addWidget(self.chat_display)
        
        # Input area
        input_layout = QHBoxLayout()
        
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Type your question here...")
        self.chat_input.returnPressed.connect(self.send_message)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.chat_input, 1)
        input_layout.addWidget(self.send_btn)
        
        layout.addLayout(input_layout)
        
        # Status bar for context mode
        self.status_bar = QLabel("General context mode")
        self.status_bar.setStyleSheet("""
            QLabel {
                font-size: 12px;
                color: #666;
                padding: 2px;
            }
        """)
        layout.addWidget(self.status_bar)
    
    def toggle_document_context(self):
        """Toggle between general and document-specific context mode."""
        self.document_context_mode = not self.document_context_mode
        
        if self.document_context_mode:
            self.context_toggle_btn.setText("📄 Document Context")
            self.context_toggle_btn.setStyleSheet("background-color: #e3f2fd;")
            self.status_bar.setText("Document-specific context mode - Questions will relate to the current PDF")
        else:
            self.context_toggle_btn.setText("🌐 General Context")
            self.context_toggle_btn.setStyleSheet("")
            self.status_bar.setText("General context mode - Questions will be answered without PDF context")
        
        # Add a message to the chat to indicate the mode change
        mode_text = "Document-specific" if self.document_context_mode else "General"
        self.add_message(f"Context mode changed to: {mode_text}", "system")
    
    def send_message(self):
        """Send user message and get response."""
        message = self.chat_input.text().strip()
        if not message:
            return
            
        # Add user message to chat
        self.add_message(message, "user")
        
        # Clear input
        self.chat_input.clear()
        
        # Simulate AI response (to be implemented later)
        # In the future, this will connect to the actual RAG system
        self.simulate_ai_response(message)
    
    def simulate_ai_response(self, user_message):
        """Simulate AI response (to be replaced with actual implementation)."""
        if self.document_context_mode:
            response = f"I understand you're asking about the document. In a future implementation, I'll analyze your PDF and provide a specific answer to: '{user_message}'"
        else:
            response = f"In a future implementation, I'll provide a general answer to: '{user_message}'"
            
        # Add AI response to chat after a short delay
        QTimer.singleShot(500, lambda: self.add_message(response, "ai"))
    
    def add_message(self, message, sender):
        """Add a message to the chat display."""
        timestamp = time.strftime("%H:%M:%S")
        
        if sender == "user":
            formatted_message = f"[{timestamp}] 👤 You: {message}"
        elif sender == "ai":
            formatted_message = f"[{timestamp}] 🤖 AI: {message}"
        else:  # system
            formatted_message = f"[{timestamp}] ⚙️ System: {message}"
            
        self.chat_display.append(formatted_message)
        
        # Scroll to bottom
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.chat_display.setTextCursor(cursor)
    
    def clear_chat(self):
        """Clear the chat conversation."""
        self.chat_display.clear()
        self.add_message("Chat history cleared", "system")
    
    def set_page_reference(self, page_number):
        """
        Set the current page reference for questions (for future implementation).
        
        Args:
            page_number (int): The page number to reference
        """
        self.current_page_reference = page_number
        self.add_message(f"Page reference set to: {page_number}", "system")
        
        # This will be used in future implementation to provide page-specific context
        # For now, we just store the reference for later use


# Dialog classes will be implemented in separate files
from PySide6.QtWidgets import QDialog

class SettingsDialog(QDialog):
    """Settings configuration dialog."""
    
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumSize(500, 400)
        
        # Placeholder - implement full settings dialog
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Settings dialog will be implemented here"))
        
        # OK/Cancel buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def get_settings(self):
        """Get updated settings."""
        return self.settings


class AboutDialog(QDialog):
    """About dialog."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Desktop PDF Translator")
        self.setFixedSize(400, 300)
        
        layout = QVBoxLayout(self)
        
        # App info
        app_info = QLabel("""
        <h2>Desktop PDF Translator</h2>
        <p><b>Version:</b> 1.0.0</p>
        <p><b>Vietnamese Language Priority</b></p>
        
        <p>A desktop application for translating PDF documents while preserving formatting.</p>
        
        <p><b>Supported Services:</b></p>
        <ul>
        <li>OpenAI GPT Models</li>
        <li>Google Gemini</li>
        </ul>
        
        <p><b>Future Features:</b></p>
        <ul>
        <li>RAG Chat Integration</li>
        <li>Batch Processing</li>
        <li>Additional Translation Services</li>
        </ul>
        """)
        app_info.setWordWrap(True)
        layout.addWidget(app_info)
        
        # OK button
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        layout.addWidget(ok_btn)