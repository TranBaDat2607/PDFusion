"""API-key encryption for `config.toml`.

Three formats live here, one per platform plus the one every install used to
have.

**DPAPI (Windows).** `CryptProtectData` with `CRYPTPROTECT_UI_FORBIDDEN` scopes
the ciphertext to the *logged-in user's* Windows account: another account on
the same machine cannot decrypt it, and neither can the file travelling to a
different machine.

**OS keystore + Fernet (Linux, macOS).** The Secret Service (libsecret /
gnome-keyring / KWallet) on Linux and the login Keychain on macOS. Neither
offers DPAPI's "protect these bytes" call, so what the keystore holds is a
randomly generated 32-byte *master key*, and `config.toml` holds Fernet
ciphertext under it. The distinction that matters is where the key lives:
reading `config.toml` is no longer enough to recover a secret from it, which
is the property the scheme below never had.

**Machine-key Fernet (legacy, still readable).** The original scheme derived a
key from `HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid` on Windows and
from `platform.node() + platform.machine()` — hostname and CPU architecture —
everywhere else, with the KDF salt stored beside the ciphertext in the same
file. Both inputs are readable by anything that can read the config, so this is
obfuscation, not encryption. Existing configs are still decrypted by that path
and re-encrypted with the current scheme the next time settings are saved;
nothing has to be re-entered. It also remains the last-resort fallback where
neither DPAPI nor a keystore is reachable — a headless Linux box with no
D-Bus session, most obviously CI — so this module stays importable and
functional anywhere.

`keyring` is imported lazily, inside the two functions that touch it. It is on
the config-load path, and the config-load path is the sidecar's boot path (see
"Import cost is a startup budget" in CLAUDE.md).
"""

import base64
import ctypes
import logging
import os
import platform
import sys
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


# Marks a blob so `decrypt_api_key` knows which scheme produced the value.
# Legacy Fernet values are bare base64 and carry a separate salt.
DPAPI_PREFIX = "dpapi:"
KEYSTORE_PREFIX = "keystore:"

#: Stored values that say for themselves which scheme wrote them, and need no
#: `api_key_salt` beside them. A legacy value carries neither marker, which is
#: why `ConfigManager._decrypt_sensitive_data` has to fall back to "is there a
#: salt?" to tell one from a key the user typed in by hand.
SELF_DESCRIBING_PREFIXES = (DPAPI_PREFIX, KEYSTORE_PREFIX)

# Secondary entropy mixed into every DPAPI blob. Not a secret — it ships in the
# binary — it just scopes the ciphertext to this application, so another
# program running as the same user can't decrypt `config.toml` by handing the
# bytes straight to CryptUnprotectData.
_DPAPI_ENTROPY = b"pdfusion.api-keys.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01

# How the master key is filed in the OS keystore. The service name is what the
# user sees in Seahorse / Keychain Access, so it names the app rather than the
# module.
_KEYSTORE_SERVICE = "PDFusion"
_KEYSTORE_USERNAME = "config-encryption-key"

# Backends that would defeat the point. `keyring.backends.fail` is the sentinel
# keyring returns when it found nothing usable; `keyrings.alt` is a separate
# distribution whose backends keep secrets in plaintext or lightly obfuscated
# files, which is exactly the property the legacy scheme is being retired for.
# Landing on one of those must fall through to the legacy path *with its
# warning*, not silently look like success.
_UNUSABLE_BACKEND_PREFIXES = ("keyring.backends.fail", "keyrings.alt")


