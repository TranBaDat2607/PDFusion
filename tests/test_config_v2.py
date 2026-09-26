"""Config v2: one table per provider, and a model named by provider + id (#85).

v1 kept a fixed section per provider (`[openai]`, `[gemini]`, …) each with its
own `model`, and said which one translates with `translation.preferred_service`.
v2 keeps every provider under `[providers.<id>]` with an ordered
`enabled_models` list, and names a model as `{provider, model}` wherever one is
chosen: `translation.model`, and `rag.answer_model` for chat.

A v1 file converts as it loads and is written back as v2 on the next save.
What must not move in the conversion is how keys are kept (#17, #32, #69):
encrypted on save, a key this process cannot decrypt written back byte for
byte, never a key in `config.toml.bak`, and never an environment key sent to a
custom endpoint.

The names #85 introduces (`ModelRef`, `ProviderSettings`, `model_for`, …) are
looked up on the module inside each test rather than imported at the top, so
that each test fails on its own behaviour, not on one shared ImportError.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
import tomlkit
from cryptography.fernet import Fernet
from pydantic import ValidationError

from desktop_pdf_translator.config import models as config_models
from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.providers.registry import PROVIDERS, provider
from desktop_pdf_translator.utils import encryption
from desktop_pdf_translator.utils.encryption import KEYSTORE_PREFIX, encrypt_api_key

OLLAMA = "http://localhost:11434/v1"
# Every registry entry, not a list of its own: a provider added to the
# registry must need no test edited (#88).
PROVIDER_IDS = {spec.id for spec in PROVIDERS}


def ModelRef(**fields):
    return config_models.ModelRef(**fields)


def ProviderSettings(**fields):
    return config_models.ProviderSettings(**fields)


def AppSettings(**fields):
    return config_models.AppSettings(**fields)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """A `keyring` stand-in, as in `test_config_security.py`."""

    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}
        self._backend = type("Backend", (), {"__module__": "keyring.backends.SecretService"})

    def get_keyring(self):
        return self._backend()

    def get_password(self, service: str, username: str):
        return self.stored.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.stored[(service, username)] = password


@pytest.fixture
def fake_keystore(monkeypatch: pytest.MonkeyPatch):
    """A working OS keystore on every platform, DPAPI forced off, so the
    ciphertext is the `keystore:` format wherever the suite runs."""
    fake = _FakeKeyring()
    monkeypatch.setattr(encryption, "_keystore_supported", lambda: True)
    monkeypatch.setattr(encryption, "_dpapi_available", lambda: False)
    monkeypatch.setattr(encryption, "_usable_keyring", lambda: fake)
    encryption._reset_master_key_cache()
    yield fake
    encryption._reset_master_key_cache()


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager over a throwaway dir with the developer's own credentials out
    of the way — `load_settings` merges `OPENAI_API_KEY` over the file, and
    `__init__` loads the repo's `.env`."""
    monkeypatch.setattr(ConfigManager, "_load_dotenv", lambda self: None)
    for service in ("OPENAI", "GEMINI", "ANTHROPIC"):
        monkeypatch.delenv(f"{service}_API_KEY", raising=False)
        monkeypatch.delenv(f"{service}_MODEL", raising=False)
    for var in ("DEBUG_MODE", "MAX_PAGES", "MAX_FILE_SIZE_MB"):
        monkeypatch.delenv(var, raising=False)
    return ConfigManager(config_dir=tmp_path / "PDFusion")


def write(manager: ConfigManager, text: str) -> None:
    manager.config_file.write_text(text, encoding="utf-8")


def saved(manager: ConfigManager) -> tomlkit.TOMLDocument:
    return tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))


def fresh(manager: ConfigManager) -> ConfigManager:
    """A second process reading the same directory."""
    return ConfigManager(config_dir=manager.config_dir)


def _ciphertext_under_another_master_key(key: str) -> str:
    """A `keystore:` value the keystore in use cannot open: what a config
    carried from another machine, or written before the keyring entry was
    replaced, holds. Built the way `encrypt_api_key` builds one, but under a
    master key this process never sees."""
    token = Fernet(Fernet.generate_key()).encrypt(key.encode("utf-8"))
    return KEYSTORE_PREFIX + base64.urlsafe_b64encode(token).decode("utf-8")


# ---------------------------------------------------------------------------
# the headline: a v1 file with keys survives load → save → load
# ---------------------------------------------------------------------------


