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

    def reset_to_defaults(self):
        """Reset all preferences to defaults."""
        for key, value in self.DEFAULTS.items():
            self.set(key, value)

        logger.info("Chat preferences reset to defaults")

    # Convenience methods
    @property
    def message_density(self) -> str:
        """Get message density setting."""
        return self.get('message_density')

    @message_density.setter
    def message_density(self, value: str):
        """Set message density setting."""
        if value in ['compact', 'comfortable', 'spacious']:
            self.set('message_density', value)

    @property
    def font_size(self) -> int:
        """Get font size setting."""
        return int(self.get('font_size'))

    @font_size.setter
    def font_size(self, value: int):
        """Set font size setting."""
        if 8 <= value <= 14:
            self.set('font_size', value)

    @property
    def show_animations(self) -> bool:
        """Get show animations setting."""
        return bool(self.get('show_animations'))

    @show_animations.setter
    def show_animations(self, value: bool):
        """Set show animations setting."""
        self.set('show_animations', value)

    @property
    def auto_scroll(self) -> bool:
        """Get auto scroll setting."""
        return bool(self.get('auto_scroll'))

    @auto_scroll.setter
    def auto_scroll(self, value: bool):
        """Set auto scroll setting."""
        self.set('auto_scroll', value)

    def get_spacing_for_density(self) -> Dict[str, int]:
        """
        Get spacing values based on message density.

        Returns:
            Dictionary with spacing values
        """
        density = self.message_density

        if density == 'compact':
            return {
                'message_spacing': 4,
                'content_padding': 12,
                'section_spacing': 8,
            }
        elif density == 'spacious':
            return {
                'message_spacing': 12,
                'content_padding': 24,
                'section_spacing': 16,
            }
        else:  # comfortable (default)
            return {
                'message_spacing': 8,
                'content_padding': 20,
                'section_spacing': 12,
            }


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
