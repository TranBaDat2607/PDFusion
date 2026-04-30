from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
)

from qfluentwidgets import (
    MessageBoxBase,
    Pivot,
    PushButton,
    PrimaryPushButton,
    PasswordLineEdit,
    ComboBox,
    BodyLabel,
    StrongBodyLabel,
    CaptionLabel,
    InfoBar,
    InfoBarPosition,
    FluentIcon,
)

from ..config import AppSettings, TranslationService
from ..translators import TranslatorFactory


class APISettingsDialog(MessageBoxBase):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self._openai_api_key: Optional[str] = settings.openai.api_key
        self._gemini_api_key: Optional[str] = settings.gemini.api_key

        self.titleLabel = StrongBodyLabel("API Key Settings")
        self.viewLayout.addWidget(self.titleLabel)

        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)

        self.openai_tab, self.openai_input, self.openai_model_combo, self.openai_status = (
            self._create_service_tab(
                TranslationService.OPENAI,
                self._openai_api_key,
                self.settings.openai.model,
            )
        )
        self.gemini_tab, self.gemini_input, self.gemini_model_combo, self.gemini_status = (
            self._create_service_tab(
                TranslationService.GEMINI,
                self._gemini_api_key,
                self.settings.gemini.model,
            )
        )

        self._add_pivot_item("openai", "OpenAI", self.openai_tab)
        self._add_pivot_item("gemini", "Gemini", self.gemini_tab)

        self.pivot.setCurrentItem("openai")
        self.stack.setCurrentWidget(self.openai_tab)
        self.pivot.currentItemChanged.connect(
            lambda key: self.stack.setCurrentWidget(
                self.openai_tab if key == "openai" else self.gemini_tab
            )
        )

        self.viewLayout.addWidget(self.pivot)
        self.viewLayout.addWidget(self.stack)

        self.yesButton.setText("Save")
        self.cancelButton.setText("Cancel")
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._handle_save)

        self.widget.setMinimumWidth(560)

    def _add_pivot_item(self, key: str, text: str, widget: QWidget) -> None:
        self.stack.addWidget(widget)
        self.pivot.addItem(
            routeKey=key,
            text=text,
            onClick=lambda: self.stack.setCurrentWidget(widget),
        )

    def _create_service_tab(
        self,
        service: TranslationService,
        api_key: Optional[str],
        selected_model: Optional[str],
    ) -> tuple[QWidget, PasswordLineEdit, ComboBox, CaptionLabel]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        status_label = CaptionLabel("")

        row_layout = QHBoxLayout()
        row_layout.setSpacing(6)

        api_input = PasswordLineEdit()
        api_input.setPlaceholderText("API key")
        if api_key:
            api_input.setText(api_key)
        row_layout.addWidget(api_input, 1)

        clear_btn = PushButton("Clear", icon=FluentIcon.DELETE)
        clear_btn.clicked.connect(lambda: self._clear_api_key(api_input, status_label))
        row_layout.addWidget(clear_btn)

        validate_btn = PrimaryPushButton("Validate")
        row_layout.addWidget(validate_btn)

        layout.addLayout(row_layout)

        model_row = QHBoxLayout()
        model_row.addWidget(BodyLabel("Model:"))
        model_combo = ComboBox()
        model_options = self._get_model_options(service)
        model_combo.addItems(model_options)
        if selected_model and selected_model in model_options:
            model_combo.setCurrentText(selected_model)
        model_row.addWidget(model_combo)
        model_row.addStretch()
        layout.addLayout(model_row)

        layout.addWidget(status_label)
        layout.addStretch()

        validate_btn.clicked.connect(
            lambda: self._validate_api_key(service, api_input, model_combo, status_label)
        )

        return widget, api_input, model_combo, status_label

    def _clear_api_key(self, field: PasswordLineEdit, status_label: CaptionLabel) -> None:
        field.clear()
        status_label.setText("")
        status_label.setStyleSheet("")

    def _validate_api_key(
        self,
        service: TranslationService,
        field: PasswordLineEdit,
        model_combo: ComboBox,
        status_label: CaptionLabel,
    ) -> None:
        api_key = field.text().strip()
        if not api_key:
            status_label.setStyleSheet("color: #cc8800;")
            status_label.setText("Enter an API key to validate")
            return

        status_label.setStyleSheet("")
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
                InfoBar.success(
                    title="Validated",
                    content=message,
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=2500,
                )
            else:
                status_label.setStyleSheet("color: #b00020;")
                status_label.setText(message)
                InfoBar.error(
                    title="Validation failed",
                    content=message,
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=4000,
                )

        except Exception as exc:
            status_label.setStyleSheet("color: #b00020;")
            status_label.setText(str(exc))
            InfoBar.error(
                title="Error",
                content=str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=4000,
            )

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
