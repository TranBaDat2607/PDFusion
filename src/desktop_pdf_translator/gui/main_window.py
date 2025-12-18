"""
Main window for desktop PDF translator with three-panel layout.
"""

import sys
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QMenuBar, QStatusBar, QToolBar, QFileDialog,
    QMessageBox, QProgressBar, QLabel, QPushButton, QGroupBox,
    QComboBox, QLineEdit, QTextEdit, QFrame, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QPixmap, QFont
from PySide6.QtCore import QStandardPaths

import qtawesome as qta

from ..config import get_settings, get_config_manager, LanguageCode, TranslationService, AppSettings
from ..processors import PDFProcessor
from ..translators import TranslatorFactory
from .widgets import (
    PDFViewer, ProgressPanel, 
    SettingsDialog, AboutDialog,
)
from .rag_chat_panel import RAGChatPanel
from .worker import TranslationWorker
logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window with three-panel layout.
    
    Layout:
    - Top toolbar: All functional buttons and controls in a single row
    - Left panel: Original PDF viewer
    - Right panel: Translated PDF viewer
    """
    
    def __init__(self):
        super().__init__()

        self.settings = get_settings()
        self.config_manager = get_config_manager()

        # Initialize icons
        self.icons = self._init_icons()

        # Processing state
        self.current_file: Optional[Path] = None
        self.translation_worker: Optional[TranslationWorker] = None
        
        # RAG state - read from config, default OFF
        self.rag_enabled = self.settings.rag.enabled
        self._updating_service_controls = False

        # UI setup
        self._setup_ui()
        self._setup_connections()
        self._apply_settings()
        self._refresh_service_controls()
        
        # Initialize RAG state
        self._initialize_rag_state()
        
        # Schedule panel size adjustment after window is shown
        QTimer.singleShot(100, self._adjust_panel_sizes)
        
        logger.info("Main window initialized")

    def _init_icons(self):
        """Initialize QtAwesome icons for the main window."""
        return {
            'browse': qta.icon('fa5s.folder-open', color='#2196F3'),
            'translate': qta.icon('fa5s.language', color='#4CAF50'),
            'cancel': qta.icon('fa5s.times-circle', color='#f44336'),
            'settings': qta.icon('fa5s.cog', color='#666'),
            'validate': qta.icon('fa5s.check-circle', color='#4CAF50'),
            'about': qta.icon('fa5s.info-circle', color='#2196F3'),
            'rag_on': qta.icon('fa5s.robot', color='#4CAF50'),
            'rag_off': qta.icon('fa5s.robot', color='#999')
        }

    def _setup_ui(self):
        """Setup the main UI layout."""
        self.setWindowTitle("Desktop PDF Translator - Vietnamese Priority")
        self.setMinimumSize(1000, 600)
        
        # Set default window size based on settings
        self.resize(
            self.settings.gui.window_width,
            self.settings.gui.window_height
        )
        
        # Create menu bar
        self._create_menu_bar()
        
        # Create comprehensive top toolbar
        self._create_top_toolbar()
        
        # Create status bar
        self._create_status_bar()
        
        # Create main layout
        self._create_main_layout()
    
    def _create_menu_bar(self):
        """Create application menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        open_action = QAction("&Open PDF...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setStatusTip("Open PDF file for translation")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # View menu
        view_menu = menubar.addMenu("&View")
        
        
        # Tools menu
        tools_menu = menubar.addMenu("&Tools")
        
        settings_action = QAction("&Settings...", self)
        settings_action.setStatusTip("Open application settings")
        settings_action.triggered.connect(self.open_settings)
        tools_menu.addAction(settings_action)
        
        validate_service_action = QAction("&Validate Services", self)
        validate_service_action.setStatusTip("Check translation service configuration")
        validate_service_action.triggered.connect(self.validate_services)
        tools_menu.addAction(validate_service_action)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def _create_top_toolbar(self):
        """Create a comprehensive top toolbar with all controls."""
        toolbar = self.addToolBar("Top Toolbar")
        toolbar.setMovable(False)  # Fixed toolbar at top
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        
        # File selection controls
        toolbar.addWidget(QLabel("File:"))
        
        self.file_label = QLabel("No file selected")
        self.file_label.setStyleSheet("padding: 5px; border: 1px solid #ccc; background: #f9f9f9; min-width: 150px;")
        toolbar.addWidget(self.file_label)
        
        self.browse_btn = QPushButton(" Browse")
        self.browse_btn.setIcon(self.icons['browse'])
        self.browse_btn.setStatusTip("Browse for PDF file")
        self.browse_btn.clicked.connect(self._browse_file)
        toolbar.addWidget(self.browse_btn)
        
        toolbar.addSeparator()
        
        # Translation settings
        toolbar.addWidget(QLabel("From:"))
        
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItems([
            "Auto-detect",
            "English", 
            "Vietnamese",
            "Japanese",
            "Chinese (Simplified)",
            "Chinese (Traditional)"
        ])
        self.source_lang_combo.setCurrentText("Auto-detect")
        toolbar.addWidget(self.source_lang_combo)
        
        toolbar.addWidget(QLabel("To:"))
        
        self.target_lang_combo = QComboBox()
        self.target_lang_combo.addItems([
            "Vietnamese",  # Default first
            "English",
            "Japanese", 
            "Chinese (Simplified)",
            "Chinese (Traditional)"
        ])
        self.target_lang_combo.setCurrentText("Vietnamese")
        toolbar.addWidget(self.target_lang_combo)
        
        self.validate_key_btn = QPushButton("Validate Key")
        self.validate_key_btn.setStatusTip("Validate API key for selected service")
        toolbar.addWidget(self.validate_key_btn)

        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("API key")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setMaximumWidth(300)
        self.api_key_input.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        toolbar.addWidget(self.api_key_input)

        self.service_label = QLabel("Service:")
        toolbar.addWidget(self.service_label)

        self.service_combo = QComboBox()
        self.service_combo.addItems(["OpenAI", "Gemini"])
        toolbar.addWidget(self.service_combo)

        self.model_label = QLabel("Model:")
        toolbar.addWidget(self.model_label)

        self.model_combo = QComboBox()
        toolbar.addWidget(self.model_combo)
        
        toolbar.addSeparator()
        
        # Action buttons
        self.translate_btn = QPushButton(" Translate")
        self.translate_btn.setIcon(self.icons['translate'])
        self.translate_btn.setStatusTip("Start translation")
        self.translate_btn.setEnabled(False)
        self.translate_btn.clicked.connect(self.start_translation)
        toolbar.addWidget(self.translate_btn)

        self.cancel_btn = QPushButton(" Cancel")
        self.cancel_btn.setIcon(self.icons['cancel'])
        self.cancel_btn.setStatusTip("Cancel translation")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_translation)
        toolbar.addWidget(self.cancel_btn)

        toolbar.addSeparator()

        self.settings_btn = QPushButton(" Settings")
        self.settings_btn.setIcon(self.icons['settings'])
        self.settings_btn.setStatusTip("Open application settings")
        self.settings_btn.clicked.connect(self.open_settings)
        toolbar.addWidget(self.settings_btn)

        self.validate_btn = QPushButton(" Validate")
        self.validate_btn.setIcon(self.icons['validate'])
        self.validate_btn.setStatusTip("Check translation service configuration")
        self.validate_btn.clicked.connect(self.validate_services)
        toolbar.addWidget(self.validate_btn)

        self.about_btn = QPushButton(" About")
        self.about_btn.setIcon(self.icons['about'])
        self.about_btn.setStatusTip("About this application")
        self.about_btn.clicked.connect(self.show_about)
        toolbar.addWidget(self.about_btn)
        
        toolbar.addSeparator()
        
        # RAG toggle button
        self.rag_toggle_btn = QPushButton(" RAG: ON" if self.rag_enabled else " RAG: OFF")
        self.rag_toggle_btn.setIcon(self.icons['rag_on'] if self.rag_enabled else self.icons['rag_off'])
        self.rag_toggle_btn.setStatusTip("Toggle RAG (AI Chat) functionality")
        self.rag_toggle_btn.setCheckable(True)
        self.rag_toggle_btn.setChecked(self.rag_enabled)
        self.rag_toggle_btn.clicked.connect(self.toggle_rag)
        self.rag_toggle_btn.setStyleSheet("""
            QPushButton:checked {
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:!checked {
                background-color: #f44336;
                color: white;
            }
        """)
        toolbar.addWidget(self.rag_toggle_btn)
        
        # Add stretch to push buttons to the left
        toolbar.addWidget(QWidget())
    
    def _browse_file(self):
        """Browse for PDF file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF File",
            str(Path.home()),
            "PDF Files (*.pdf);;All Files (*.*)"
        )
        
        if file_path:
            self.load_file(Path(file_path))
    
    def _create_status_bar(self):
        """Create status bar with progress indicator."""
        status_bar = self.statusBar()

        # Status label (with more space for progress messages)
        self.status_label = QLabel("Ready")
        self.status_label.setMinimumWidth(300)
        status_bar.addWidget(self.status_label, 1)  # Stretch factor 1

        # Progress percentage label (compact)
        self.progress_percent_label = QLabel("")
        self.progress_percent_label.setVisible(False)
        self.progress_percent_label.setStyleSheet("color: #2196F3; font-weight: bold; padding: 0 5px;")
        self.progress_percent_label.setMinimumWidth(50)
        status_bar.addPermanentWidget(self.progress_percent_label)

        # Progress bar (more prominent, wider)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumWidth(250)
        self.progress_bar.setMinimumWidth(200)
        self.progress_bar.setMaximumHeight(18)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #bbb;
                border-radius: 3px;
                text-align: center;
                background-color: #f0f0f0;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
        """)
        status_bar.addPermanentWidget(self.progress_bar)

        # Service status
        self.service_status_label = QLabel("Checking services...")
        status_bar.addPermanentWidget(self.service_status_label)

        # Check service status on startup
        QTimer.singleShot(1000, self.check_service_status)
    
    def _create_main_layout(self):
        """Create the main three-panel layout with chat on the right."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main horizontal splitter
        main_splitter = QSplitter(Qt.Horizontal)

        # Left panel: Original PDF viewer
        left_panel = QGroupBox("Original PDF")
        left_layout = QVBoxLayout(left_panel)
        self.original_pdf_viewer = PDFViewer()
        left_layout.addWidget(self.original_pdf_viewer)

        # Middle panel: Translated PDF viewer
        middle_panel = QGroupBox("Translated PDF")
        middle_layout = QVBoxLayout(middle_panel)
        self.translated_pdf_viewer = PDFViewer()
        middle_layout.addWidget(self.translated_pdf_viewer)

        # Right panel: RAG chat panel
        right_panel = QGroupBox("Chat with PDF")
        right_layout = QVBoxLayout(right_panel)
        self.chat_panel = RAGChatPanel()
        self.chat_panel.setVisible(True)  # Always visible by default
        right_layout.addWidget(self.chat_panel)

        # Add panels to main splitter
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(middle_panel)
        main_splitter.addWidget(right_panel)

        # Set stretch factors for equal proportions (each panel gets equal weight)
        main_splitter.setStretchFactor(0, 1)  # Left panel
        main_splitter.setStretchFactor(1, 1)  # Middle panel
        main_splitter.setStretchFactor(2, 1)  # Right panel

        # Store splitter reference to set sizes after show
        self.main_splitter = main_splitter

        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.addWidget(main_splitter, 1)  # Splitter takes most space

        # Compact progress panel at bottom (won't push content when collapsed)
        self.progress_panel = ProgressPanel()
        self.progress_panel.setVisible(False)
        self.progress_panel.setMaximumHeight(120)  # Limit max height
        main_layout.addWidget(self.progress_panel, 0)  # No stretch

        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

    def _setup_connections(self):
        """Setup signal-slot connections."""
        self.validate_key_btn.clicked.connect(self._handle_validate_api_key)
        self.api_key_input.editingFinished.connect(self._on_api_key_changed)
        self.service_combo.currentTextChanged.connect(self._on_service_changed)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        self.progress_panel.cancel_requested.connect(self.cancel_translation)
        self.chat_panel.pdf_navigation_requested.connect(self._handle_pdf_navigation)
        self.chat_panel.web_link_requested.connect(self._handle_web_link)

    def _apply_settings(self):
        """Apply current settings to UI."""
        if self.settings.gui.theme != "system":
            pass

    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open PDF File",
            str(Path.home()),
            "PDF Files (*.pdf);;All Files (*.*)"
        )
        if file_path:
            self.load_file(Path(file_path))

    def load_file(self, file_path: Path):
        try:
            if not file_path.exists():
                QMessageBox.warning(self, "File Error", f"File does not exist: {file_path}")
                return
            if file_path.suffix.lower() != '.pdf':
                QMessageBox.warning(self, "File Error", "Please select a PDF file.")
                return
            if self.original_pdf_viewer.load_pdf(file_path):
                self.current_file = file_path
                self.file_label.setText(file_path.name)
                self.status_label.setText(f"Loaded: {file_path.name}")
                self.translate_btn.setEnabled(True)
                if self.rag_enabled:
                    self.chat_panel.process_document(file_path)
                else:
                    self.chat_panel.set_rag_disabled_message()
                logger.info(f"Loaded PDF: {file_path}")
            else:
                QMessageBox.critical(self, "Load Error", f"Failed to load PDF: {file_path}")
        except Exception as e:
            logger.exception(f"Error loading file: {e}")
            QMessageBox.critical(self, "Load Error", f"Error loading file: {e}")

    def _on_service_changed(self, text: str):
        if self._updating_service_controls:
            return
        service = self._text_to_service(text)
        if service is None:
            return
        if self.settings.translation.preferred_service != service:
            self.settings.translation.preferred_service = service
            self._persist_settings()
        self._refresh_service_controls()
        self.check_service_status()

    def _on_api_key_changed(self):
        if self._updating_service_controls:
            return
        service = self.settings.translation.preferred_service
        api_key = self.api_key_input.text().strip() or None
        if service == TranslationService.OPENAI:
            self.settings.openai.api_key = api_key
        elif service == TranslationService.GEMINI:
            self.settings.gemini.api_key = api_key
        self._persist_settings()
        self.check_service_status()

    def _on_model_changed(self, model: str):
        if self._updating_service_controls:
            return
        service = self.settings.translation.preferred_service
        if service == TranslationService.OPENAI:
            self.settings.openai.model = model
        elif service == TranslationService.GEMINI:
            self.settings.gemini.model = model
        self._persist_settings()

    def _handle_validate_api_key(self):
        service = self.settings.translation.preferred_service
        api_key = self.api_key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Missing API Key", "Please enter an API key before validating.")
            return
        try:
            translator = TranslatorFactory.create_translator(
                service=service,
                lang_in=str(self.settings.translation.default_source_lang.value),
                lang_out=str(self.settings.translation.default_target_lang.value),
                api_key=api_key,
                model=self.model_combo.currentText(),
            )
            ok, message = translator.validate_configuration()
            if ok:
                QMessageBox.information(self, "API Key Valid", message)
            else:
                QMessageBox.critical(self, "Validation Failed", message)
        except Exception as exc:
            QMessageBox.critical(self, "Validation Failed", str(exc))

    def _refresh_service_controls(self):
        self._updating_service_controls = True
        try:
            service = self.settings.translation.preferred_service
            target_text = "OpenAI" if service == TranslationService.OPENAI else "Gemini"
            if self.service_combo.currentText() != target_text:
                self.service_combo.setCurrentText(target_text)

            if service == TranslationService.OPENAI:
                api_key = self.settings.openai.api_key or ""
                model = self.settings.openai.model
            else:
                api_key = self.settings.gemini.api_key or ""
                model = self.settings.gemini.model

            if self.api_key_input.text() != api_key:
                self.api_key_input.setText(api_key)

            options = self._get_model_options(service)
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(options)
            if model in options:
                self.model_combo.setCurrentText(model)
            elif options:
                self.model_combo.setCurrentIndex(0)
            self.model_combo.blockSignals(False)
        finally:
            self._updating_service_controls = False

    def _get_model_options(self, service: TranslationService) -> list[str]:
        if service == TranslationService.OPENAI:
            return ["gpt-4.1"]
        if service == TranslationService.GEMINI:
            return ["gemini-1.5-flash"]
        return []

    def _text_to_service(self, text: str) -> Optional[TranslationService]:
        normalized = text.strip().lower()
        if normalized == "openai":
            return TranslationService.OPENAI
        if normalized == "gemini":
            return TranslationService.GEMINI
        return None

    def _persist_settings(self):
        self.config_manager.save_settings(self.settings)
        self.config_manager._settings = self.settings

    def _check_translation_cancellation(self):
        """Check if translation has been cancelled and clean up if needed."""
        if self.translation_worker and self.translation_worker.isRunning():
            # Force terminate if still running after timeout
            self.translation_worker.terminate()
            self.translation_worker.wait(1000)  # Wait up to 1 second
            
        self.translation_worker = None
    
    def start_translation(self):
        """Start PDF translation process."""
        if not self.current_file:
            QMessageBox.warning(self, "No File", "Please select a PDF file first.")
            return
        
        # Get translation settings from toolbar controls
        source_lang_map = {
            "Auto-detect": "auto",
            "English": "en",
            "Vietnamese": "vi", 
            "Japanese": "ja",
            "Chinese (Simplified)": "zh-cn",
            "Chinese (Traditional)": "zh-tw"
        }
        
        target_lang_map = {
            "Vietnamese": "vi",
            "English": "en",
            "Japanese": "ja",
            "Chinese (Simplified)": "zh-cn", 
            "Chinese (Traditional)": "zh-tw"
        }
        
        service_map = {
            "OpenAI": "openai",
            "Gemini": "gemini"
        }
        
        # Get current selections from GUI
        source_lang_text = self.source_lang_combo.currentText()
        target_lang_text = self.target_lang_combo.currentText()
        service_text = self.service_combo.currentText()
        
        # Log the selections for debugging
        logger.info(f"GUI selections - Source: {source_lang_text}, Target: {target_lang_text}, Service: {service_text}")
        
        translation_config = {
            "source_lang": source_lang_map.get(source_lang_text, "auto"),
            "target_lang": target_lang_map.get(target_lang_text, "vi"),
            "service": service_map.get(service_text, "openai"),
            "preserve_formatting": True,  # Default value
            "cache_translations": True    # Default value
        }
        
        # Log the resolved configuration
        logger.info(f"Resolved config - Source: {translation_config['source_lang']}, Target: {translation_config['target_lang']}, Service: {translation_config['service']}")
        
        # Validate service configuration
        is_valid, message = TranslatorFactory.validate_service_availability(
            translation_config['service']
        )
        
        if not is_valid:
            QMessageBox.critical(self, "Service Error", f"Translation service error: {message}")
            return
        
        try:
            # Create and start translation worker
            self.translation_worker = TranslationWorker(
                file_path=self.current_file,
                **translation_config
            )
            
            # Connect worker signals to handler methods
            self.translation_worker.progress_updated.connect(self.on_translation_progress)
            self.translation_worker.translation_completed.connect(self.on_translation_completed)
            self.translation_worker.translation_failed.connect(self.on_translation_failed)
            
            # Update UI state
            self.translate_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)

            # Show status bar progress
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.progress_percent_label.setVisible(True)
            self.progress_percent_label.setText("0%")

            # Show compact progress panel
            self.progress_panel.setVisible(True)
            self.progress_panel.start_translation()

            # Start worker
            self.translation_worker.start()

            self.status_label.setText("Translation in progress...")

            logger.info("Translation started")
            
        except Exception as e:
            logger.exception(f"Error starting translation: {e}")
            QMessageBox.critical(self, "Translation Error", f"Failed to start translation: {e}")
    
    def cancel_translation(self):
        """Cancel current translation."""
        if self.translation_worker and self.translation_worker.isRunning():
            self.translation_worker.cancel()
            # Wait for a short time to allow graceful cancellation
            QTimer.singleShot(100, self._check_translation_cancellation)

            # Reset UI state immediately
            self.translate_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

            # Hide status bar progress
            self.progress_bar.setVisible(False)
            self.progress_percent_label.setVisible(False)

            # Hide progress panel
            self.progress_panel.setVisible(False)
            self.progress_panel.reset()

            self.status_label.setText("Translation cancelled")

            logger.info("Translation cancellation requested")
    
    def _check_translation_cancellation(self):
        """Check if translation has been cancelled and clean up if needed."""
        if self.translation_worker and self.translation_worker.isRunning():
            # Force terminate if still running after timeout
            self.translation_worker.terminate()
            self.translation_worker.wait(1000)  # Wait up to 1 second
            
        self.translation_worker = None
    
    def on_translation_progress(self, event_data):
        """Handle translation progress updates."""
        progress_percent = event_data.get('progress_percent', 0)
        message = event_data.get('message', '')
        stage = event_data.get('stage', '')

        # Update status bar progress
        self.progress_bar.setValue(int(progress_percent))
        self.progress_percent_label.setText(f"{int(progress_percent)}%")

        # Update status label with stage or message
        if stage:
            self.status_label.setText(f"{stage}")
        elif message:
            self.status_label.setText(f"{message}")

        # Update compact progress panel (for detailed view)
        self.progress_panel.update_progress(event_data)
    
    def on_translation_completed(self, result_data):
        """Handle translation completion."""
        try:
            translated_file = result_data.get('translated_file')

            if translated_file and Path(translated_file).exists():
                # Load translated PDF
                self.translated_pdf_viewer.load_pdf(Path(translated_file))
                self.status_label.setText("Translation completed successfully")

                # Process document for RAG only if RAG is enabled
                if self.rag_enabled:
                    self._process_document_for_rag(Path(translated_file))

                # Show success message
                QMessageBox.information(
                    self,
                    "Translation Complete",
                    f"Translation completed successfully!\nOutput: {translated_file}"
                )
            else:
                self.status_label.setText("Translation completed but output file not found")

        except Exception as e:
            logger.exception(f"Error handling translation completion: {e}")

        finally:
            # Reset UI state
            self.translate_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

            # Hide status bar progress
            self.progress_bar.setVisible(False)
            self.progress_percent_label.setVisible(False)

            # Complete and hide progress panel
            self.progress_panel.complete_translation()
            # Panel will auto-hide after 3 seconds via QTimer in complete_translation()
    
    def on_translation_failed(self, error_message):
        """Handle translation failure."""
        logger.error(f"Translation failed: {error_message}")

        QMessageBox.critical(self, "Translation Failed", f"Translation failed: {error_message}")

        # Reset UI state
        self.translate_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        # Hide status bar progress
        self.progress_bar.setVisible(False)
        self.progress_percent_label.setVisible(False)

        # Hide progress panel
        self.progress_panel.setVisible(False)
        self.progress_panel.reset()

        self.status_label.setText("Translation failed")
    
    # RAG Integration Methods
    def _process_document_for_rag(self, document_path: Path):
        """Process document for RAG system after translation."""
        try:
            # Set current document in chat panel
            self.chat_panel.set_current_document(document_path)
            
            # Process document for RAG (in background)
            self.chat_panel.process_document(document_path)
            
            logger.info(f"Document processed for RAG: {document_path}")
            
        except Exception as e:
            logger.error(f"Failed to process document for RAG: {e}")
    
    def _handle_pdf_navigation(self, page: int, bbox: object):
        """Handle PDF navigation request from chat panel."""
        try:
            # Navigate to the specified page in translated PDF viewer
            if hasattr(self.translated_pdf_viewer, 'goto_page'):
                self.translated_pdf_viewer.goto_page(page)
                
                # Highlight region if bbox is provided
                if bbox and hasattr(self.translated_pdf_viewer, 'highlight_region'):
                    self.translated_pdf_viewer.highlight_region(bbox)
                
                self.status_label.setText(f"Navigated to page {page}")
                logger.info(f"PDF navigation: page {page}")
            else:
                logger.warning("PDF viewer does not support navigation")
                
        except Exception as e:
            logger.error(f"PDF navigation failed: {e}")
            QMessageBox.warning(self, "Navigation Error", f"Failed to navigate to page {page}: {e}")
    
    def _handle_web_link(self, url: str):
        """Handle web link request from chat panel."""
        try:
            import webbrowser
            webbrowser.open(url)
            self.status_label.setText(f"Opened web link: {url[:50]}...")
            logger.info(f"Web link opened: {url}")
            
        except Exception as e:
            logger.error(f"Failed to open web link: {e}")
            QMessageBox.warning(self, "Link Error", f"Failed to open link: {e}")
    
    # Settings and configuration
    def open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.Accepted:
            # Save new settings
            new_settings = dialog.get_settings()
            self.config_manager.save_settings(new_settings)
            self.settings = new_settings

    # Service validation
    def check_service_status(self):
        """Check translation service status."""
        available_services = TranslatorFactory.get_available_services()
        
        if not available_services:
            self.service_status_label.setText("No services available")
            self.service_status_label.setStyleSheet("color: red")
        else:
            # Check configuration of preferred service
            preferred_service = self.settings.translation.preferred_service
            is_valid, message = TranslatorFactory.validate_service_availability(preferred_service)
            
            if is_valid:
                self.service_status_label.setText(f"✓ {preferred_service.value} ready")
                self.service_status_label.setStyleSheet("color: green")
            else:
                self.service_status_label.setText(f"{preferred_service.value} not configured")
                self.service_status_label.setStyleSheet("color: orange")
    
    def validate_services(self):
        """Show service validation dialog."""
        available_services = TranslatorFactory.get_available_services()

        def _service_model_options(service: TranslationService) -> list[str]:
            if service == TranslationService.OPENAI:
                return ["gpt-4.1"]
            if service == TranslationService.GEMINI:
                return ["gemini-1.5-flash"]
            return []

        message = "Translation Service Status:\n\n"
        
        for service in [TranslationService.OPENAI, TranslationService.GEMINI]:
            if service in available_services:
                is_valid, status_msg = TranslatorFactory.validate_service_availability(service)
                status = "Ready" if is_valid else f"{status_msg}"
            else:
                status = "Not installed"
            
            model_options = _service_model_options(service)
            message += f"{service.value}: {status} ({', '.join(model_options)})\n"
        
        QMessageBox.information(self, "Service Status", message)
    
    # Help and about
    def show_about(self):
        """Show about dialog."""
        dialog = AboutDialog(self)
        dialog.exec()
    
    # Window events
    def closeEvent(self, event):
        """Handle window close event."""
        # Cancel any running translation
        if self.translation_worker and self.translation_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Translation in Progress",
                "Translation is still running. Do you want to cancel and exit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.cancel_translation()
            else:
                event.ignore()
                return
        
        # Save current window size
        self.config_manager.update_settings(
            gui={
                "window_width": self.width(),
                "window_height": self.height()
            }
        )
        
        event.accept()
        logger.info("Application closed")
    
    
    def toggle_rag(self):
        """Toggle RAG functionality on/off."""
        self.rag_enabled = not self.rag_enabled
        
        # Update button appearance
        if self.rag_enabled:
            self.rag_toggle_btn.setText(" RAG: ON")
            self.rag_toggle_btn.setIcon(self.icons['rag_on'])
            self.rag_toggle_btn.setChecked(True)
            self.status_label.setText("RAG enabled - AI Chat ready")
            
            # Re-enable chat panel
            self.chat_panel.setEnabled(True)
            self.chat_panel.set_rag_enabled_message()
            
            # If there's a current document, process it for RAG
            if self.current_file:
                self.chat_panel.process_document(self.current_file)
        else:
            self.rag_toggle_btn.setText(" RAG: OFF")
            self.rag_toggle_btn.setIcon(self.icons['rag_off'])
            self.rag_toggle_btn.setChecked(False)
            self.status_label.setText("RAG disabled - AI Chat unavailable")
            
            # Disable chat panel
            self.chat_panel.setEnabled(False)
            self.chat_panel.set_rag_disabled_message()
        
        logger.info(f"RAG toggled: {'enabled' if self.rag_enabled else 'disabled'}")
        
        # Update RAG state in memory only (don't save to avoid TOML issues)
        self.settings.rag.enabled = self.rag_enabled
    
    def _adjust_panel_sizes(self):
        """Adjust panel sizes to be equal after window is shown."""
        if hasattr(self, 'main_splitter'):
            # Get actual splitter width
            total_width = self.main_splitter.width()
            if total_width > 0:
                # Calculate equal width for each panel
                panel_width = total_width // 3
                self.main_splitter.setSizes([panel_width, panel_width, panel_width])
                logger.info(f"Adjusted panel sizes to equal widths: {panel_width}px each")
    
    def _initialize_rag_state(self):
        """Initialize RAG state based on config."""
        if not self.rag_enabled:
            # If RAG is disabled in config, set up the disabled state
            self.chat_panel.setEnabled(False)
            self.chat_panel.set_rag_disabled_message()
        else:
            # RAG is enabled
            self.chat_panel.setEnabled(True)
            self.chat_panel.set_rag_enabled_message()


def create_app():
    """Create and configure the QApplication."""
    app = QApplication(sys.argv)
    
    # Set application properties
    app.setApplicationName("Desktop PDF Translator")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("PDF Translator Team")
    
    # Set Vietnamese locale support
    app.setProperty("Vietnamese_Support", True)
    
    return app


def main():
    """Main GUI entry point."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create application
    app = create_app()
    
    # Create main window
    window = MainWindow()
    window.show()
    
    # Start event loop
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())