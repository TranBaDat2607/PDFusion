"""How `ConfigManager` *reads* a config (`config/manager.py`).

`test_config_security.py` covers the write side — encryption, the atomic
replace, the key-stripped backup. This is the other half: the precedence rules
between file / environment / defaults, and the recovery path that exists
because a config that fails validation used to be discarded wholesale.

That recovery is the reason this file is worth its length. A `config.toml`
loading as defaults is silent: the app starts, every setting is back to
factory, and the user finds out when a translation comes out in the wrong
language or an API key they pasted last week is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from desktop_pdf_translator.config.manager import ConfigManager
from desktop_pdf_translator.config.models import (
    AppSettings,
    LanguageCode,
    TranslationService,
)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "PDFusion"


@pytest.fixture
def manager(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """A manager over a throwaway dir with the developer's own credentials out
    of the way — `load_settings` merges `OPENAI_API_KEY` over whatever the file
    said, and `__init__` loads the repo's `.env`."""
    monkeypatch.setattr(ConfigManager, "_load_dotenv", lambda self: None)
    for service in ("OPENAI", "GEMINI", "ANTHROPIC"):
        monkeypatch.delenv(f"{service}_API_KEY", raising=False)
        monkeypatch.delenv(f"{service}_MODEL", raising=False)
    for var in ("DEBUG_MODE", "MAX_PAGES", "MAX_FILE_SIZE_MB"):
        monkeypatch.delenv(var, raising=False)
    return ConfigManager(config_dir=config_dir)


def write_config(manager: ConfigManager, document: dict) -> None:
    manager.config_file.write_text(tomlkit.dumps(document), encoding="utf-8")


# ---------------------------------------------------------------------------
# the empty cases
# ---------------------------------------------------------------------------


def test_a_missing_config_file_loads_defaults(manager: ConfigManager):
    settings = manager.load_settings()
    assert settings.translation.default_target_lang == LanguageCode.VIETNAMESE
    assert settings.openai.api_key is None


def test_the_config_directory_is_created_on_construction(
    manager: ConfigManager, config_dir: Path
):
    assert config_dir.exists()


def test_settings_are_loaded_once_and_memoized(manager: ConfigManager):
    assert manager.settings is manager.settings


# ---------------------------------------------------------------------------
# file values
# ---------------------------------------------------------------------------


def test_file_values_override_the_model_defaults(manager: ConfigManager):
    write_config(
        manager,
        {
            "openai": {"model": "gpt-4o-mini", "temperature": 0.9},
            "translation": {"default_target_lang": "ja"},
        },
    )
    settings = manager.load_settings()
    assert settings.openai.model == "gpt-4o-mini"
    assert settings.openai.temperature == 0.9
    assert settings.translation.default_target_lang == LanguageCode.JAPANESE


def test_sections_absent_from_the_file_keep_their_defaults(manager: ConfigManager):
    write_config(manager, {"openai": {"model": "gpt-4o-mini"}})
    settings = manager.load_settings()
    assert settings.gemini.model == "gemini-1.5-flash"
    assert settings.translation.cache_translations is True


def test_a_corrupt_file_does_not_stop_the_sidecar(manager: ConfigManager):
    """Startup runs through this. A parse failure has to degrade to defaults,
    not raise out of the lifespan — which exits the process after READY has
    already been printed."""
    manager.config_file.write_text("this is not [ toml", encoding="utf-8")
    assert manager.load_settings().openai.model == "gpt-4.1"


# ---------------------------------------------------------------------------
# environment overrides
# ---------------------------------------------------------------------------