def test_v1_keys_survive_the_conversion_and_an_unreadable_one_is_kept_verbatim(
    manager: ConfigManager, fake_keystore
):
    """Three encrypted keys, one of which this process cannot decrypt. The
    readable two come through intact; the unreadable one is never blanked, and
    lands in the v2 file byte for byte — on the converting save and on the
    save after it."""
    openai_stored, _ = encrypt_api_key("sk-openai-key")
    gemini_stored, _ = encrypt_api_key("sk-gemini-key")
    assert openai_stored.startswith(KEYSTORE_PREFIX)
    unreadable = _ciphertext_under_another_master_key("sk-anthropic-key")
    write(
        manager,
        f'''[openai]
api_key = "{openai_stored}"
model = "gpt-4.1"

[gemini]
api_key = "{gemini_stored}"
model = "gemini-3.8-flash"

[anthropic]
api_key = "{unreadable}"
model = "claude-sonnet-4-6"

[translation]
preferred_service = "openai"
''',
    )

    settings = manager.load_settings()
    assert settings.providers["openai"].api_key == "sk-openai-key"
    assert settings.providers["gemini"].api_key == "sk-gemini-key"
    assert settings.providers["anthropic"].api_key is None
    assert manager.has_unreadable_key("anthropic")

    assert manager.save_settings(settings)
    document = saved(manager)
    assert "anthropic" not in document, "the converting save writes v2"
    assert document["providers"]["anthropic"]["api_key"] == unreadable
    written = manager.config_file.read_text(encoding="utf-8")
    assert "sk-openai-key" not in written and "sk-gemini-key" not in written

    second = fresh(manager)
    reloaded = second.load_settings()
    assert reloaded.providers["openai"].api_key == "sk-openai-key"
    assert reloaded.providers["gemini"].api_key == "sk-gemini-key"
    assert reloaded.providers["anthropic"].api_key is None
    assert second.has_unreadable_key("anthropic")

    # And once more, v2 → v2.
    assert second.save_settings(reloaded)
    assert saved(manager)["providers"]["anthropic"]["api_key"] == unreadable
    again = fresh(manager).load_settings()
    assert again.providers["openai"].api_key == "sk-openai-key"
    assert again.providers["gemini"].api_key == "sk-gemini-key"


# ---------------------------------------------------------------------------
# v1 → v2
# ---------------------------------------------------------------------------

V1_FILE = f'''[openai]
model = "llama3.2:3b"
base_url = "{OLLAMA}"
temperature = 1.5
max_tokens = 2048
max_qps = 2.0

[gemini]
model = "gemini-3.5-flash"
temperature = 0.7

[anthropic]
model = "claude-haiku-4-5-20251001"
max_tokens = 8000
max_qps = 0.5

[argos]
model = "argostranslate"

[translation]
preferred_service = "anthropic"
default_target_lang = "ja"
'''


def _assert_converted(settings) -> None:
    openai = settings.providers["openai"]
    assert openai.enabled_models == ["llama3.2:3b"]
    assert openai.base_url == OLLAMA
    assert openai.temperature == 1.5
    assert openai.max_tokens == 2048
    assert openai.max_qps == 2.0

    gemini = settings.providers["gemini"]
    assert gemini.enabled_models == ["gemini-3.5-flash"]
    assert gemini.temperature == 0.7

    anthropic = settings.providers["anthropic"]
    assert anthropic.enabled_models == ["claude-haiku-4-5-20251001"]
    assert anthropic.max_tokens == 8000
    assert anthropic.max_qps == 0.5

    assert settings.translation.model == ModelRef(
        provider="anthropic", model="claude-haiku-4-5-20251001"
    )
    assert settings.translation.default_target_lang.value == "ja"


def test_a_v1_file_loads_as_v2(manager: ConfigManager):
    write(manager, V1_FILE)

    _assert_converted(manager.load_settings())


def test_the_save_after_a_v1_load_writes_v2(manager: ConfigManager):
    write(manager, V1_FILE)

    assert manager.save_settings(manager.load_settings())

    document = saved(manager)
    for provider_id in PROVIDER_IDS:
        assert provider_id not in document, f"top-level [{provider_id}] left behind"
    assert "preferred_service" not in document["translation"]
    assert document["translation"]["model"] == {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
    }
    assert document["providers"]["openai"]["enabled_models"] == ["llama3.2:3b"]
    assert document["providers"]["openai"]["base_url"] == OLLAMA
    _assert_converted(fresh(manager).load_settings())


