"""
Custom widgets for the desktop PDF translator GUI.
"""

import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QApplication
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage, QTextCursor
import fitz

from qfluentwidgets import (
    PushButton, PrimaryPushButton, TransparentToolButton,
    BodyLabel, StrongBodyLabel, CaptionLabel,
    ProgressBar, PlainTextEdit, MessageBoxBase, FluentIcon,
)

logger = logging.getLogger(__name__)


class PDFScrollArea(QScrollArea):
    """Custom scroll area with zoom support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pdf_viewer = parent

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            zoom_delta = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
            self.pdf_viewer.zoom_factor *= zoom_delta
            self.pdf_viewer.zoom_factor = max(0.2, min(self.pdf_viewer.zoom_factor, 5.0))
            self.pdf_viewer._update_all_pages()
            event.accept()
        else:
            super().wheelEvent(event)


class PDFViewer(QWidget):
    """PDF viewer widget with continuous vertical scrolling and lazy loading."""

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self.current_file: Optional[Path] = None
        self.doc = None
        self.zoom_factor = 1.0
        self.base_dpi = 150.0
        self.page_widgets = []
        self.is_rendering = False

        self.rendered_pages = set()
        self.page_heights = []
        self.render_buffer = 3

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = PDFScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setContentsMargins(10, 10, 10, 10)
        self.pages_layout.setSpacing(10)

        self.scroll_area.setWidget(self.pages_container)
        layout.addWidget(self.scroll_area)

        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)

        button_layout = QHBoxLayout()

        self.page_label = CaptionLabel("No PDF loaded")
        self.page_label.setAlignment(Qt.AlignCenter)

        self.zoom_in_btn = TransparentToolButton(FluentIcon.ZOOM_IN)
        self.zoom_in_btn.setToolTip("Zoom in")
        self.zoom_in_btn.clicked.connect(self.zoom_in)

        self.zoom_out_btn = TransparentToolButton(FluentIcon.ZOOM_OUT)
        self.zoom_out_btn.setToolTip("Zoom out")
        self.zoom_out_btn.clicked.connect(self.zoom_out)

        self.fit_width_btn = TransparentToolButton(FluentIcon.FIT_PAGE)
        self.fit_width_btn.setToolTip("Fit width")
        self.fit_width_btn.clicked.connect(self.fit_width)

        button_layout.addWidget(self.page_label)
        button_layout.addStretch()
        button_layout.addWidget(self.zoom_in_btn)
        button_layout.addWidget(self.zoom_out_btn)
        button_layout.addWidget(self.fit_width_btn)

        layout.addLayout(button_layout)

    def load_pdf(self, file_path: Path) -> bool:
        try:
            if not file_path.exists():
                return False

            if self.doc:
                self.doc.close()

            self.clear_pages()
            self.rendered_pages.clear()
            self.page_heights.clear()

            self.current_file = file_path
            self.doc = fitz.open(file_path)
            self.zoom_factor = 1.0

            self._calculate_page_dimensions()
            self._create_page_placeholders()

            self.page_label.setText(f"PDF loaded: {len(self.doc)} pages")

            QTimer.singleShot(200, self.fit_width)

            return True

        except Exception as e:
            logger.exception(f"Error loading PDF: {e}")
            self.show_error(f"Error loading PDF: {e}")
            return False

    def _calculate_page_dimensions(self):
        if not self.doc:
            return

        render_dpi = self.base_dpi * self.zoom_factor

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            page_rect = page.rect
            width = int(page_rect.width * render_dpi / 72.0)
            height = int(page_rect.height * render_dpi / 72.0)
            self.page_heights.append((width, height))

    def _create_page_placeholders(self):
        if not self.doc:
            return

        self.pages_container.setUpdatesEnabled(False)

        try:
            for page_num in range(len(self.doc)):
                if page_num < len(self.page_heights):
                    width, height = self.page_heights[page_num]
                else:
                    width, height = 600, 800

                page_label = QLabel()
                page_label.setMinimumSize(width, height)
                page_label.setMaximumSize(width, height)
                page_label.setAlignment(Qt.AlignCenter)
                page_label.setText(f"Page {page_num + 1}\nLoading...")
                page_label.setProperty("page_number", page_num)

                self.pages_layout.addWidget(page_label)
                self.page_widgets.append(page_label)

        finally:
            self.pages_container.setUpdatesEnabled(True)

    def _on_scroll(self):
        if not self.doc:
            return
        QTimer.singleShot(100, self._render_visible_pages)

    def _render_visible_pages(self):
        if not self.doc or self.is_rendering:
            return

        viewport = self.scroll_area.viewport()
        viewport_rect = viewport.rect()
        scroll_y = self.scroll_area.verticalScrollBar().value()

        visible_pages = []
        for page_num, page_label in enumerate(self.page_widgets):
            if page_label is None:
                continue

            widget_pos = page_label.pos()
            widget_height = page_label.height()
            widget_top = widget_pos.y() - scroll_y
            widget_bottom = widget_top + widget_height

            is_visible = (
                widget_bottom >= -viewport_rect.height() * self.render_buffer and
                widget_top <= viewport_rect.height() * (1 + self.render_buffer)
            )

            if is_visible and page_num not in self.rendered_pages:
                visible_pages.append(page_num)

        if visible_pages:
            self._render_pages(visible_pages)

    def _render_pages(self, page_numbers: list):
        if not self.doc or self.is_rendering:
            return

        self.is_rendering = True

        try:
            render_dpi = self.base_dpi * self.zoom_factor

            for page_num in page_numbers:
                if page_num >= len(self.doc) or page_num in self.rendered_pages:
                    continue

                page = self.doc[page_num]
                mat = fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(img)
                pix = None

                if page_num < len(self.page_widgets):
                    page_label = self.page_widgets[page_num]
                    page_label.setPixmap(pixmap)
                    page_label.setText("")
                    self.rendered_pages.add(page_num)

                if len(self.rendered_pages) % 2 == 0:
                    QApplication.processEvents()

        except Exception as e:
            logger.exception(f"Error rendering pages: {e}")
        finally:
            self.is_rendering = False

    def render_all_pages(self):
        if not self.doc:
            return
        self._render_visible_pages()

    def clear_pages(self):
        self.pages_container.setUpdatesEnabled(False)

        for widget in self.page_widgets:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

        while self.pages_layout.count():
            child = self.pages_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.page_widgets.clear()
        self.pages_container.setUpdatesEnabled(True)

    def show_error(self, message: str):
        self.clear_pages()
        error_label = StrongBodyLabel(message)
        error_label.setAlignment(Qt.AlignCenter)
        self.pages_layout.addWidget(error_label)

    def _update_all_pages(self):
        if not self.doc or self.is_rendering:
            return

        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() > 0:
            scroll_percentage = scrollbar.value() / scrollbar.maximum()
        else:
            scroll_percentage = 0

        self.rendered_pages.clear()
        self.page_heights.clear()
        self._calculate_page_dimensions()

        self.pages_container.setUpdatesEnabled(False)
        try:
            for page_num, page_label in enumerate(self.page_widgets):
                if page_num < len(self.page_heights):
                    width, height = self.page_heights[page_num]
                    page_label.setMinimumSize(width, height)
                    page_label.setMaximumSize(width, height)
                    page_label.clear()
                    page_label.setText(f"Page {page_num + 1}\nLoading...")
        finally:
            self.pages_container.setUpdatesEnabled(True)

        QTimer.singleShot(10, lambda: self._restore_scroll_position(scroll_percentage))
        QTimer.singleShot(50, self._render_visible_pages)

    def _restore_scroll_position(self, scroll_percentage):
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() > 0:
            new_value = int(scroll_percentage * scrollbar.maximum())
            scrollbar.setValue(new_value)

    def zoom_in(self):
        self.zoom_factor *= 1.2
        if self.zoom_factor > 5.0:
            self.zoom_factor = 5.0
        self._update_all_pages()

    def zoom_out(self):
        self.zoom_factor /= 1.2
        if self.zoom_factor < 0.2:
            self.zoom_factor = 0.2
        self._update_all_pages()

    def fit_width(self):
        if not self.doc or self.is_rendering:
            return

        try:
            page = self.doc[0]
            page_rect = page.rect
            view_width = self.scroll_area.viewport().width() - 60
            page_width_at_base_dpi = page_rect.width * (self.base_dpi / 72.0)
            target_zoom = view_width / page_width_at_base_dpi
            self.zoom_factor = max(0.2, min(target_zoom, 3.0))
            self._update_all_pages()

        except Exception as e:
            logger.exception(f"Error in fit_width: {e}")

    def goto_page(self, page_number: int):
        if not self.doc:
            logger.warning("Cannot navigate: No PDF loaded")
            return

        page_index = page_number - 1

        if page_index < 0 or page_index >= len(self.doc):
            logger.warning(f"Invalid page number: {page_number}")
            return

        try:
            if page_index not in self.rendered_pages:
                self._render_pages([page_index])

            if page_index < len(self.page_widgets):
                page_widget = self.page_widgets[page_index]
                self.scroll_area.ensureWidgetVisible(page_widget, 50, 50)
                self.page_label.setText(f"Page {page_number} of {len(self.doc)}")
                logger.info(f"Navigated to page {page_number}")
            else:
                logger.warning(f"Page widget not found for page {page_number}")

        except Exception as e:
            logger.error(f"Navigation to page {page_number} failed: {e}")

    def highlight_region(self, bbox: tuple):
        logger.info(f"Highlight region requested: {bbox}")

    def clear(self):
        self.current_file = None
        if self.doc:
            self.doc.close()
            self.doc = None
        self.clear_pages()
        self.page_label.setText("No PDF loaded")
        self.zoom_factor = 1.0


class ProgressPanel(QWidget):
    """Compact collapsible panel for showing translation progress details."""

    cancel_requested = Signal()

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self.is_active = False
        self.is_expanded = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.toggle_btn = PushButton("Show Details")
        self.toggle_btn.setMaximumWidth(140)
        self.toggle_btn.clicked.connect(self.toggle_details)
        header_layout.addWidget(self.toggle_btn)

        header_layout.addStretch()

        self.cancel_btn = PushButton("Cancel", icon=FluentIcon.CLOSE)
        self.cancel_btn.setMaximumWidth(100)
        self.cancel_btn.clicked.connect(self.cancel_requested)
        header_layout.addWidget(self.cancel_btn)

        layout.addLayout(header_layout)

        self.details_container = QWidget()
        self.details_container.setVisible(False)
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, 5, 0, 0)
        details_layout.setSpacing(5)

        self.overall_progress = ProgressBar()
        self.overall_progress.setTextVisible(True)
        details_layout.addWidget(self.overall_progress)

        self.status_text = PlainTextEdit()
        self.status_text.setMaximumHeight(80)
        self.status_text.setReadOnly(True)
        details_layout.addWidget(self.status_text)

        layout.addWidget(self.details_container)

    def toggle_details(self):
        self.is_expanded = not self.is_expanded
        self.details_container.setVisible(self.is_expanded)
        self.toggle_btn.setText("Hide Details" if self.is_expanded else "Show Details")

    def start_translation(self):
        self.is_active = True
        self.cancel_btn.setVisible(True)
        self.toggle_btn.setVisible(True)
        self.status_text.clear()
        self.status_text.appendPlainText("Translation started...")
        self.overall_progress.setValue(0)

        self.is_expanded = False
        self.details_container.setVisible(False)
        self.toggle_btn.setText("Show Details")

    def update_progress(self, event_data: Dict[str, Any]):
        if not self.is_active:
            return

        progress = event_data.get('progress_percent', 0)
        self.overall_progress.setValue(int(progress))

        message = event_data.get('message', '')
        if message:
            timestamp = time.strftime("%H:%M:%S")
            self.status_text.appendPlainText(f"[{timestamp}] {message}")

            cursor = self.status_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.status_text.setTextCursor(cursor)

    def complete_translation(self):
        self.is_active = False
        self.overall_progress.setValue(100)
        self.status_text.appendPlainText("Translation completed!")

        self.cancel_btn.setVisible(False)

        QTimer.singleShot(3000, self.reset)

    def reset(self):
        self.is_active = False
        self.overall_progress.setValue(0)
        self.status_text.clear()
        self.cancel_btn.setVisible(False)
        self.toggle_btn.setVisible(False)
        self.details_container.setVisible(False)
        self.is_expanded = False


class SettingsDialog(MessageBoxBase):
    """Settings configuration dialog."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.titleLabel = StrongBodyLabel("Settings")
        self.viewLayout.addWidget(self.titleLabel)

        body = BodyLabel(
            "Settings dialog will be implemented here.\n\n"
            "API keys and translation services can be configured\n"
            "via the API Settings dialog from the toolbar."
        )
        body.setWordWrap(True)
        self.viewLayout.addWidget(body)

        self.yesButton.setText("OK")
        self.cancelButton.setText("Cancel")

        self.widget.setMinimumWidth(420)

    def get_settings(self):
        return self.settings


class AboutDialog(MessageBoxBase):
    """About dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.titleLabel = StrongBodyLabel("About Desktop PDF Translator")
        self.viewLayout.addWidget(self.titleLabel)

        info = BodyLabel(
            "<p><b>Version:</b> 1.0.0<br/>"
            "<b>Vietnamese Language Priority</b></p>"
            "<p>A desktop application for translating PDF documents while preserving formatting.</p>"
            "<p><b>Supported Services:</b></p>"
            "<ul>"
            "<li>OpenAI GPT Models</li>"
            "<li>Google Gemini</li>"
            "</ul>"
            "<p><b>Future Features:</b></p>"
            "<ul>"
            "<li>RAG Chat Integration</li>"
            "<li>Batch Processing</li>"
            "<li>Additional Translation Services</li>"
            "</ul>"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        self.viewLayout.addWidget(info)

        self.cancelButton.hide()
        self.yesButton.setText("OK")

        self.widget.setMinimumWidth(440)
