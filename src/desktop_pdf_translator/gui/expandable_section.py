"""
Expandable section widget for showing collapsible content.
Used for PDF chunks, web links, and paper references.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from typing import List, Dict, Any
import logging

from qfluentwidgets import (
    CardWidget, TransparentPushButton, BodyLabel, CaptionLabel, FluentIcon,
)

logger = logging.getLogger(__name__)


class ExpandableSection(CardWidget):
    """Expandable card section that shows/hides content when clicked."""

    item_clicked = Signal(str, dict)

    def __init__(self, title: str, items: List[Dict[str, Any]], item_type: str):
        super().__init__()
        self.title = title
        self.items = items
        self.item_type = item_type
        self.is_expanded = False

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header_btn = TransparentPushButton(f"▶  {self.title}")
        self.header_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.header_btn.clicked.connect(self.toggle)
        layout.addWidget(self.header_btn)

        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(10, 4, 10, 8)
        self.content_layout.setSpacing(4)

        self._populate_items()

        self.content_container.setVisible(False)
        layout.addWidget(self.content_container)

    def _populate_items(self):
        if self.item_type == 'pdf':
            for item in self.items[:10]:
                self.content_layout.addWidget(self._create_pdf_chunk_item(item))
        elif self.item_type == 'web':
            for item in self.items[:10]:
                self.content_layout.addWidget(self._create_web_link_item(item))
        elif self.item_type == 'deep_search':
            for item in self.items[:20]:
                self.content_layout.addWidget(self._create_paper_item(item))

    def _create_pdf_chunk_item(self, item: Dict[str, Any]) -> QWidget:
        page = item.get('page', 'N/A')
        text = item.get('text', item.get('content', ''))[:150]

        widget = CaptionLabel(f"Page {page}: {text}...")
        widget.setWordWrap(True)
        widget.setCursor(QCursor(Qt.PointingHandCursor))
        widget.setToolTip(f"Click to navigate to page {page}")

        def on_click(event):
            self.item_clicked.emit('pdf', item)
            event.accept()

        widget.mousePressEvent = on_click
        return widget

    def _create_web_link_item(self, item: Dict[str, Any]) -> QWidget:
        title = item.get('title', 'Web Source')
        url = item.get('url', item.get('link', ''))
        source_type = item.get('source_type', item.get('source', 'web'))

        widget = BodyLabel(f"[{source_type}] {title}")
        widget.setWordWrap(True)
        widget.setStyleSheet("color: #0066cc; text-decoration: underline;")
        widget.setCursor(QCursor(Qt.PointingHandCursor))
        widget.setToolTip(f"Click to open: {url}")

        widget.mousePressEvent = lambda e: self.item_clicked.emit('web', item)
        return widget

    def _create_paper_item(self, item: Dict[str, Any]) -> QWidget:
        paper_type = item.get('type', 'academic')
        title = item.get('title', 'Unknown Paper')
        authors = item.get('authors', '')
        year = item.get('year', '')
        source = item.get('source', 'unknown')
        url = item.get('url', '')

        if paper_type == 'local':
            text = f"[Local] {title}"
            is_link = False
        else:
            text = f"[{source}] {title}"
            if authors or year:
                text += f" ({authors[:50]}{'...' if len(authors) > 50 else ''}, {year})"
            is_link = bool(url)

        widget = BodyLabel(text)
        widget.setWordWrap(True)
        if is_link:
            widget.setStyleSheet("color: #0066cc; text-decoration: underline;")
            widget.setCursor(QCursor(Qt.PointingHandCursor))
            widget.setToolTip(f"Click to open: {url}")

            def on_click(event):
                self.item_clicked.emit('web', {'url': url})
                event.accept()

            widget.mousePressEvent = on_click

        return widget

    def toggle(self):
        self.is_expanded = not self.is_expanded
        self.content_container.setVisible(self.is_expanded)
        arrow = "▼" if self.is_expanded else "▶"
        self.header_btn.setText(f"{arrow}  {self.title}")
