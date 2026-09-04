"""How API keys are stored, and how `config.toml` is written (#17).

Both are cheap to import — `config/` and `utils/` pull in tomlkit, pydantic and
cryptography, none of the BabelDOC/torch stack the other suites document
avoiding.
"""

from __future__ import annotations

import platform
from unittest import mock

import pytest
import tomlkit

from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.config.models import AppSettings
from desktop_pdf_translator.utils import encryption
from desktop_pdf_translator.utils.encryption import (
    DPAPI_PREFIX,
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


def _make_legacy_ciphertext(key: str = KEY) -> tuple[str, str]:
    """A pre-DPAPI stored value: `(ciphertext, salt_b64)`.

    Produced by the code that used to write it — with DPAPI forced off,
    `encrypt_api_key` *is* the legacy branch — rather than by restating the
    HKDF-over-MachineGuid + double-base64 encoding here, where it could drift
    into a format nothing ever wrote while these tests kept passing.
    """
    with mock.patch.object(encryption, "_dpapi_available", return_value=False):
        return encrypt_api_key(key)


def test_legacy_machine_key_values_still_decrypt():
    """Existing installs must keep working — nobody should have to re-enter a
    key because the storage format changed."""
    legacy, salt_b64 = _make_legacy_ciphertext()

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


def _with_key(key: str = KEY, model: str | None = None) -> AppSettings:
    settings = AppSettings()
    settings.openai.api_key = key
    if model is not None:
        settings.openai.model = model
    return settings


def _no_space(*_args, **_kwargs):
    """Stand-in for a crash or a full disk part-way through the write."""
    raise OSError(28, "No space left on device")


def test_settings_round_trip_through_the_file(manager: ConfigManager):
    assert manager.save_settings(_with_key())

    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.openai.api_key == KEY


def test_the_key_is_not_written_in_the_clear(manager: ConfigManager):
    manager.save_settings(_with_key())
    assert KEY not in manager.config_file.read_text(encoding="utf-8")


def test_a_failed_write_leaves_the_previous_config_intact(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """The regression this is about. `config.toml` used to be written in
    place, so a crash mid-write truncated it — and a truncated config loads as
    defaults, silently discarding every setting *including the API keys*."""
    assert manager.save_settings(_with_key())
    before = manager.config_file.read_text(encoding="utf-8")

    monkeypatch.setattr(tomlkit, "dump", _no_space)
    assert manager.save_settings(_with_key("sk-replacement")) is False

    assert manager.config_file.read_text(encoding="utf-8") == before
    assert ConfigManager(config_dir=manager.config_dir).load_settings().openai.api_key == KEY


def test_a_failed_write_leaves_no_temp_file_behind(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    manager.save_settings(AppSettings())

    monkeypatch.setattr(tomlkit, "dump", _no_space)
    manager.save_settings(AppSettings())

    assert list(manager.config_dir.glob("*.tmp")) == []


def test_the_previous_generation_is_kept_as_a_backup(manager: ConfigManager):
    """A bad-but-complete save is recoverable by hand."""
    manager.save_settings(_with_key(model="gpt-first"))
    manager.save_settings(_with_key("sk-second", model="gpt-second"))

    backup = (manager.config_dir / "config.toml.bak").read_text(encoding="utf-8")
    assert "gpt-first" in backup
    assert "gpt-second" in manager.config_file.read_text(encoding="utf-8")


def test_the_backup_never_holds_key_material(manager: ConfigManager):
    """A backup must never be more readable than the file it backs up.

    Stripping the keys makes that hold for every storage format at once,
    rather than detecting the one migration where it would have been violated.
    The rest of the file — models, languages, cache limits — is what is
    actually worth recovering; a key is re-enterable.
    """
    manager.save_settings(_with_key())
    manager.save_settings(_with_key("sk-second"))

    backup = (manager.config_dir / "config.toml.bak").read_text(encoding="utf-8")
    assert KEY not in backup
    assert "api_key" not in backup  # covers `api_key_salt` too


def test_the_first_save_needs_no_backup(manager: ConfigManager):
    assert manager.save_settings(AppSettings())
    assert not (manager.config_dir / "config.toml.bak").exists()


def _write_legacy_config(config_file) -> tuple[str, str]:
    """Put a pre-DPAPI `config.toml` on disk and return `(ciphertext, salt_b64)`.

    The legacy on-disk shape is the machine-key ciphertext beside the
    `api_key_salt` it needs to decrypt.
    """
    ciphertext, salt_b64 = _make_legacy_ciphertext()
    config_file.write_text(
        f'''[openai]
api_key = "{ciphertext}"
api_key_salt = "{salt_b64}"
''',
        encoding="utf-8",
    )
    return ciphertext, salt_b64


def test_the_backup_of_a_legacy_config_holds_no_legacy_key(manager: ConfigManager):
    """The case that forced the invariant: on the legacy → DPAPI upgrade the
    outgoing generation is exactly the MachineGuid-encrypted value the
    migration exists to retire — decryptable by any process on the box."""
    ciphertext, salt_b64 = _write_legacy_config(manager.config_file)

    settings = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert settings.openai.api_key == KEY  # the legacy value still loads
    assert manager.save_settings(settings)

    backup = (manager.config_dir / "config.toml.bak").read_text(encoding="utf-8")
    assert ciphertext not in backup
    assert salt_b64 not in backup


@on_windows
def test_a_migrating_save_rewrites_the_key_as_a_dpapi_blob(manager: ConfigManager):
    """Nobody re-enters a key because the storage format changed."""
    ciphertext, salt_b64 = _write_legacy_config(manager.config_file)

    settings = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert manager.save_settings(settings)

    saved = manager.config_file.read_text(encoding="utf-8")
    assert DPAPI_PREFIX in saved
    assert "api_key_salt" not in saved
    assert ConfigManager(config_dir=manager.config_dir).load_settings().openai.api_key == KEY
