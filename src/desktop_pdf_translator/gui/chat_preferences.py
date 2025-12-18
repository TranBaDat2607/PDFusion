"""
User preferences for chat UI customization.
"""

import logging
from typing import Dict, Any
from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)


class ChatPreferences:
    """Manage user preferences for chat UI."""

    # Default values
    DEFAULTS = {
        'message_density': 'comfortable',  # compact, comfortable, spacious
        'font_size': 9,  # pt
        'show_animations': True,
        'auto_scroll': True,
        'show_processing_time': True,
        'expand_references_default': False,
        'enable_web_research_default': False,
        'max_message_history': 100,  # Keep last N messages
    }

    def __init__(self, settings: QSettings = None):
        """
        Initialize chat preferences.

        Args:
            settings: QSettings instance (optional)
        """
        self.settings = settings or QSettings("PDFusion", "ChatPreferences")
        self._cache = {}
        self._load_preferences()

    def _load_preferences(self):
        """Load preferences from settings."""
        for key, default_value in self.DEFAULTS.items():
            self._cache[key] = self.settings.value(f"chat/{key}", default_value)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get preference value.

        Args:
            key: Preference key
            default: Default value if key not found

        Returns:
            Preference value
        """
        if default is None:
            default = self.DEFAULTS.get(key)

        return self._cache.get(key, default)

    def set(self, key: str, value: Any):
        """
        Set preference value.

        Args:
            key: Preference key
            value: New value
        """
        self._cache[key] = value
        self.settings.setValue(f"chat/{key}", value)
        logger.info(f"Chat preference updated: {key} = {value}")

    # Convenience methods
    @property
    def show_animations(self) -> bool:
        """Get show animations setting."""
        return bool(self.get('show_animations'))

    @property
    def auto_scroll(self) -> bool:
        """Get auto scroll setting."""
        return bool(self.get('auto_scroll'))


# Global preferences instance
_preferences_instance = None


def get_chat_preferences() -> ChatPreferences:
    """
    Get global chat preferences instance.

    Returns:
        ChatPreferences instance
    """
    global _preferences_instance

    if _preferences_instance is None:
        _preferences_instance = ChatPreferences()

    return _preferences_instance
