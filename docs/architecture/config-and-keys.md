# Config and API keys

How `config.toml` is shaped, loaded and saved, and how the API keys in it are
protected.

## Where keys are stored

A key saved in the Settings sheet is encrypted by `utils/encryption.py` before
it is written to `config.toml` under the data root (see
[cross-platform.md](cross-platform.md)). Which scheme protects it is
per-platform, and the distinction that matters is *where the key that unlocks
it lives*:

| Platform | Scheme | The unlocking key is held by |
|---|---|---|
| Windows | **DPAPI** — `CryptProtectData`, user-scoped, with app entropy | the OS, scoped to the logged-in account |
| Linux | Fernet under a master key in the **Secret Service** (gnome-keyring, KWallet) | the user's keyring |
| macOS | Fernet under a master key in the **login Keychain** | the Keychain |
| any, as a last resort | Fernet under a key derived from the machine id | *the file itself* |

Three things about that table. **The last row is not encryption** and is only
reached where the platform's store is unavailable — a headless Linux box with
no D-Bus session, most obviously CI. Off Windows the derivation input is
`platform.node() + platform.machine()` (hostname and CPU architecture) and the
KDF salt is stored beside the ciphertext in the same file, so anyone who can
read `config.toml` can reproduce the key. `encrypt_api_key` logs a warning
naming the fix when it lands there; it does not refuse to save, because "the
app won't start" is worse than "the key is obfuscated" for a user who never
had a keyring in the first place.

**The keystore holds one master key, not one entry per provider** — a single
`PDFusion / config-encryption-key` the user can see and revoke in Seahorse or
Keychain Access. And **the decrypt path never mints one**: a missing entry
means the ciphertext is unrecoverable, and generating a replacement would turn
"the keyring is locked" (temporary) into "those keys are gone" (permanent).
The key comes back blank.