def test_a_v1_file_without_a_preferred_service_translates_with_argos(
    manager: ConfigManager,
):
    write(manager, '[openai]\nmodel = "gpt-5.6-luna"\n')

    settings = manager.load_settings()

    assert settings.translation.model == ModelRef(provider="argos", model="argostranslate")
    assert settings.providers["openai"].enabled_models == ["gpt-5.6-luna"]


def test_a_v1_preferred_service_with_no_model_of_its_own_gets_the_default(
    manager: ConfigManager,
):
    """A v1 section with no `model` meant the provider's default — that is
    what v1 itself would have translated with."""
    write(manager, '[translation]\npreferred_service = "gemini"\n')

    settings = manager.load_settings()

    assert settings.translation.model == ModelRef(
        provider="gemini", model=provider("gemini").default_model
    )


# ---------------------------------------------------------------------------
# retired models
# ---------------------------------------------------------------------------


def test_a_retired_v1_model_converts_to_the_current_default(manager: ConfigManager):
    write(
        manager,
        '[gemini]\nmodel = "gemini-1.5-flash"\n\n'
        '[anthropic]\nmodel = "claude-3-5-sonnet-20241022"\n\n'
        '[translation]\npreferred_service = "gemini"\n',
    )

    settings = manager.load_settings()

    gemini_default = provider("gemini").default_model
    assert settings.translation.model == ModelRef(provider="gemini", model=gemini_default)
    assert settings.providers["gemini"].enabled_models == [gemini_default]
    assert settings.providers["anthropic"].enabled_models == [
        provider("anthropic").default_model
    ]


def test_retired_models_in_v2_model_refs_are_replaced(manager: ConfigManager):
    write(
        manager,
        '[translation]\nmodel = { provider = "gemini", model = "gemini-2.0-flash" }\n\n'
        '[rag]\nanswer_model = { provider = "anthropic", model = "claude-3-5-sonnet-20241022" }\n',
    )

    settings = manager.load_settings()

    assert settings.translation.model == ModelRef(
        provider="gemini", model=provider("gemini").default_model
    )
    assert settings.rag.answer_model == ModelRef(
        provider="anthropic", model=provider("anthropic").default_model
    )


def test_retired_enabled_models_are_replaced_without_duplicates(manager: ConfigManager):
    """A retired id becomes the default in its place; when the default is
    already on the list it appears once. An id the app has never heard of is
    the user's and stays."""
    gemini_default = provider("gemini").default_model
    anthropic_default = provider("anthropic").default_model
    write(
        manager,
        f'''[providers.gemini]
enabled_models = ["gemini-1.5-flash", "{gemini_default}", "gemini-3.5-flash"]

[providers.anthropic]
enabled_models = ["my-proxy-model", "claude-3-5-sonnet-20241022"]
''',
    )

    settings = manager.load_settings()

    assert settings.providers["gemini"].enabled_models == [gemini_default, "gemini-3.5-flash"]
    assert settings.providers["anthropic"].enabled_models == [
        "my-proxy-model",
        anthropic_default,
    ]


# ---------------------------------------------------------------------------
# config.toml.bak
# ---------------------------------------------------------------------------


