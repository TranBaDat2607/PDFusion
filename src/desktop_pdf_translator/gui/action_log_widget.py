"""
Visual action log widget to show what the agent is doing during searches.
Text-only display without icons or emojis.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from datetime import datetime
import logging

from qfluentwidgets import CardWidget, BodyLabel, CaptionLabel, StrongBodyLabel

logger = logging.getLogger(__name__)


class ActionLogWidget(CardWidget):
    """Minimal text-only log showing agent actions, shown above chat bubbles during searches."""

    def __init__(self):
        super().__init__()
        self.actions = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.actions_container = QWidget()
        self.actions_layout = QVBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(12, 8, 12, 8)
        self.actions_layout.setSpacing(4)

        layout.addWidget(self.actions_container)

    def add_action(self, action_type: str, description: str, status: str = "running"):
        action_item = ActionItem(action_type, description, status)
        self.actions_layout.addWidget(action_item)
        self.actions.append(action_item)

    def update_last_action(self, status: str, result: str = None):
        if self.actions:
            self.actions[-1].update_status(status, result)

    def clear_actions(self):
        while self.actions_layout.count() > 0:
            item = self.actions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.actions.clear()


class ActionItem(QWidget):
    """Single action item in the log - text only, semantic per-type colors."""

    ACTION_CONFIGS = {
        'search':      {'color': '#0066cc', 'label': 'Search'},
        'rag':         {'color': '#2e7d32', 'label': 'RAG'},
        'deep_search': {'color': '#9C27B0', 'label': 'Deep Search'},
        'web':         {'color': '#f57c00', 'label': 'Web Research'},
        'local':       {'color': '#455a64', 'label': 'Local Database'},
        'api':         {'color': '#00897b', 'label': 'API'},
        'synthesis':   {'color': '#6a1b9a', 'label': 'Synthesis'},
        'hop':         {'color': '#9C27B0', 'label': 'Citation Hop'},
    }

    STATUS_COLORS = {
        'running':  '#ff9800',
        'complete': '#4caf50',
        'error':    '#f44336',
    }

    def __init__(self, action_type: str, description: str, status: str = "running"):
        super().__init__()
        self.action_type = action_type
        self.description = description
        self.status = status
        self.timestamp = datetime.now()
        self.setup_ui()

    def setup_ui(self):
        config = self.ACTION_CONFIGS.get(self.action_type, {'color': '#666', 'label': 'Action'})

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        # Per-type colored label (semantic — kept as inline color)
        self.action_label = StrongBodyLabel(f"{config['label']}:")
        self.action_label.setStyleSheet(f"color: {config['color']}; min-width: 100px;")
        layout.addWidget(self.action_label)

        self.desc_label = BodyLabel(self.description)
        self.desc_label.setWordWrap(False)
        layout.addWidget(self.desc_label, stretch=1)

        self.status_label = CaptionLabel()
        self.update_status_indicator()
        layout.addWidget(self.status_label)

    def update_status_indicator(self):
        if self.status == "running":
            self.status_label.setText("running...")
        elif self.status == "complete":
            self.status_label.setText("done")
        elif self.status == "error":
            self.status_label.setText("failed")

        color = self.STATUS_COLORS.get(self.status, '')
        if color:
            self.status_label.setStyleSheet(f"color: {color};")

    def update_status(self, status: str, result: str = None):
        self.status = status
        self.update_status_indicator()

        if result and status == "complete":
            self.desc_label.setText(f"{self.description} - {result}")
        elif result and status == "error":
            self.desc_label.setText(f"{self.description} - Error: {result}")
            self.desc_label.setStyleSheet(f"color: {self.STATUS_COLORS['error']};")
