"""
Expandable section widget for showing collapsible content.
Used for PDF chunks, web links, and paper references.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class ExpandableSection(QFrame):
    """
    Expandable section that shows/hides content when clicked.
    """

    item_clicked = Signal(str, dict)  # (item_type, item_data) - for navigation

    def __init__(self, title: str, items: List[Dict[str, Any]], item_type: str):
        """
        Initialize expandable section.

        Args:
            title: Section title (e.g., "PDF Search (5 chunks)")
            items: List of items to show when expanded
            item_type: Type of items ('pdf', 'web', 'deep_search')
        """
        super().__init__()
        self.title = title
        self.items = items
        self.item_type = item_type
        self.is_expanded = False

        self.setup_ui()

    def setup_ui(self):
        """Setup the expandable section UI."""
        self.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header (clickable to expand/collapse)
        self.header_btn = QPushButton(f"▶ {self.title}")
        self.header_btn.setFlat(True)
        self.header_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.header_btn.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 6px 10px;
                background-color: transparent;
                border: none;
                font-size: 8pt;
                font-weight: bold;
                color: #495057;
            }
            QPushButton:hover {
                background-color: #e9ecef;
            }
        """)
        self.header_btn.clicked.connect(self.toggle)
        layout.addWidget(self.header_btn)

        # Content container (hidden by default)
        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(10, 4, 10, 8)
        self.content_layout.setSpacing(3)

        # Add items based on type
        self._populate_items()

        self.content_container.setVisible(False)
        layout.addWidget(self.content_container)

    def _populate_items(self):
        """Populate items based on type."""
        if self.item_type == 'pdf':
            # PDF chunks - show text preview
            for item in self.items[:10]:  # Limit to 10
                chunk_widget = self._create_pdf_chunk_item(item)
                self.content_layout.addWidget(chunk_widget)

        elif self.item_type == 'web':
            # Web sources - show clickable links
            for item in self.items[:10]:  # Limit to 10
                link_widget = self._create_web_link_item(item)
                self.content_layout.addWidget(link_widget)

        elif self.item_type == 'deep_search':
            # Papers - show titles with links
            for item in self.items[:20]:  # Limit to 20
                paper_widget = self._create_paper_item(item)
                self.content_layout.addWidget(paper_widget)

    def _create_pdf_chunk_item(self, item: Dict[str, Any]) -> QWidget:
        """Create widget for PDF chunk with text preview."""
        widget = QLabel()

        page = item.get('page', 'N/A')
        text = item.get('text', item.get('content', ''))[:150]  # Preview 150 chars

        widget.setText(f"Page {page}: {text}...")
        widget.setWordWrap(True)
        widget.setStyleSheet("""
            QLabel {
                color: #666;
                font-size: 8pt;
                padding: 4px;
                background-color: white;
                border-radius: 2px;
            }
        """)
        widget.setCursor(QCursor(Qt.PointingHandCursor))
        widget.setToolTip(f"Click to navigate to page {page}")

        # Make clickable with proper event handling
        def on_click(event):
            self.item_clicked.emit('pdf', item)
            event.accept()

        widget.mousePressEvent = on_click

        return widget

    def _create_web_link_item(self, item: Dict[str, Any]) -> QWidget:
        """Create widget for web link."""
        widget = QLabel()

        title = item.get('title', 'Web Source')
        url = item.get('url', item.get('link', ''))
        source_type = item.get('source_type', item.get('source', 'web'))

        widget.setText(f"[{source_type}] {title}")
        widget.setWordWrap(True)
        widget.setStyleSheet("""
            QLabel {
                color: #0066cc;
                font-size: 8pt;
                padding: 4px;
                background-color: white;
                border-radius: 2px;
                text-decoration: underline;
            }
            QLabel:hover {
                color: #0052a3;
            }
        """)
        widget.setCursor(QCursor(Qt.PointingHandCursor))
        widget.setToolTip(f"Click to open: {url}")

        # Make clickable
        widget.mousePressEvent = lambda e: self.item_clicked.emit('web', item)

        return widget

    def _create_paper_item(self, item: Dict[str, Any]) -> QWidget:
        """Create widget for academic paper."""
        widget = QLabel()

        paper_type = item.get('type', 'academic')
        title = item.get('title', 'Unknown Paper')
        authors = item.get('authors', '')
        year = item.get('year', '')
        source = item.get('source', 'unknown')
        url = item.get('url', '')

        if paper_type == 'local':
            # Local paper from ChromaDB
            text = f"[Local] {title}"
            color = "#495057"
        else:
            # Academic paper
            text = f"[{source}] {title}"
            if authors or year:
                text += f" ({authors[:50]}{'...' if len(authors) > 50 else ''}, {year})"
            color = "#0066cc" if url else "#495057"

        widget.setText(text)
        widget.setWordWrap(True)
        widget.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 8pt;
                padding: 4px;
                background-color: white;
                border-radius: 2px;
                {'text-decoration: underline;' if url else ''}
            }}
            QLabel:hover {{
                background-color: #f0f0f0;
            }}
        """)

        if url:
            widget.setCursor(QCursor(Qt.PointingHandCursor))
            widget.setToolTip(f"Click to open: {url}")

            def on_click(event):
                self.item_clicked.emit('web', {'url': url})
                event.accept()

            widget.mousePressEvent = on_click

        return widget

    def toggle(self):
        """Toggle expanded/collapsed state."""
        self.is_expanded = not self.is_expanded
        self.content_container.setVisible(self.is_expanded)

        # Update arrow
        arrow = "▼" if self.is_expanded else "▶"
        self.header_btn.setText(f"{arrow} {self.title}")
