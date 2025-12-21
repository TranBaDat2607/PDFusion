"""
Visual action log widget to show what the agent is doing during searches.
Text-only display without icons or emojis.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ActionLogWidget(QFrame):
    """
    Minimal text-only log showing agent actions.
    Appears at top of chat bubbles during searches.
    """

    def __init__(self):
        super().__init__()
        self.actions = []
        self.setup_ui()

    def setup_ui(self):
        """Setup the minimal action log UI."""
        self.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container for action items
        self.actions_container = QWidget()
        self.actions_layout = QVBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(12, 8, 12, 8)
        self.actions_layout.setSpacing(4)

        layout.addWidget(self.actions_container)

    def add_action(self, action_type: str, description: str, status: str = "running"):
        """
        Add an action to the log.

        Args:
            action_type: Type of action (e.g., "search", "rag", "deep_search", "web")
            description: Description of the action
            status: Status ("running", "complete", "error")
        """
        action_item = ActionItem(action_type, description, status)
        self.actions_layout.addWidget(action_item)
        self.actions.append(action_item)

    def update_last_action(self, status: str, result: str = None):
        """
        Update the status of the last action.

        Args:
            status: New status ("complete", "error")
            result: Optional result text
        """
        if self.actions:
            self.actions[-1].update_status(status, result)

    def clear_actions(self):
        """Clear all actions from the log."""
        # Remove all action items
        while self.actions_layout.count() > 0:
            item = self.actions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.actions.clear()


class ActionItem(QWidget):
    """Single action item in the log - text only, no icons."""

    # Action type configurations (text-only labels and colors)
    ACTION_CONFIGS = {
        'search': {
            'color': '#0066cc',
            'label': 'Search'
        },
        'rag': {
            'color': '#2e7d32',
            'label': 'RAG'
        },
        'deep_search': {
            'color': '#9C27B0',
            'label': 'Deep Search'
        },
        'web': {
            'color': '#f57c00',
            'label': 'Web Research'
        },
        'local': {
            'color': '#455a64',
            'label': 'Local Database'
        },
        'api': {
            'color': '#00897b',
            'label': 'API'
        },
        'synthesis': {
            'color': '#6a1b9a',
            'label': 'Synthesis'
        },
        'hop': {
            'color': '#9C27B0',
            'label': 'Citation Hop'
        }
    }

    def __init__(self, action_type: str, description: str, status: str = "running"):
        super().__init__()
        self.action_type = action_type
        self.description = description
        self.status = status
        self.timestamp = datetime.now()
        self.setup_ui()

    def setup_ui(self):
        """Setup the minimal text-only action item UI."""
        config = self.ACTION_CONFIGS.get(self.action_type, {
            'color': '#666',
            'label': 'Action'
        })

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        # Action label with colored text
        self.action_label = QLabel(f"{config['label']}:")
        self.action_label.setStyleSheet(f"""
            QLabel {{
                color: {config['color']};
                font-weight: bold;
                font-size: 9pt;
                min-width: 100px;
            }}
        """)
        layout.addWidget(self.action_label)

        # Description
        self.desc_label = QLabel(self.description)
        self.desc_label.setWordWrap(False)
        self.desc_label.setStyleSheet("""
            QLabel {
                color: #333;
                font-size: 9pt;
            }
        """)
        layout.addWidget(self.desc_label, stretch=1)

        # Status indicator (text only)
        self.status_label = QLabel()
        self.update_status_indicator()
        layout.addWidget(self.status_label)

    def update_status_indicator(self):
        """Update the status indicator (text only)."""
        if self.status == "running":
            self.status_label.setText("running...")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #ff9800;
                    font-size: 8pt;
                    font-style: italic;
                }
            """)
        elif self.status == "complete":
            self.status_label.setText("done")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #4caf50;
                    font-size: 8pt;
                    font-weight: bold;
                }
            """)
        elif self.status == "error":
            self.status_label.setText("failed")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 8pt;
                    font-weight: bold;
                }
            """)

    def update_status(self, status: str, result: str = None):
        """Update the status and optionally update description with result."""
        self.status = status
        self.update_status_indicator()

        # Update description to show result
        if result and status == "complete":
            self.desc_label.setText(f"{self.description} - {result}")
        elif result and status == "error":
            self.desc_label.setText(f"{self.description} - Error: {result}")
            self.desc_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 9pt;
                }
            """)
