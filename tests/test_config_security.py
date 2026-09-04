"""How API keys are stored, and how `config.toml` is written (#17).

Both are cheap to import — `config/` and `utils/` pull in tomlkit, pydantic and
cryptography, none of the BabelDOC/torch stack the other suites document
avoiding.
"""

from __future__ import annotations

import platform

import pytest
import tomlkit

from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.config.models import AppSettings
from desktop_pdf_translator.utils import encryption
from desktop_pdf_translator.utils.encryption import (
    DPAPI_PREFIX,
    _derive_key_from_machine,
    decrypt_api_key,
    encrypt_api_key,
    is_encrypted,
)

on_windows = pytest.mark.skipif(
    platform.system() != "Windows", reason="DPAPI is Windows-only"
)

KEY = "sk-test-0123456789abcdef"


# ---------------------------------------------------------------------------
# Key storage
# ---------------------------------------------------------------------------


def test_a_key_round_trips():
    stored, salt = encrypt_api_key(KEY)
    assert stored != KEY
    assert decrypt_api_key(stored, salt) == KEY


def test_stored_form_is_recognised_as_ciphertext():
    stored, _ = encrypt_api_key(KEY)
    assert is_encrypted(stored)


def test_env_placeholders_are_left_alone():
    """`${OPENAI_API_KEY}` is an indirection, not a secret — encrypting it
    would break the substitution it exists for."""
    assert encrypt_api_key("${OPENAI_API_KEY}") == ("${OPENAI_API_KEY}", "")
    assert not is_encrypted("${OPENAI_API_KEY}")


@on_windows
def test_windows_keys_are_stored_with_dpapi():
    """The point of the change: `CryptProtectData` scopes the ciphertext to
    the logged-in user, where the previous MachineGuid-derived key could be
    reproduced by any process on the box."""
    stored, salt = encrypt_api_key(KEY)
    assert stored.startswith(DPAPI_PREFIX)
    # DPAPI blobs are self-contained; there is no salt to store beside them.
    assert salt == ""


@on_windows
def test_a_dpapi_blob_needs_no_salt_to_decrypt():
    stored, _ = encrypt_api_key(KEY)
    assert decrypt_api_key(stored, "") == KEY


def test_legacy_machine_key_values_still_decrypt():
    """Existing installs must keep working — nobody should have to re-enter a
    key because the storage format changed."""
    import base64
    import os

    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    legacy = base64.urlsafe_b64encode(
        Fernet(_derive_key_from_machine(salt)).encrypt(KEY.encode())
    ).decode()
    salt_b64 = base64.urlsafe_b64encode(salt).decode()

    assert not legacy.startswith(DPAPI_PREFIX)
    assert decrypt_api_key(legacy, salt_b64) == KEY


def test_a_legacy_value_without_its_salt_fails_softly():
    """Rather than raising and taking config loading down with it — the key
    just comes back blank and the user re-enters it."""
    assert decrypt_api_key("bm90LXJlYWxseS1lbmNyeXB0ZWQ=", "") is None


def test_garbage_does_not_raise():
    assert decrypt_api_key(DPAPI_PREFIX + "!!!not base64!!!", "") is None


