"""
Configuration manager for desktop PDF translator.
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import tomlkit
from pydantic import ValidationError

from .models import AppSettings

# Try to import python-dotenv for .env file support
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False
    load_dotenv = None


logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration with TOML files and environment variables."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize configuration manager.
        
        Args:
            config_dir: Directory for configuration files. Defaults to user config dir.
        """
        if config_dir is None:
            # Default to user's AppData directory on Windows
            self.config_dir = Path.home() / "AppData" / "Local" / "DesktopPDFTranslator"
        else:
            self.config_dir = Path(config_dir)
        
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.toml"
        
        # Initialize with default settings
        self._settings: Optional[AppSettings] = None
        
        # Load .env file if available
        self._load_dotenv()
        
    @property
    def settings(self) -> AppSettings:
        """Get current application settings."""
        if self._settings is None:
            self._settings = self.load_settings()
        return self._settings
    
    def load_settings(self) -> AppSettings:
        """Load settings from file and environment variables."""
        # Start with default settings
        config_data = {}
        
        # Load from TOML file if it exists
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    file_config = tomlkit.load(f)
                config_data.update(dict(file_config))
                logger.info(f"Loaded configuration from {self.config_file}")
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}")
        
        # Override with environment variables
        env_config = self._load_from_environment()
        self._deep_merge(config_data, env_config)
        
        # Create settings model with validation
        try:
            settings = AppSettings(**config_data)
            logger.info("Configuration loaded successfully")
            return settings
        except ValidationError as e:
            logger.error(f"Configuration validation failed: {e}")
            # Return default settings if validation fails
            return AppSettings()
    
    def save_settings(self, settings: AppSettings) -> bool:
        """Save settings to TOML file.
        
        Args:
            settings: Settings to save
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Convert to dict and format for TOML
            config_dict = settings.dict()
            
            # Remove sensitive data (API keys) from saved file
            config_dict = self._remove_sensitive_data(config_dict)
            
            # Create TOML document
            doc = tomlkit.document()
            
            # Add sections in organized way
            for section, data in config_dict.items():
                if isinstance(data, dict):
                    table = tomlkit.table()
                    for key, value in data.items():
                        table.add(key, value)
                    doc.add(section, table)
                else:
                    doc.add(section, data)
            
            # Write to file
            with open(self.config_file, "w", encoding="utf-8") as f:
                tomlkit.dump(doc, f)
            
            logger.info(f"Settings saved to {self.config_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False
    
    def _load_from_environment(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        env_config = {}
        
        # OpenAI settings
        if openai_key := os.getenv("OPENAI_API_KEY"):
            env_config.setdefault("openai", {})["api_key"] = openai_key
        
        if openai_model := os.getenv("OPENAI_MODEL"):
            env_config.setdefault("openai", {})["model"] = openai_model
        
        # Gemini settings
        if gemini_key := os.getenv("GEMINI_API_KEY"):
            env_config.setdefault("gemini", {})["api_key"] = gemini_key
        
        if gemini_model := os.getenv("GEMINI_MODEL"):
            env_config.setdefault("gemini", {})["model"] = gemini_model
        
        # Application settings
        if debug := os.getenv("DEBUG_MODE"):
            env_config["debug_mode"] = debug.lower() in ("true", "1", "yes")
        
        # Translation settings
        if max_pages := os.getenv("MAX_PAGES"):
            try:
                env_config.setdefault("translation", {})["max_pages"] = int(max_pages)
            except ValueError:
                logger.warning(f"Invalid MAX_PAGES value: {max_pages}")
        
        if max_size := os.getenv("MAX_FILE_SIZE_MB"):
            try:
                env_config.setdefault("translation", {})["max_file_size_mb"] = float(max_size)
            except ValueError:
                logger.warning(f"Invalid MAX_FILE_SIZE_MB value: {max_size}")
        
        return env_config
    
    def _load_dotenv(self) -> None:
        """Load environment variables from .env file if available."""
        if not DOTENV_AVAILABLE:
            return
        
        # Look for .env file in project root and config directory
        env_files = [
            Path.cwd() / ".env",
            self.config_dir / ".env"
        ]
        
        for env_file in env_files:
            if env_file.exists():
                try:
                    load_dotenv(env_file)
                    logger.info(f"Loaded environment variables from {env_file}")
                    break
                except Exception as e:
                    logger.warning(f"Failed to load .env file {env_file}: {e}")
    
    def _remove_sensitive_data(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data like API keys from config before saving."""
        safe_config = config_dict.copy()
        
        # Remove API keys - they should come from environment variables
        for service in ["openai", "gemini"]:
            if service in safe_config and isinstance(safe_config[service], dict):
                safe_config[service] = safe_config[service].copy()
                if "api_key" in safe_config[service]:
                    safe_config[service]["api_key"] = "${API_KEY}"  # Placeholder
        
        return safe_config
    
    def _deep_merge(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """Deep merge source dictionary into target dictionary."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value
    
    def reset_to_defaults(self) -> AppSettings:
        """Reset settings to defaults and save."""
        self._settings = AppSettings()
        self.save_settings(self._settings)
        logger.info("Settings reset to defaults")
        return self._settings
    
    def update_settings(self, **kwargs) -> bool:
        """Update specific settings and save.
        
        Args:
            **kwargs: Settings to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            # Get current settings as dict
            current_dict = self.settings.dict()
            
            # Update with provided values
            self._deep_merge(current_dict, kwargs)
            
            # Validate new settings
            new_settings = AppSettings(**current_dict)
            
            # Save and update
            if self.save_settings(new_settings):
                self._settings = new_settings
                return True
            return False
            
        except ValidationError as e:
            logger.error(f"Settings update validation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False
    
    def get_default_config_path(self) -> Path:
        """Get the default configuration file path."""
        return self.config_file
    
    def export_config(self, export_path: Path) -> bool:
        """Export current configuration to specified path.
        
        Args:
            export_path: Path to export configuration
            
        Returns:
            True if exported successfully, False otherwise
        """
        try:
            config_dict = self.settings.dict()
            config_dict = self._remove_sensitive_data(config_dict)
            
            with open(export_path, "w", encoding="utf-8") as f:
                tomlkit.dump(config_dict, f)
            
            logger.info(f"Configuration exported to {export_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to export configuration: {e}")
            return False


# Global configuration manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_settings() -> AppSettings:
    """Get current application settings."""
    return get_config_manager().settings