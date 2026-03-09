"""
AES-256 encryption utilities via Fernet (symmetric encryption).

ENCRYPTION_KEY must be a URL-safe base64-encoded 32-byte key.
Generate one with: from cryptography.fernet import Fernet; Fernet.generate_key()
"""
import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    # Prefer Django settings so @override_settings works in tests
    try:
        from django.conf import settings
        key_str = getattr(settings, 'ENCRYPTION_KEY', None)
    except Exception:
        key_str = None
    if not key_str:
        key_str = os.environ['ENCRYPTION_KEY']
    return Fernet(key_str.encode())


def encrypt_value(plain_text: str) -> str:
    """Encrypt a plaintext string. Returns URL-safe base64 ciphertext."""
    return _get_fernet().encrypt(plain_text.encode()).decode()


def decrypt_value(cipher_text: str) -> str:
    """Decrypt a Fernet-encrypted ciphertext string. Returns plaintext."""
    return _get_fernet().decrypt(cipher_text.encode()).decode()
