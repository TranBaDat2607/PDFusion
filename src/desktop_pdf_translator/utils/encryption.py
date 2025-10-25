import os
import base64
import hashlib
import platform
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


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


def encrypt_api_key(api_key: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    if not api_key or api_key.startswith("${"):
        return api_key, ""
    
    if salt is None:
        salt = os.urandom(16)
    
    key = _derive_key_from_machine(salt)
    fernet = Fernet(key)
    
    encrypted = fernet.encrypt(api_key.encode())
    encrypted_b64 = base64.urlsafe_b64encode(encrypted).decode('utf-8')
    salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')
    
    return encrypted_b64, salt_b64


def decrypt_api_key(encrypted_key: str, salt_b64: str) -> Optional[str]:
    if not encrypted_key or encrypted_key.startswith("${"):
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
    if not value or value.startswith("${"):
        return False
    
    try:
        base64.urlsafe_b64decode(value.encode('utf-8'))
        return True
    except Exception:
        return False
