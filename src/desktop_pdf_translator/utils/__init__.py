"""
Utility modules for desktop PDF translator.
"""

from .encryption import (
    DPAPI_PREFIX,
    encrypt_api_key,
    decrypt_api_key,
    is_encrypted,
)
from .paths import adopt_legacy_config, appdata_dir, logs_dir
from .logging_setup import configure_logging

__all__ = [
    "DPAPI_PREFIX",
    "encrypt_api_key",
    "decrypt_api_key",
    "is_encrypted",
    "adopt_legacy_config",
    "appdata_dir",
    "logs_dir",
    "configure_logging",
]
