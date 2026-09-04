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
from ..utils import DPAPI_PREFIX, encrypt_api_key, decrypt_api_key, is_encrypted

# Try to import python-dotenv for .env file support
from dotenv import load_dotenv


logger = logging.getLogger(__name__)

# The services whose settings carry an API key. Adding a backend that needs one
# is a one-line edit here rather than in each of the loops below.
KEYED_SERVICES = ("openai", "gemini", "anthropic")


class ConfigManager:
    """Manages application configuration with TOML files and environment variables."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize configuration manager.
        
        Args:
            config_dir: Directory for configuration files. Defaults to user config dir.
        """
        if config_dir is None:
            # Default to user's AppData directory on Windows
            self.config_dir = Path.home() / "AppData" / "Local" / "PDFusion"
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
                self._decrypt_sensitive_data(config_data)
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
            # Don't discard the whole config when a single field is invalid
            # (e.g. an unknown model name). Drop only the offending fields and
            # retry so valid siblings — crucially API keys loaded from env —
            # survive.
            logger.warning(
                "Configuration validation failed; dropping invalid field(s) "
                "and retrying: %s",
                e,
            )
            return self._load_with_invalid_fields_dropped(config_data, e)

    def _load_with_invalid_fields_dropped(
        self, config_data: Dict[str, Any], error: ValidationError
    ) -> AppSettings:
        """Best-effort recovery from a ValidationError.

        Removes each field flagged by `error` (by its `loc` path) from a copy
        of `config_data`, then retries. Falls back to dropping whole offending
        sections, and finally to bare defaults — but only as a last resort.
        Each dropped field reverts to its model default rather than wiping the
        entire configuration.
        """
        import copy

        pruned = copy.deepcopy(config_data)
        for err in error.errors():
            loc = err.get("loc", ())
            self._pop_path(pruned, loc)

        try:
            settings = AppSettings(**pruned)
            logger.info("Configuration loaded after dropping invalid field(s)")
            return settings
        except ValidationError as e2:
            # Second pass: drop the whole top-level section of anything still
            # invalid (handles cross-field validators we can't pinpoint).
            for err in e2.errors():
                loc = err.get("loc", ())
                if loc:
                    pruned.pop(loc[0], None)
            try:
                settings = AppSettings(**pruned)
                logger.info("Configuration loaded after dropping invalid section(s)")
                return settings
            except ValidationError as e3:
                logger.error(
                    "Configuration still invalid after pruning; using defaults: %s",
                    e3,
                )
                return AppSettings()

    @staticmethod
    def _pop_path(data: Dict[str, Any], loc: tuple) -> None:
        """Delete the value at a pydantic error `loc` path from a nested dict.
        No-op if the path doesn't resolve (e.g. it points into a list)."""
        if not loc:
            return
        node: Any = data
        for key in loc[:-1]:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return
        if isinstance(node, dict):
            node.pop(loc[-1], None)
    
    def save_settings(self, settings: AppSettings) -> bool:
        """Save settings to the TOML file, atomically.

        The write goes to a sibling temp file that is fsync'd and then
        `os.replace`d over `config.toml`. `os.replace` is atomic on Windows and
        POSIX, so a crash or a full disk mid-write leaves the previous file
        intact instead of a truncated one — which, for this file, means
        silently resetting every setting *including the encrypted API keys* to
        defaults. The previous contents are also kept as `config.toml.bak` for
        one generation — minus the API keys — so a bad-but-complete write is
        recoverable by hand. See `_write_backup`.

        Args:
            settings: Settings to save

        Returns:
            True if saved successfully, False otherwise
        """
        tmp_file = self.config_file.with_name(self.config_file.name + ".tmp")
        try:
            # Convert to dict and format for TOML
            config_dict = settings.dict()

            # Prepare sensitive data (API keys) for storage
            config_dict = self._remove_sensitive_data(config_dict)

            # Clean None values that can't be serialized to TOML
            config_dict = self._clean_none_values(config_dict)

            with open(tmp_file, "w", encoding="utf-8") as f:
                tomlkit.dump(config_dict, f)
                f.flush()
                os.fsync(f.fileno())

            # Read the outgoing generation once, while it is still there.
            try:
                previous = self.config_file.read_text(encoding="utf-8")
            except OSError:
                previous = None  # first save, or unreadable — nothing to back up
            if previous is not None:
                self._write_backup(previous)

            os.replace(tmp_file, self.config_file)

            logger.info(f"Settings saved to {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _write_backup(self, previous: str) -> None:
        """Keep the outgoing generation of `config.toml` as `config.toml.bak`.

        The API keys are stripped on the way in, so a backup is never more
        readable than the file it backs up. That has to hold for every storage
        format, not just the legacy → DPAPI upgrade that made it obvious — that
        one would otherwise have parked a MachineGuid-encrypted key, decryptable
        by any process on the box, beside the hardened file. A key is
        re-enterable; the rest of the file (models, languages, cache limits) is
        what is actually worth recovering by hand.

        Best-effort by design: failing to write it must not block the save, and
        a backup that can't be refreshed is removed rather than left behind
        holding a stale generation of the same settings.
        """
        backup = self.config_file.with_name(self.config_file.name + ".bak")
        try:
            document = tomlkit.parse(previous)
            for service in KEYED_SERVICES:
                section = document.get(service)
                if isinstance(section, dict):
                    section.pop("api_key", None)
                    section.pop("api_key_salt", None)
            backup.write_text(tomlkit.dumps(document), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — a backup is never load-bearing
            logger.warning("Could not refresh %s: %s", backup, exc)
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_from_environment(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        env_config = {}
        
        # Per-service API key + model overrides, e.g. OPENAI_API_KEY /
        # OPENAI_MODEL.
        for service in KEYED_SERVICES:
            if api_key := os.getenv(f"{service.upper()}_API_KEY"):
                env_config.setdefault(service, {})["api_key"] = api_key
            if model := os.getenv(f"{service.upper()}_MODEL"):
                env_config.setdefault(service, {})["model"] = model

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
        # Look for .env in two well-known locations:
        #   1. The project root (only meaningful in dev — resolved via __file__,
        #      NOT Path.cwd(), which would resolve to C:\Program Files\PDFusion\
        #      on an installed Start-Menu launch and is non-writable / wrong).
        #   2. The user's config dir under AppData.
        # `parents[3]` from this file is `<repo>/src/desktop_pdf_translator/config/manager.py`
        # → repo root in dev; in the PyInstaller bundle it points at the install
        # dir which never contains a .env, so this is a harmless miss there.
        project_root_env = Path(__file__).resolve().parents[3] / ".env"
        env_files = [
            project_root_env,
            self.config_dir / ".env",
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

        for service in KEYED_SERVICES:
            if service in safe_config and isinstance(safe_config[service], dict):
                safe_config[service] = safe_config[service].copy()
                api_key = safe_config[service].get("api_key")
                if not api_key:
                    safe_config[service]["api_key"] = ""
                    safe_config[service].pop("api_key_salt", None)
                    continue
                if isinstance(api_key, str) and api_key.startswith("${"):
                    safe_config[service]["api_key_salt"] = ""
                    continue
                encrypted_key, salt = encrypt_api_key(api_key)
                safe_config[service]["api_key"] = encrypted_key
                if salt:
                    safe_config[service]["api_key_salt"] = salt
                else:
                    safe_config[service].pop("api_key_salt", None)

        return safe_config

    def _decrypt_sensitive_data(self, config_data: Dict[str, Any]) -> None:
        """Turn stored ciphertext back into usable keys, in place.

        Two formats can be on disk: a DPAPI blob (self-contained, no salt) and
        the legacy machine-key Fernet value (needs its `api_key_salt`
        sibling). `encryption.decrypt_api_key` picks by prefix; the salt is
        only required for the legacy one. Re-encryption to DPAPI happens on
        the next `save_settings`.
        """
        for service in KEYED_SERVICES:
            if service not in config_data or not isinstance(config_data[service], dict):
                continue
            service_data = config_data[service]
            encrypted_key = service_data.get("api_key")
            salt = service_data.get("api_key_salt")
            if not isinstance(salt, str):
                salt = ""
            # Either a DPAPI blob, or a legacy value with its salt beside it.
            # `is_encrypted` can't separate the second from plaintext on its own
            # — it answers True for anything that base64-decodes — so the salt
            # is what says a legacy value was stored rather than typed.
            if isinstance(encrypted_key, str) and (
                encrypted_key.startswith(DPAPI_PREFIX)
                or (salt and is_encrypted(encrypted_key))
            ):
                service_data["api_key"] = decrypt_api_key(encrypted_key, salt)
            service_data.pop("api_key_salt", None)

    def _clean_none_values(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Remove None values from config dict to prevent TOML serialization errors."""
        cleaned = {}
        
        for key, value in config_dict.items():
            if value is None:
                continue
            elif isinstance(value, dict):
                cleaned_nested = self._clean_none_values(value)
                if cleaned_nested:  # Only add if not empty after cleaning
                    cleaned[key] = cleaned_nested
            elif isinstance(value, list):
                cleaned_list = [item for item in value if item is not None]
                if cleaned_list:  # Only add if not empty after cleaning
                    cleaned[key] = cleaned_list
            else:
                cleaned[key] = value
        
        return cleaned
    
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
            config_dict = self._clean_none_values(config_dict)
            
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