**The save path is the other half of that, and without it the decrypt path's
restraint bought nothing.** A key that came back blank is indistinguishable, by
the time `_remove_sensitive_data` sees it, from a key the user cleared — so any
`PUT /config` at all (Chat on, a new target language) wrote `api_key = ""` over
the ciphertext, and since `_write_backup` strips keys, it went from both files
at once. A locked keyring cost the user every provider key they had. So
`ConfigManager` remembers what it could not read (`_unreadable_keys`) and
writes it back **verbatim**, and `PUT /providers/{id}` (`PUT /config` until
#88) calls `forget_unreadable_key` whenever the body carries that provider's
`api_key` — which, with `DELETE /providers/{id}/key`, is the only way a key is
set or cleared, so clearing one still clears it. Two consequences.
`reset_to_defaults` drops the records first, being an explicit wipe. And a
preserved key counts as saved for the endpoint rule below (#32): it decrypts
again once the keystore is reachable, and would otherwise go to whatever
endpoint was set meanwhile — `has_unreadable_key` is what `PUT /providers/{id}` asks.

Relatedly, **a failed decrypt is `pop`ped, never assigned**. `config_data`
holds tomlkit tables and those refuse a `None` value; assigning one raised out
of `load_settings`'s whole file branch, which left the *ciphertext* standing as
`api_key` — sent to the provider as a credential, reported by `GET /config` as
a configured key, and every other setting in the file silently back to its
default.

Values written by any earlier scheme still decrypt and are upgraded to the
current platform's on the next save, so nobody re-enters a key. A stored value
carries its own marker (`SELF_DESCRIBING_PREFIXES` — `dpapi:`, `keystore:`); a
legacy one carries none, which is why `ConfigManager._decrypt_sensitive_data`
falls back to "is there an `api_key_salt` beside it?" to tell a legacy
ciphertext from a key someone typed in by hand. `config.toml` is written to a temp file
and `os.replace`d into position — an in-place write that crashed used to truncate
the file, and a truncated config loads as defaults, i.e. silently discards every
setting including the keys. The outgoing generation is kept as `config.toml.bak`
**with the API keys stripped out** (`manager.py:_write_backup`): a backup must
never be more readable than the file it backs up, and holding that as an
unconditional invariant is what avoids having to detect the one migration
(legacy → DPAPI, legacy → keystore) where copying verbatim would have parked a
machine-readable key beside the hardened one. A key is re-enterable; the rest of the file is what is
worth recovering by hand.

## Where config comes from

- **`.env` is searched at the repo root, resolved from `__file__`, not the
  cwd** — on an installed launch the cwd would be the non-writable install
  dir — and in the data root (`config/manager.py:_load_dotenv`). It overrides
  the TOML, except for an API key whose provider has its own endpoint (see
  "LLM endpoints and models" in [providers.md](providers.md)).
- **Python used to hardcode `~/AppData/Local/PDFusion`**, which is a different
  folder wherever Local AppData has been relocated and nobody's convention off
  Windows. `ConfigManager` copies a `config.toml` left at that old root once
  (`adopt_legacy_config`), so settings and keys survive the move.
- **`pdf_cache_max_size_mb` applies without a restart**: it is re-read on
  every eviction pass.

## Providers and model choices in config.toml (#85)

Before #85, each provider had a top-level table holding its key *and* the
model it ran, and `translation.preferred_service` said which one translated.
That shape could not choose a chat model apart from the translation model,
nor a set of models for the pickers, and every new provider needed a new
Pydantic class plus fields on the config API. Now:

```toml
[providers.openai]            # one table per registry id, all the same class
api_key = "…encrypted…"
base_url = "http://localhost:11434/v1"   # absent = the provider's own
enabled_models = ["gpt-4.1", "gpt-5.6-sol"]
temperature = 0.3

[translation]
model = { provider = "anthropic", model = "claude-sonnet-4-6" }

[rag]
answer_model = { provider = "openai", model = "gpt-4.1" }   # absent = the translation model
```

- **Which model a provider runs** when chosen without one named is
  `AppSettings.model_for(id)`: the translation model's name when that provider
  translates, otherwise the first of its `enabled_models`, otherwise its
  default. `enabled_models` is ordered for this reason: choosing a model for a
  provider, by any route, moves it to the front (`remember_model`), and
  switching the translation model remembers the outgoing one too
  (`translate_with`). So switching providers and back keeps each one's model,
  as when each section held its own. `GET /config`'s per-provider `model` is
  `model_for`, and so is the model a `/translate` with an explicit `service`
  runs.
- **`ModelRef` is checked against the registry**: an unknown provider fails,
  and a fixed-model provider (Argos) always names its default.
- **Per-provider rules are field validators, not a model validator.**
  `AppSettings` fills each entry's `provider_id` (never saved) from its key,
  and the validators read the spec from it, so a bad value fails at
  `providers.<id>.<field>` and the load path drops only that field. The
  recovery's second pass drops at most one provider's table, never the map:
  that would be every key at once. A `ModelRef` that fails is dropped whole,
  since half a reference can't be repaired field by field. A provider id no
  registry entry has is left out as the file loads (a downgrade is the likely
  cause), and refused when it comes through the API.
- **Chat's answer model** is `rag.answer_model`, then the translation model,
  then every LLM by `priority` with its `model_for`, one candidate per
  provider; the first whose provider has a key answers. See ["Chat answers"](data-and-chat.md#chat-answers).

**Converting a config from before #85** happens as it loads, beside
`_replace_retired_models` (`ConfigManager._convert_v1`): each top-level
provider table moves under `providers.<id>`, its `model` becomes
`enabled_models = [model]`, and `preferred_service` plus that service's model
become `translation.model`. Only the shape changes — keys move as they were
stored, ciphertext and salt alike, and are decrypted afterwards on the new
shape. The file is not rewritten on load; the next save writes the new shape.
`RETIRED_MODELS` applies after the conversion, to the translation and answer
models and to every `enabled_models` entry. A file holding both shapes for one
provider keeps the new one, except for a key only the old table has. `<PREFIX>_MODEL` in the environment is applied the
way a model chosen in the app is: it goes first in `enabled_models`, and
becomes the translation model's name when its provider translates.

**The key rules, per provider id.** Every rule below held for the old top-level
tables and now holds for `providers.<id>`, and the tests that guard them were
ported one-for-one (`test_config_security.py`, `test_config_manager_load.py`,
`test_config_api.py`), with the conversion's own in `test_config_v2.py`:

- Keys are encrypted on save and decrypted on load
  (`_remove_sensitive_data`, `_decrypt_sensitive_data`), each under
  `providers.<id>`.
- A key that can't be decrypted is remembered by provider id
  (`_unreadable_keys`) and written back verbatim, never blanked. Only setting
  or clearing that provider's key — `PUT /providers/{id}`'s `api_key`,
  `DELETE /providers/{id}/key` (and until #88 `PUT /config`'s block) — forgets
  it, and only once the new settings have validated.
- `_write_backup` strips `api_key` and `api_key_salt` from **both** shapes: the
  first save after a conversion backs up the old file, whose keys sit in
  top-level tables.
- The save is a temp file plus `os.replace`, unchanged.
- Environment keys come from each spec's `env_prefix` and are ignored for a
  provider with a custom `base_url` (`_keep_environment_keys_off_endpoints`).
- An endpoint change without `api_key` in the same body is a **422** whenever a
  key is saved, unreadable ones included (`routes/providers.py:
  endpoint_change_refusal`). Since #88 `PUT /providers/{id}` is the only
  route that changes one; the `PUT /config` tests of this rule were ported to
  `test_provider_config_api.py` one-for-one.