def test_the_environment_wins_over_the_file(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    write_config(manager, {"openai": {"model": "gpt-4o-mini"}})
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")
    assert manager.load_settings().openai.model == "gpt-4.1"


def test_an_environment_key_is_read_for_every_keyed_service(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    settings = manager.load_settings()
    assert settings.openai.api_key == "sk-openai"
    assert settings.gemini.api_key == "sk-gemini"
    assert settings.anthropic.api_key == "sk-anthropic"


def test_an_environment_override_merges_rather_than_replaces_its_section(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """`_deep_merge`, not `dict.update` — an `OPENAI_API_KEY` in the
    environment must not wipe the model chosen in Settings."""
    write_config(manager, {"openai": {"model": "gpt-4o-mini"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    settings = manager.load_settings()
    assert settings.openai.api_key == "sk-from-env"
    assert settings.openai.model == "gpt-4o-mini"


def test_numeric_environment_overrides_are_parsed(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_PAGES", "42")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "12.5")
    monkeypatch.setenv("DEBUG_MODE", "yes")
    settings = manager.load_settings()
    assert settings.translation.max_pages == 42
    assert settings.translation.max_file_size_mb == 12.5
    assert settings.debug_mode is True


def test_an_unparseable_numeric_override_is_ignored_not_fatal(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MAX_PAGES", "lots")
    assert manager.load_settings().translation.max_pages == 50


# ---------------------------------------------------------------------------
# recovery by field
# ---------------------------------------------------------------------------


def test_one_bad_field_does_not_cost_the_whole_config(manager: ConfigManager):
    """The case this exists for: a config with an out-of-range temperature
    used to load as bare defaults, silently discarding every other setting the
    user had — API keys included."""
    write_config(
        manager,
        {
            "openai": {"api_key": "${OPENAI_API_KEY}", "temperature": 9.0},
            "translation": {"default_target_lang": "ja"},
        },
    )
    settings = manager.load_settings()

    assert settings.openai.temperature == 0.3  # the offending field reverted
    assert settings.translation.default_target_lang == LanguageCode.JAPANESE


def test_a_key_loaded_from_the_environment_survives_an_invalid_file(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """The recovery is what keeps the key: it is merged in from the
    environment *before* validation, so discarding the whole payload takes it
    with the invalid field."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    write_config(manager, {"openai": {"temperature": 9.0}})
    assert manager.load_settings().openai.api_key == "sk-from-env"


def test_an_unknown_enum_value_drops_only_that_field(manager: ConfigManager):
    write_config(
        manager,
        {
            "translation": {
                "default_target_lang": "klingon",
                "preferred_service": "gemini",
            }
        },
    )
    settings = manager.load_settings()
    assert settings.translation.default_target_lang == LanguageCode.VIETNAMESE
    assert settings.translation.preferred_service == TranslationService.GEMINI


def test_several_bad_fields_across_sections_are_all_dropped(manager: ConfigManager):
    write_config(
        manager,
        {
            "openai": {"temperature": 9.0, "model": "gpt-4o-mini"},
            "anthropic": {"max_tokens": -5, "model": "claude-haiku-4-5"},
        },
    )
    settings = manager.load_settings()
    assert settings.openai.temperature == 0.3
    assert settings.openai.model == "gpt-4o-mini"
    assert settings.anthropic.max_tokens == 4000
    assert settings.anthropic.model == "claude-haiku-4-5"


def test_a_section_of_the_wrong_shape_is_dropped_whole(manager: ConfigManager):
    """A scalar where a table belongs can't be pinpointed to a field, so the
    second pass drops the top-level section — and only that section."""
    write_config(manager, {"openai": "gpt-4o", "translation": {"max_pages": 7}})
    settings = manager.load_settings()
    assert settings.openai.model == "gpt-4.1"
    assert settings.translation.max_pages == 7


def test_defaults_are_the_last_resort_not_the_first(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """If pruning can't produce a valid model, defaults are still returned —
    the sidecar must start."""
    write_config(manager, {"openai": {"temperature": 9.0}})
    settings = manager.load_settings()
    assert isinstance(settings, AppSettings)


def test_pop_path_removes_a_nested_field():
    data = {"openai": {"temperature": 9.0, "model": "gpt-4o"}}
    ConfigManager._pop_path(data, ("openai", "temperature"))
    assert data == {"openai": {"model": "gpt-4o"}}


def test_pop_path_ignores_a_path_that_does_not_resolve():
    """Pydantic `loc` tuples can point into a list, which this can't index —
    a miss must be a no-op rather than a raise inside the recovery path."""
    data = {"openai": {"model": "gpt-4o"}}
    ConfigManager._pop_path(data, ("openai", "missing"))
    ConfigManager._pop_path(data, ("nope", "deeper", "still"))
    ConfigManager._pop_path(data, ())
    assert data == {"openai": {"model": "gpt-4o"}}


# ---------------------------------------------------------------------------
# round trip through the file
# ---------------------------------------------------------------------------


def test_a_saved_config_reloads_with_the_same_values(manager: ConfigManager):
    settings = manager.load_settings()
    settings.openai.model = "gpt-4o-mini"
    settings.translation.default_target_lang = LanguageCode.JAPANESE
    settings.translation.pdf_cache_max_size_mb = 250.0
    assert manager.save_settings(settings) is True

    reloaded = ConfigManager(config_dir=manager.config_dir).load_settings()
    assert reloaded.openai.model == "gpt-4o-mini"
    assert reloaded.translation.default_target_lang == LanguageCode.JAPANESE
    assert reloaded.translation.pdf_cache_max_size_mb == 250.0


def test_a_stored_key_is_decrypted_on_the_way_back_in(manager: ConfigManager):
    settings = manager.load_settings()
    settings.openai.api_key = "sk-round-trip"
    manager.save_settings(settings)

    on_disk = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert on_disk["openai"]["api_key"] != "sk-round-trip"
    assert ConfigManager(config_dir=manager.config_dir).load_settings(
    ).openai.api_key == "sk-round-trip"


def test_the_salt_never_reaches_the_settings_model(manager: ConfigManager):
    """`api_key_salt` is a storage detail of the legacy format. `AppSettings`
    has no such field, so leaving it in the payload would fail validation and
    send the whole config through the recovery path."""
    settings = manager.load_settings()
    settings.openai.api_key = "sk-round-trip"
    manager.save_settings(settings)

    data = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    config_data = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    manager._decrypt_sensitive_data(config_data)
    assert "api_key_salt" not in config_data["openai"]


def test_an_env_placeholder_is_stored_verbatim(manager: ConfigManager):
    """`${OPENAI_API_KEY}` is an indirection, not a secret — encrypting it
    would break the substitution it exists for."""
    settings = manager.load_settings()
    settings.openai.api_key = "${OPENAI_API_KEY}"
    manager.save_settings(settings)

    on_disk = tomlkit.parse(manager.config_file.read_text(encoding="utf-8"))
    assert on_disk["openai"]["api_key"] == "${OPENAI_API_KEY}"


# ---------------------------------------------------------------------------
# update_settings / reset_to_defaults
# ---------------------------------------------------------------------------


def test_update_settings_merges_and_persists(manager: ConfigManager):
    assert manager.update_settings(openai={"model": "gpt-4o-mini"}) is True
    assert manager.settings.openai.model == "gpt-4o-mini"
    assert ConfigManager(
        config_dir=manager.config_dir
    ).load_settings().openai.model == "gpt-4o-mini"


def test_update_settings_leaves_untouched_fields_alone(manager: ConfigManager):
    manager.update_settings(openai={"model": "gpt-4o-mini"})
    manager.update_settings(translation={"max_pages": 7})
    assert manager.settings.openai.model == "gpt-4o-mini"
    assert manager.settings.translation.max_pages == 7


def test_an_invalid_update_is_refused_and_changes_nothing(manager: ConfigManager):
    before = manager.settings.openai.temperature
    assert manager.update_settings(openai={"temperature": 9.0}) is False
    assert manager.settings.openai.temperature == before


def test_a_failed_write_leaves_the_in_memory_settings_untouched(
    manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
):
    """`update_settings` only adopts the new model once the file is on disk —
    otherwise the running process and `config.toml` disagree until restart."""
    monkeypatch.setattr(ConfigManager, "save_settings", lambda self, s: False)
    assert manager.update_settings(openai={"model": "gpt-4o-mini"}) is False
    assert manager.settings.openai.model == "gpt-4.1"


def test_reset_to_defaults_writes_the_file_too(manager: ConfigManager):
    manager.update_settings(openai={"model": "gpt-4o-mini"})
    manager.reset_to_defaults()
    assert ConfigManager(
        config_dir=manager.config_dir
    ).load_settings().openai.model == "gpt-4.1"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_an_exported_config_carries_no_readable_key(
    manager: ConfigManager, tmp_path: Path
):
    settings = manager.load_settings()
    settings.openai.api_key = "sk-secret-value"
    manager._settings = settings

    target = tmp_path / "exported.toml"
    assert manager.export_config(target) is True
    assert "sk-secret-value" not in target.read_text(encoding="utf-8")


def test_export_to_an_unwritable_path_reports_false(
    manager: ConfigManager, tmp_path: Path
):
    assert manager.export_config(tmp_path / "missing_dir" / "out.toml") is False


# ---------------------------------------------------------------------------
# _clean_none_values
# ---------------------------------------------------------------------------


def test_none_values_are_stripped_before_serialization(manager: ConfigManager):
    """tomlkit has no representation for None; leaving one in raises mid-dump
    and loses the save."""
    cleaned = manager._clean_none_values(
        {"a": None, "b": {"c": None, "d": 1}, "e": [1, None, 2], "f": {"g": None}}
    )
    assert cleaned == {"b": {"d": 1}, "e": [1, 2]}


def test_an_unset_optional_field_does_not_break_the_save(manager: ConfigManager):
    settings = manager.load_settings()
    assert settings.openai.max_tokens is None
    assert manager.save_settings(settings) is True
    assert "max_tokens" not in tomlkit.parse(
        manager.config_file.read_text(encoding="utf-8")
    )["openai"]