def test_the_backup_holds_no_key_material_from_a_v1_or_a_v2_generation(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """No keystore here (conftest) and DPAPI forced off, so keys are stored in
    the legacy form with an `api_key_salt` beside them — both fields must go
    from the backup, from v1's top-level tables and from v2's nested
    `[providers.<id>]` ones."""
    monkeypatch.setattr(encryption, "_dpapi_available", lambda: False)
    openai_stored, openai_salt = encrypt_api_key("sk-openai-key")
    anthropic_stored, anthropic_salt = encrypt_api_key("sk-anthropic-key")
    assert openai_salt, "expected the legacy scheme, which stores a salt"
    write(
        manager,
        f'''[openai]
api_key = "{openai_stored}"
api_key_salt = "{openai_salt}"
model = "gpt-5.6-luna"

[anthropic]
api_key = "{anthropic_stored}"
api_key_salt = "{anthropic_salt}"
''',
    )

    # The outgoing generation is v1.
    assert manager.save_settings(manager.load_settings())
    backup_file = manager.config_dir / "config.toml.bak"
    backup = backup_file.read_text(encoding="utf-8")
    assert "gpt-5.6-luna" in backup, "the rest of the file is what is worth keeping"
    assert "api_key" not in backup  # covers `api_key_salt` too
    assert "providers" in saved(manager), "the converting save writes v2"

    # The outgoing generation is now v2.
    second = fresh(manager)
    assert second.save_settings(second.load_settings())
    backup = backup_file.read_text(encoding="utf-8")
    assert "gpt-5.6-luna" in backup
    assert "api_key" not in backup
    assert "providers" in tomlkit.parse(backup)
    assert fresh(manager).load_settings().providers["openai"].api_key == "sk-openai-key"


# ---------------------------------------------------------------------------
# environment keys
# ---------------------------------------------------------------------------


def test_an_environment_key_never_reaches_a_saved_custom_endpoint(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    write(
        manager,
        f'''[providers.openai]
api_key = "ollama"
base_url = "{OLLAMA}"

[providers.anthropic]
base_url = "http://localhost:11434"
''',
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    settings = manager.load_settings()

    assert settings.providers["openai"].api_key == "ollama"
    assert settings.providers["openai"].base_url == OLLAMA
    assert settings.providers["anthropic"].api_key is None
    assert settings.providers["anthropic"].base_url == "http://localhost:11434"


def test_an_environment_key_is_used_with_the_providers_own_endpoint(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    write(
        manager,
        '[providers.openai]\napi_key = "sk-saved"\nenabled_models = ["gpt-4.1"]\n',
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini-env")

    settings = manager.load_settings()

    assert settings.providers["openai"].api_key == "sk-from-env"
    assert settings.providers["openai"].enabled_models == ["gpt-4.1"]
    assert settings.providers["gemini"].api_key == "sk-gemini-env"


# ---------------------------------------------------------------------------
# ModelRef
# ---------------------------------------------------------------------------


def test_a_model_ref_names_a_registry_provider():
    with pytest.raises(ValidationError):
        ModelRef(provider="mistral", model="mistral-large")


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_model_ref_needs_a_model(blank: str):
    with pytest.raises(ValidationError):
        ModelRef(provider="openai", model=blank)


def test_a_model_ref_strips_its_model():
    assert ModelRef(provider="openai", model="  gpt-4.1 ").model == "gpt-4.1"


def test_an_argos_model_ref_is_always_argostranslate():
    assert ModelRef(provider="argos", model="anything").model == "argostranslate"


# ---------------------------------------------------------------------------
# ProviderSettings, and the rules each provider brings
# ---------------------------------------------------------------------------


def test_provider_settings_defaults():
    settings = ProviderSettings()

    assert settings.api_key is None
    assert settings.base_url is None
    assert settings.enabled_models == []
    assert settings.temperature == 0.3
    assert settings.max_tokens is None
    assert settings.max_qps is None


def test_a_provider_endpoint_is_normalized():
    assert ProviderSettings(base_url=f" {OLLAMA}/ ").base_url == OLLAMA
    assert ProviderSettings(base_url="").base_url is None


def test_a_provider_endpoint_must_be_a_web_url():
    with pytest.raises(ValidationError):
        ProviderSettings(base_url="localhost:11434")


def test_every_registry_provider_has_settings():
    assert set(AppSettings().providers) == PROVIDER_IDS
    only_openai = AppSettings(providers={"openai": {"api_key": "sk"}})
    assert set(only_openai.providers) == PROVIDER_IDS
    assert only_openai.providers["openai"].api_key == "sk"
    assert only_openai.providers["gemini"].api_key is None


def test_an_unknown_provider_id_is_refused():
    with pytest.raises(ValidationError):
        AppSettings(providers={"mistral": {"api_key": "sk"}})


@pytest.mark.parametrize("provider_id", ["gemini", "anthropic"])
def test_a_temperature_above_one_is_refused_where_the_provider_caps_it(provider_id: str):
    with pytest.raises(ValidationError):
        AppSettings(providers={provider_id: {"temperature": 1.5}})


def test_openai_takes_a_temperature_up_to_two():
    settings = AppSettings(providers={"openai": {"temperature": 2.0}})
    assert settings.providers["openai"].temperature == 2.0
    with pytest.raises(ValidationError):
        AppSettings(providers={"openai": {"temperature": 2.5}})


def test_gemini_takes_no_endpoint():
    with pytest.raises(ValidationError):
        AppSettings(providers={"gemini": {"base_url": "http://localhost:8080"}})


def test_max_tokens_defaults_per_provider():
    settings = AppSettings()
    assert settings.providers["anthropic"].max_tokens == 4000
    assert settings.providers["openai"].max_tokens is None


# ---------------------------------------------------------------------------
# translation / rag defaults
# ---------------------------------------------------------------------------


def test_the_default_translation_model_is_argos():
    assert AppSettings().translation.model == ModelRef(
        provider="argos", model="argostranslate"
    )


def test_there_is_no_answer_model_by_default():
    assert AppSettings().rag.answer_model is None


def test_translation_has_no_preferred_service_field():
    assert "preferred_service" not in config_models.TranslationSettings.model_fields


# ---------------------------------------------------------------------------
# model_for / remember_model / has_api_key
# ---------------------------------------------------------------------------


def _settings_for_model_for():
    return AppSettings(
        providers={
            "openai": {"enabled_models": ["gpt-4.1", "gpt-5.6-sol"]},
            "anthropic": {"enabled_models": ["claude-opus-5", "claude-sonnet-4-6"]},
        },
        translation={"model": {"provider": "openai", "model": "gpt-5.6-sol"}},
    )


def test_model_for_the_translation_provider_is_the_translation_model():
    assert _settings_for_model_for().model_for("openai") == "gpt-5.6-sol"


def test_model_for_another_provider_is_its_first_enabled_model():
    assert _settings_for_model_for().model_for("anthropic") == "claude-opus-5"


def test_model_for_a_provider_with_no_enabled_models_is_its_default():
    assert _settings_for_model_for().model_for("gemini") == provider("gemini").default_model


def test_model_for_argos_is_argostranslate():
    assert _settings_for_model_for().model_for("argos") == "argostranslate"


def test_remember_model_moves_a_known_model_to_the_front():
    settings = AppSettings(providers={"openai": {"enabled_models": ["a", "b", "c"]}})

    settings.remember_model("openai", "c")

    assert settings.providers["openai"].enabled_models == ["c", "a", "b"]
    assert settings.model_for("openai") == "c"


def test_remember_model_inserts_a_new_model_at_the_front():
    settings = AppSettings(providers={"openai": {"enabled_models": ["a", "b"]}})

    settings.remember_model("openai", "new")
    settings.remember_model("gemini", "gemini-x")

    assert settings.providers["openai"].enabled_models == ["new", "a", "b"]
    assert settings.providers["gemini"].enabled_models == ["gemini-x"]


def test_remember_model_keeps_no_duplicates():
    settings = AppSettings(providers={"openai": {"enabled_models": ["a", "b"]}})

    settings.remember_model("openai", "a")
    settings.remember_model("openai", "b")

    assert settings.providers["openai"].enabled_models == ["b", "a"]


def test_has_api_key_reads_the_provider_table():
    settings = AppSettings(providers={"openai": {"api_key": "sk-openai"}})

    assert settings.has_api_key("openai") is True
    assert settings.has_api_key("gemini") is False
    assert settings.has_api_key("argos") is True


# ---------------------------------------------------------------------------
# added after review
# ---------------------------------------------------------------------------


def test_a_legacy_key_is_kept_when_the_new_table_has_none(manager: ConfigManager):
    """A file holding both shapes for a provider keeps the new table, but a
    key only the old table has is still the provider's key: dropping it would
    lose it for good on the next save."""
    write(
        manager,
        '[openai]\napi_key = "sk-legacy"\nmodel = "gpt-4o-mini"\n\n'
        '[providers.openai]\nenabled_models = ["gpt-4.1"]\n',
    )

    settings = manager.load_settings()

    assert settings.providers["openai"].api_key == "sk-legacy"
    assert settings.model_for("openai") == "gpt-4.1"


def test_a_provider_that_takes_no_key_keeps_none():
    """A key hand-edited into a keyless provider's table (Ollama's) would be
    sent in place of its placeholder, and could never be cleared: the API
    refuses a key for it both ways, while the endpoint rule treats a saved key
    as one to guard (#88)."""
    settings = AppSettings(providers={"ollama": {"api_key": "sk-hand-edited"}})

    assert settings.providers["ollama"].api_key is None
