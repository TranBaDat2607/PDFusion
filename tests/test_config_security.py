"""How API keys are stored, and how `config.toml` is written (#17, #69).

Both are cheap to import — `config/` and `utils/` pull in tomlkit, pydantic and
cryptography, none of the BabelDOC/torch stack the other suites document
avoiding.

The storage scheme is per-platform, and only one of the three can be exercised
for real on any given runner. So the platform-specific assertions are marked,
and the keystore scheme — the one that replaced a key derived from the
machine's *hostname* — is driven through a stand-in backend (`fake_keystore`)
rather than a live Secret Service. That is not a weaker test than the real
thing: what it has to prove is that the ciphertext depends on a secret held
outside `config.toml`, and an in-memory backend shows that as plainly as
gnome-keyring would — on a CI runner, where no keyring is running at all.
"""

from __future__ import annotations

import platform
import sys
from unittest import mock

import pytest
import tomlkit

from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.config.models import AppSettings
from desktop_pdf_translator.utils import encryption
from desktop_pdf_translator.utils.encryption import (
    DPAPI_PREFIX,
    KEYSTORE_PREFIX,
    decrypt_api_key,
    encrypt_api_key,
    is_encrypted,
)

#: The real `_usable_keyring`, captured at import — before `conftest.py`'s
#: autouse `_no_real_keystore` fixture replaces it for every test. The one test
#: below that is *about* that function has to call the original, and without
#: this its assertions would pass against the stub instead (which also answers
#: `None`, so the failure would look like a pass).
_real_usable_keyring = encryption._usable_keyring

on_windows = pytest.mark.skipif(
    platform.system() != "Windows", reason="DPAPI is Windows-only"
)
off_windows = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Windows uses DPAPI; the keystore path is for Linux and macOS",
)

KEY = "sk-test-0123456789abcdef"


class _FakeKeyring:
    """A `keyring` stand-in: one dict, and the two calls this module makes."""

    def __init__(self, backend_module: str = "keyring.backends.SecretService"):
        self.stored: dict[tuple[str, str], str] = {}
        self._backend = type("Backend", (), {"__module__": backend_module})

    def get_keyring(self):
        return self._backend()

    def get_password(self, service: str, username: str):
        return self.stored.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.stored[(service, username)] = password


@pytest.fixture
def fake_keystore(monkeypatch: pytest.MonkeyPatch):
    """A working OS keystore, on whatever platform the suite is running.

    `_keystore_supported()` and `_dpapi_available()` are both forced: without
    them this fixture would silently do nothing on a Windows runner and the
    tests below would pass by exercising DPAPI instead of the thing they name.
    """
    fake = _FakeKeyring()
    monkeypatch.setattr(encryption, "_keystore_supported", lambda: True)
    monkeypatch.setattr(encryption, "_dpapi_available", lambda: False)
    monkeypatch.setattr(encryption, "_usable_keyring", lambda: fake)
    encryption._reset_master_key_cache()
    yield fake
    encryption._reset_master_key_cache()


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


@off_windows
def test_off_windows_the_keystore_is_the_scheme_that_applies():
    """Which branch the *real* platform takes, asserted without taking it.

    Actually running `encrypt_api_key` here would file a master key in the
    developer's login keyring — real, persistent state, which `conftest.py`'s
    `_no_real_keystore` exists to keep every test out of. So this checks the
    decision and leaves the mechanism to `fake_keystore` below.
    """
    assert encryption._keystore_supported()
    assert not encryption._dpapi_available()


@on_windows
def test_on_windows_the_keystore_is_not_reached_at_all():
    """DPAPI is strictly better there — no second secret to lose, no prompt —
    and a keystore branch that could fire on Windows would be a fourth format
    to migrate later."""
    assert not encryption._keystore_supported()


def test_a_keystore_key_round_trips(fake_keystore):
    stored, salt = encrypt_api_key(KEY)

    assert stored.startswith(KEYSTORE_PREFIX)
    assert decrypt_api_key(stored, salt) == KEY
    assert is_encrypted(stored)


def test_the_secret_that_protects_the_file_is_not_in_the_file(fake_keystore):
    """The whole reason this scheme exists. The legacy one derived its key from
    `platform.node() + platform.machine()` and stored the salt beside the
    ciphertext, so anything that could read `config.toml` could reproduce the
    key. Here the ciphertext is worthless without the keystore entry."""
    stored, _ = encrypt_api_key(KEY)

    assert fake_keystore.stored, "nothing was filed in the keystore"
    fake_keystore.stored.clear()
    encryption._reset_master_key_cache()

    assert decrypt_api_key(stored, "") is None