class _DataBlob(ctypes.Structure):
    # `ctypes.c_ulong` rather than `wintypes.DWORD`: importing `ctypes.wintypes`
    # raises on non-Windows, and this module must stay importable there.
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    @classmethod
    def of(cls, data: bytes) -> "_DataBlob":
        buf = ctypes.create_string_buffer(data, len(data))
        return cls(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def value(self) -> bytes:
        return ctypes.string_at(self.pbData, self.cbData)


def _dpapi_available() -> bool:
    return platform.system() == "Windows"


def _dpapi_call(func_name: str, data: bytes) -> Optional[bytes]:
    """Run CryptProtectData / CryptUnprotectData over `data`.

    Returns `None` on any failure so callers can fall back rather than crash —
    a machine with a broken crypt32 should still start the app.
    """
    try:
        crypt32 = ctypes.windll.crypt32
        func = getattr(crypt32, func_name)
        blob_in = _DataBlob.of(data)
        entropy = _DataBlob.of(_DPAPI_ENTROPY)
        blob_out = _DataBlob()
        ok = func(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            return blob_out.value()
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:  # noqa: BLE001 — any failure means "use the fallback"
        logger.debug("%s failed", func_name, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# OS keystore — Secret Service on Linux, Keychain on macOS
# ---------------------------------------------------------------------------


def _keystore_supported() -> bool:
    """Whether this platform is one where a keystore is the *intended* store.

    Windows is excluded on purpose even though `keyring` has a backend for it:
    DPAPI is strictly better there (no second secret to lose, no prompt) and is
    tried first anyway, so reaching the keystore on Windows would only mean
    DPAPI had failed — and a second scheme to migrate later.
    """
    return sys.platform in ("linux", "darwin") or sys.platform.startswith(
        ("linux", "freebsd", "openbsd", "netbsd")
    )


def _usable_keyring():
    """The `keyring` module, or `None` when it can't protect anything here.

    Every failure mode collapses to `None`: not installed, no backend, or a
    backend that stores secrets in a file the way the legacy scheme did.
    """
    if not _keystore_supported():
        return None
    try:
        import keyring
    except Exception:  # noqa: BLE001 — not installed, or broken install
        logger.debug("keyring is not importable", exc_info=True)
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        logger.debug("keyring.get_keyring() failed", exc_info=True)
        return None
    name = type(backend).__module__ or ""
    if name.startswith(_UNUSABLE_BACKEND_PREFIXES):
        logger.debug("Ignoring keyring backend %s", name)
        return None
    return keyring


# The master key is read once per process. A Secret Service lookup is a D-Bus
# round trip and `load_settings` decrypts every configured service's key, so
# without this a start with three providers configured pays for three.
_master_key_cache: Optional[bytes] = None
_master_key_looked_up = False


def _reset_master_key_cache() -> None:
    """Forget the cached master key. For tests, which swap the backend under
    this module between cases."""
    global _master_key_cache, _master_key_looked_up
    _master_key_cache = None
    _master_key_looked_up = False


def _keystore_master_key(create: bool) -> Optional[bytes]:
    """The Fernet key held in the OS keystore.

    `create=True` (the encrypt path) generates and stores one when there isn't
    one yet. `create=False` (the decrypt path) never does: a missing key there
    means the stored ciphertext is unrecoverable, and minting a fresh key would
    turn that into a silent, permanent one.
    """
    global _master_key_cache, _master_key_looked_up

    if _master_key_looked_up and _master_key_cache is not None:
        return _master_key_cache

    keyring = _usable_keyring()
    if keyring is None:
        return None

    try:
        existing = keyring.get_password(_KEYSTORE_SERVICE, _KEYSTORE_USERNAME)
    except Exception:  # noqa: BLE001 — locked keyring, no session bus, …
        logger.debug("Could not read the master key from the keystore", exc_info=True)
        return None

    _master_key_looked_up = True
    if existing:
        _master_key_cache = existing.encode("utf-8")
        return _master_key_cache
    if not create:
        return None

    generated = Fernet.generate_key()
    try:
        keyring.set_password(
            _KEYSTORE_SERVICE, _KEYSTORE_USERNAME, generated.decode("utf-8")
        )
    except Exception:  # noqa: BLE001
        logger.debug("Could not store the master key in the keystore", exc_info=True)
        return None
    logger.info("Created a new PDFusion master key in the system keystore")
    _master_key_cache = generated
    return generated


# ---------------------------------------------------------------------------
# Legacy machine-key scheme — decrypt-only for existing configs, and the
# last-resort fallback where no keystore is reachable
# ---------------------------------------------------------------------------


def _get_machine_id() -> str:
    system = platform.system()

    if system == "Windows":
        try:
            import winreg
            registry = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            key = winreg.OpenKey(registry, r"SOFTWARE\Microsoft\Cryptography")
            machine_guid = winreg.QueryValueEx(key, "MachineGuid")[0]
            winreg.CloseKey(key)
            return machine_guid
        except Exception:
            pass

    return platform.node() + platform.machine()


def _derive_key_from_machine(salt: bytes) -> bytes:
    machine_id = _get_machine_id()

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"pdfusion-machine-binding",
    )
    derived = hkdf.derive(machine_id.encode())
    return base64.urlsafe_b64encode(derived)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt_api_key(api_key: str) -> tuple[str, str]:
    """Encrypt a key for storage.

    Returns `(stored_value, salt_b64)`. The salt is empty for DPAPI and
    keystore values, which need none; it is only meaningful for the legacy
    Fernet path.
    """
    if not api_key or api_key.startswith("${"):
        return api_key, ""

    if _dpapi_available():
        blob = _dpapi_call("CryptProtectData", api_key.encode("utf-8"))
        if blob is not None:
            return DPAPI_PREFIX + base64.urlsafe_b64encode(blob).decode("utf-8"), ""
        logger.warning(
            "DPAPI unavailable; falling back to machine-derived key encryption"
        )
    elif _keystore_supported():
        master = _keystore_master_key(create=True)
        if master is not None:
            token = Fernet(master).encrypt(api_key.encode("utf-8"))
            return (
                KEYSTORE_PREFIX + base64.urlsafe_b64encode(token).decode("utf-8"),
                "",
            )
        logger.warning(
            "No system keystore is available (Secret Service on Linux, Keychain "
            "on macOS), so API keys in config.toml are only obfuscated, not "
            "encrypted: the key that protects them is derived from this "
            "machine's hostname. Install and unlock gnome-keyring / KWallet, or "
            "keep config.toml out of reach of other users."
        )

    salt = os.urandom(16)
    key = _derive_key_from_machine(salt)
    fernet = Fernet(key)

    encrypted = fernet.encrypt(api_key.encode())
    encrypted_b64 = base64.urlsafe_b64encode(encrypted).decode('utf-8')
    salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')

    return encrypted_b64, salt_b64


def decrypt_api_key(encrypted_key: str, salt_b64: str = "") -> Optional[str]:
    """Decrypt a stored key, in whichever format it was written."""
    if not encrypted_key or encrypted_key.startswith("${"):
        return None

    if encrypted_key.startswith(DPAPI_PREFIX):
        try:
            blob = base64.urlsafe_b64decode(
                encrypted_key[len(DPAPI_PREFIX):].encode("utf-8")
            )
        except Exception:
            return None
        plaintext = _dpapi_call("CryptUnprotectData", blob)
        return plaintext.decode("utf-8") if plaintext is not None else None

    if encrypted_key.startswith(KEYSTORE_PREFIX):
        master = _keystore_master_key(create=False)
        if master is None:
            # The keystore is locked, gone, or this is a different machine.
            # Blank, so the user can re-enter — never an exception, which would
            # take config loading down with it.
            return None
        try:
            token = base64.urlsafe_b64decode(
                encrypted_key[len(KEYSTORE_PREFIX):].encode("utf-8")
            )
            return Fernet(master).decrypt(token).decode("utf-8")
        except Exception:
            return None

    if not salt_b64:
        # A legacy value without its salt can't be recovered. Returning None
        # (rather than raising) leaves the key blank so the user can re-enter it.
        return None

    try:
        salt = base64.urlsafe_b64decode(salt_b64.encode('utf-8'))
        key = _derive_key_from_machine(salt)
        fernet = Fernet(key)

        encrypted = base64.urlsafe_b64decode(encrypted_key.encode('utf-8'))
        decrypted = fernet.decrypt(encrypted)

        return decrypted.decode('utf-8')
    except Exception:
        return None


def is_encrypted(value: str) -> bool:
    """Whether a stored value looks like ciphertext rather than a raw key."""
    if not value or value.startswith("${"):
        return False

    if value.startswith((DPAPI_PREFIX, KEYSTORE_PREFIX)):
        return True

    try:
        base64.urlsafe_b64decode(value.encode('utf-8'))
        return True
    except Exception:
        return False
