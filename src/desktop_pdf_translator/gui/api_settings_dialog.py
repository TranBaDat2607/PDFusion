from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTabWidget,
    QWidget,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QLabel,
    QComboBox,
)

from ..config import AppSettings, TranslationService
from ..translators import TranslatorFactory


class APISettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("API Key Settings")
        self.setMinimumWidth(520)

        self._openai_api_key: Optional[str] = settings.openai.api_key
        self._gemini_api_key: Optional[str] = settings.gemini.api_key

        self.tab_widget = QTabWidget()
        self.openai_tab, self.openai_input, self.openai_model_combo, self.openai_status = self._create_service_tab(
            TranslationService.OPENAI,
            self._openai_api_key,
            self.settings.openai.model,
        )
        self.gemini_tab, self.gemini_input, self.gemini_model_combo, self.gemini_status = self._create_service_tab(
            TranslationService.GEMINI,
            self._gemini_api_key,
            self.settings.gemini.model,
        )

        self.tab_widget.addTab(self.openai_tab, "OpenAI")
        self.tab_widget.addTab(self.gemini_tab, "Gemini")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tab_widget)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._handle_save)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def _create_service_tab(self, service: TranslationService, api_key: Optional[str], selected_model: Optional[str]) -> tuple[QWidget, QLineEdit, QComboBox, QLabel]:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        status_label = QLabel("")
        status_label.setStyleSheet("color: #666666;")

        row_layout = QHBoxLayout()

        validate_btn = QPushButton("Validate")
        row_layout.addWidget(validate_btn)

        api_input = QLineEdit()
        api_input.setEchoMode(QLineEdit.Password)
        if api_key:
            api_input.setText(api_key)
        row_layout.addWidget(api_input)

        reveal_btn = QPushButton("Show")
        reveal_btn.setCheckable(True)
        reveal_btn.toggled.connect(lambda checked, field=api_input, button=reveal_btn: self._toggle_visibility(field, button, checked))
        row_layout.addWidget(reveal_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._clear_api_key(api_input, status_label))
        row_layout.addWidget(clear_btn)

        model_combo = QComboBox()
        model_options = self._get_model_options(service)
        model_combo.addItems(model_options)
        if selected_model and selected_model in model_options:
            model_combo.setCurrentText(selected_model)
        row_layout.addWidget(model_combo)
        row_layout.addStretch()

        layout.addLayout(row_layout)
        layout.addWidget(status_label)

        validate_btn.clicked.connect(lambda: self._validate_api_key(service, api_input, model_combo, status_label))

        return widget, api_input, model_combo, status_label

    def _toggle_visibility(self, field: QLineEdit, button: QPushButton, checked: bool) -> None:
        field.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        button.setText("Hide" if checked else "Show")

    def _clear_api_key(self, field: QLineEdit, status_label: QLabel) -> None:
        field.clear()
        status_label.setStyleSheet("color: #666666;")
        status_label.setText("")

    def _validate_api_key(self, service: TranslationService, field: QLineEdit, model_combo: QComboBox, status_label: QLabel) -> None:
        api_key = field.text().strip()
        if not api_key:
            status_label.setStyleSheet("color: #cc8800;")
            status_label.setText("Enter an API key to validate")
            return

        status_label.setStyleSheet("color: #666666;")
        status_label.setText("Validating...")

        try:
            translator = TranslatorFactory.create_translator(
                service=service,
                lang_in=str(self.settings.translation.default_source_lang.value),
                lang_out=str(self.settings.translation.default_target_lang.value),
                api_key=api_key,
                model=model_combo.currentText(),
            )

            is_valid, message = translator.validate_configuration()
            if is_valid:
                status_label.setStyleSheet("color: #1b8a3a;")
                status_label.setText(message)
            else:
                status_label.setStyleSheet("color: #b00020;")
                status_label.setText(message)

        except Exception as exc:
            status_label.setStyleSheet("color: #b00020;")
            status_label.setText(str(exc))

    def _get_model_options(self, service: TranslationService) -> list[str]:
        if service == TranslationService.OPENAI:
            return ["gpt-4.1", "gpt-4o", "gpt-4o-mini"]
        if service == TranslationService.GEMINI:
            return ["gemini-1.5-flash", "gemini-1.5-pro"]
        return []

    def _handle_save(self) -> None:
        self._openai_api_key = self.openai_input.text().strip() or None
        self._gemini_api_key = self.gemini_input.text().strip() or None
        self.settings.openai.model = self.openai_model_combo.currentText()
        self.settings.gemini.model = self.gemini_model_combo.currentText()

        self.settings.openai.api_key = self._openai_api_key
        self.settings.gemini.api_key = self._gemini_api_key

        self.accept()

    def get_updated_settings(self) -> AppSettings:
        return self.settings