def test_a_missing_master_key_is_never_replaced_with_a_fresh_one(fake_keystore):
    """Minting one on the decrypt path would turn "the keyring is locked" —
    temporary — into "those keys are gone" — permanent."""
    stored, _ = encrypt_api_key(KEY)
    fake_keystore.stored.clear()
    encryption._reset_master_key_cache()

    assert decrypt_api_key(stored, "") is None
    assert fake_keystore.stored == {}


def test_every_key_shares_one_master_key(fake_keystore):
    """One entry the user can see and revoke in Seahorse / Keychain Access,
    not one per provider."""
    encrypt_api_key(KEY)
    encrypt_api_key("sk-another")

    assert len(fake_keystore.stored) == 1


def test_an_unusable_backend_is_recognised_as_unusable(monkeypatch: pytest.MonkeyPatch):
    """`keyring.backends.fail` is the sentinel keyring hands back when it found
    nothing; `keyrings.alt` is a real, installable distribution whose backends
    keep secrets in a plaintext or lightly-obfuscated file — the exact property
    the legacy scheme is being retired for. Neither may be mistaken for a
    keystore."""
    monkeypatch.setattr(encryption, "_keystore_supported", lambda: True)
    for module in ("keyring.backends.fail", "keyrings.alt.file"):
        monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring(backend_module=module))
        assert _real_usable_keyring() is None, module

    real = _FakeKeyring(backend_module="keyring.backends.macOS")
    monkeypatch.setitem(sys.modules, "keyring", real)
    assert _real_usable_keyring() is real


def test_without_a_keystore_the_legacy_scheme_still_stores_something_usable(
    monkeypatch: pytest.MonkeyPatch,
):
    """A headless Linux box with no D-Bus session — most obviously CI. The key
    is only obfuscated there, which `encrypt_api_key` warns about, but the app
    still starts and the key still works. Falling through to it *with* that
    warning is the documented behaviour; failing to save would not be."""
    monkeypatch.setattr(encryption, "_dpapi_available", lambda: False)
    monkeypatch.setattr(encryption, "_usable_keyring", lambda: None)
    encryption._reset_master_key_cache()

    stored, salt = encrypt_api_key(KEY)

    assert not stored.startswith((DPAPI_PREFIX, KEYSTORE_PREFIX))
    assert salt, "the legacy path stores a salt beside the ciphertext"
    assert decrypt_api_key(stored, salt) == KEY


def _make_legacy_ciphertext(key: str = KEY) -> tuple[str, str]:
    """A pre-DPAPI stored value: `(ciphertext, salt_b64)`.

    Produced by the code that used to write it — with DPAPI and the keystore
    both forced off, `encrypt_api_key` *is* the legacy branch — rather than by
    restating the HKDF-over-machine-id + double-base64 encoding here, where it
    could drift into a format nothing ever wrote while these tests kept
    passing.
    """
    with mock.patch.object(
        encryption, "_dpapi_available", return_value=False
    ), mock.patch.object(encryption, "_usable_keyring", return_value=None):
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


def test_a_migrating_save_rewrites_the_key_in_the_keystore_scheme(
    manager: ConfigManager, fake_keystore
):
    """The off-Windows half of the migration: a config written by the hostname
    scheme loads, and the next save replaces it with a value the file alone
    cannot decrypt. Nobody re-enters a key."""
    _write_legacy_config(manager.config_file)

    settings = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert settings.openai.api_key == KEY
    assert manager.save_settings(settings)

    saved = manager.config_file.read_text(encoding="utf-8")
    assert KEYSTORE_PREFIX in saved
    assert "api_key_salt" not in saved
    assert ConfigManager(config_dir=manager.config_dir).load_settings().openai.api_key == KEY


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


# ---------------------------------------------------------------------------
# A key the keystore cannot open right now
# ---------------------------------------------------------------------------


def _lock_the_keystore(fake_keystore) -> dict[tuple[str, str], str]:
    """Take the master key away and return it, so it can be handed back.

    Stands in for every way the entry stops being readable without stopping
    being *there*: a locked keyring, an unlock prompt the user dismissed, a
    login with no session bus, a `config.toml` carried to another machine.
    The cache has to be reset too, or the key stays available in-process and
    the test proves nothing.
    """
    entries = dict(fake_keystore.stored)
    fake_keystore.stored.clear()
    encryption._reset_master_key_cache()
    return entries