# ---------------------------------------------------------------------------
# Writing config.toml
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager pointed at a throwaway config dir, with the environment's own
    keys out of the way.

    `load_settings` merges `OPENAI_API_KEY` & friends over whatever the file
    said, and `ConfigManager.__init__` loads the repo's `.env` — so on a
    developer machine with real credentials these tests would assert against
    those instead of what they wrote.
    """
    monkeypatch.setattr(ConfigManager, "_load_dotenv", lambda self: None)
    for service in ("OPENAI", "GEMINI", "ANTHROPIC"):
        monkeypatch.delenv(f"{service}_API_KEY", raising=False)
        monkeypatch.delenv(f"{service}_MODEL", raising=False)
    return ConfigManager(config_dir=tmp_path)


def test_settings_round_trip_through_the_file(manager: ConfigManager):
    settings = AppSettings()
    settings.openai.api_key = KEY
    assert manager.save_settings(settings)

    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.openai.api_key == KEY


def test_the_key_is_not_written_in_the_clear(manager: ConfigManager):
    settings = AppSettings()
    settings.openai.api_key = KEY
    manager.save_settings(settings)
    assert KEY not in manager.config_file.read_text(encoding="utf-8")


def test_a_failed_write_leaves_the_previous_config_intact(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """The regression this is about. `config.toml` used to be written in
    place, so a crash mid-write truncated it — and a truncated config loads as
    defaults, silently discarding every setting *including the API keys*."""
    good = AppSettings()
    good.openai.api_key = KEY
    assert manager.save_settings(good)
    before = manager.config_file.read_text(encoding="utf-8")

    def die(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tomlkit, "dump", die)

    doomed = AppSettings()
    doomed.openai.api_key = "sk-replacement"
    assert manager.save_settings(doomed) is False

    assert manager.config_file.read_text(encoding="utf-8") == before
    assert ConfigManager(config_dir=manager.config_dir).load_settings().openai.api_key == KEY


def test_a_failed_write_leaves_no_temp_file_behind(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    manager.save_settings(AppSettings())

    def die(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tomlkit, "dump", die)
    manager.save_settings(AppSettings())

    assert list(manager.config_dir.glob("*.tmp")) == []


def test_the_previous_generation_is_kept_as_a_backup(manager: ConfigManager):
    first = AppSettings()
    first.openai.api_key = KEY
    manager.save_settings(first)

    second = AppSettings()
    second.openai.api_key = "sk-second"
    manager.save_settings(second)

    backup = manager.config_dir / "config.toml.bak"
    assert backup.exists()
    # The backup holds the *previous* write, so a bad-but-complete save is
    # recoverable by hand.
    assert backup.read_text(encoding="utf-8") != manager.config_file.read_text(
        encoding="utf-8"
    )


def test_the_first_save_needs_no_backup(manager: ConfigManager):
    assert manager.save_settings(AppSettings())
    assert not (manager.config_dir / "config.toml.bak").exists()


def _write_legacy_config(config_file, key: str = KEY) -> None:
    """Put a pre-DPAPI `config.toml` on disk: machine-key ciphertext beside the
    `api_key_salt` it needs."""
    import base64
    import os

    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    ciphertext = base64.urlsafe_b64encode(
        Fernet(_derive_key_from_machine(salt)).encrypt(key.encode())
    ).decode()
    config_file.write_text(
        f'[openai]\napi_key = "{ciphertext}"\n'
        f'api_key_salt = "{base64.urlsafe_b64encode(salt).decode()}"\n',
        encoding="utf-8",
    )


@on_windows
def test_a_migrating_save_does_not_leave_the_legacy_key_in_the_backup(
    manager: ConfigManager,
):
    """The upgrade must not park a machine-readable copy of the key next to the
    hardened one. `.bak` holds the *previous* generation, and on this one save
    the previous generation is exactly the MachineGuid-encrypted value the
    migration exists to retire — decryptable by any process on the box."""
    _write_legacy_config(manager.config_file)

    settings = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert settings.openai.api_key == KEY  # the legacy value still loads
    assert manager.save_settings(settings)

    assert DPAPI_PREFIX in manager.config_file.read_text(encoding="utf-8")
    backup = manager.config_dir / "config.toml.bak"
    if backup.exists():
        assert "api_key_salt" not in backup.read_text(encoding="utf-8")


def test_a_non_migrating_save_still_keeps_a_backup(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """Only a real upgrade drops the backup. Where DPAPI is unavailable — off
    Windows, or a broken crypt32 — both generations are legacy, which is not a
    downgrade, and the recovery copy has to survive."""
    monkeypatch.setattr(encryption, "_dpapi_available", lambda: False)

    first = AppSettings()
    first.openai.api_key = KEY
    assert manager.save_settings(first)
    assert "api_key_salt" in manager.config_file.read_text(encoding="utf-8")

    second = AppSettings()
    second.openai.api_key = "sk-second"
    assert manager.save_settings(second)

    assert (manager.config_dir / "config.toml.bak").exists()
