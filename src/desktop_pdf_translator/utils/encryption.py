"""API-key encryption for `config.toml`.

Two formats live here.

**DPAPI (current, Windows).** `CryptProtectData` with `CRYPTPROTECT_UI_FORBIDDEN`
scopes the ciphertext to the *logged-in user's* Windows account: another
account on the same machine cannot decrypt it, and neither can the file
travelling to a different machine.

**Machine-key Fernet (legacy, still readable).** The previous scheme derived a
key from `HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid` — a value every
process on the box can read, so any of them could decrypt `config.toml`.
Existing configs are still decrypted by that path and re-encrypted with DPAPI
the next time settings are saved; nothing has to be re-entered.

It also remains the fallback wherever DPAPI isn't available (non-Windows), so
this module stays importable and functional off Windows.
"""

import base64
import ctypes
import logging
import os
import platform
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


# Marks a DPAPI blob so `decrypt_api_key` knows which scheme produced the
# value. Legacy Fernet values are bare base64 and carry a separate salt.
DPAPI_PREFIX = "dpapi:"

# Secondary entropy mixed into every blob. Not a secret — it ships in the
# binary — it just scopes the ciphertext to this application, so another
# program running as the same user can't decrypt `config.toml` by handing the
# bytes straight to CryptUnprotectData.
_DPAPI_ENTROPY = b"pdfusion.api-keys.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


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
# Legacy machine-key scheme — decrypt-only for existing configs
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

    Returns `(stored_value, salt_b64)`. The salt is empty for DPAPI blobs,
    which carry their own; it is only meaningful for the legacy Fernet path.
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

    if value.startswith(DPAPI_PREFIX):
        return True

    try:
        base64.urlsafe_b64decode(value.encode('utf-8'))
        return True
    except Exception:
        return False