def _unlock_the_keystore(fake_keystore, entries) -> None:
    fake_keystore.stored.update(entries)
    encryption._reset_master_key_cache()


def test_a_key_that_cannot_be_decrypted_survives_an_unrelated_save(
    manager: ConfigManager, fake_keystore
):
    """The regression: a locked keystore used to cost the user every key.

    The decrypt path deliberately never mints a replacement master key, so
    that "the keystore is locked" stays temporary. But the key came back
    blank, and the save path could not tell blank-because-unreadable from
    blank-because-cleared — so the next `PUT /config` for anything at all
    (Chat on, a new target language) wrote `api_key = ""`, and since the
    backup strips keys, the ciphertext went from both files at once.
    """
    assert manager.save_settings(_with_key(model="gpt-first"))
    stored_value = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))[
        "openai"
    ]["api_key"]
    assert str(stored_value).startswith(KEYSTORE_PREFIX)

    entries = _lock_the_keystore(fake_keystore)

    locked = ConfigManager(config_dir=manager.config_dir)
    settings = locked.load_settings()
    assert settings.openai.api_key is None, "an unreadable key is never handed out"

    settings.openai.model = "gpt-second"  # an unrelated change, saved
    assert locked.save_settings(settings)

    saved = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == stored_value
    assert saved["openai"]["model"] == "gpt-second"

    # And when the keystore comes back, nobody re-enters anything.
    _unlock_the_keystore(fake_keystore, entries)
    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.openai.api_key == KEY
    assert reloaded.openai.model == "gpt-second"


def test_a_key_that_cannot_be_decrypted_never_becomes_the_key(
    manager: ConfigManager, fake_keystore
):
    """The second half of the same defect, and the sharper one.

    The failed decrypt was assigned straight back into the config table — and
    those are tomlkit tables, which refuse a None value. The raise aborted the
    whole file branch of `load_settings`, which left the *ciphertext* standing
    as `openai.api_key`: sent to the provider as a credential, and reported by
    `GET /config` as a key that is configured. The rest of the file went with
    it, silently reverting to defaults.
    """
    assert manager.save_settings(_with_key(model="gpt-configured"))
    stored_value = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))[
        "openai"
    ]["api_key"]
    _lock_the_keystore(fake_keystore)

    settings = ConfigManager(config_dir=manager.config_dir).load_settings()

    assert settings.openai.api_key is None
    assert settings.openai.api_key != str(stored_value)
    assert settings.openai.model == "gpt-configured", "the file still loaded"


def test_the_backup_of_a_preserved_key_still_holds_no_key_material(
    manager: ConfigManager, fake_keystore
):
    """Preserving the ciphertext in `config.toml` must not leak it into
    `config.toml.bak`, which is held to "never more readable than the file it
    backs up" for every format at once."""
    assert manager.save_settings(_with_key())
    _lock_the_keystore(fake_keystore)

    locked = ConfigManager(config_dir=manager.config_dir)
    assert locked.save_settings(locked.load_settings())

    backup = (manager.config_dir / "config.toml.bak").read_text(encoding="utf-8")
    assert "api_key" not in backup  # covers `api_key_salt` too


def test_clearing_a_key_that_cannot_be_decrypted_still_clears_it(
    manager: ConfigManager, fake_keystore
):
    """The other half of the rule. Preserving is for a key nobody touched;
    an explicit set-or-clear drops the record first (`PUT /config` calls
    `forget_unreadable_key` whenever the body carries `api_key` at all), so
    the user can still get rid of a key they can no longer read."""
    assert manager.save_settings(_with_key())
    _lock_the_keystore(fake_keystore)

    locked = ConfigManager(config_dir=manager.config_dir)
    settings = locked.load_settings()
    assert locked.has_unreadable_key("openai")

    locked.forget_unreadable_key("openai")
    settings.openai.api_key = None
    assert locked.save_settings(settings)

    saved = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == ""
    assert "api_key_salt" not in saved["openai"]


def test_resetting_to_defaults_does_not_write_a_preserved_key_back(
    manager: ConfigManager, fake_keystore
):
    assert manager.save_settings(_with_key())
    _lock_the_keystore(fake_keystore)

    locked = ConfigManager(config_dir=manager.config_dir)
    locked.load_settings()
    locked.reset_to_defaults()

    saved = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == ""
