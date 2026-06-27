"""
Envelope encryption primitives for the Vault.

Model ("Nivel B"):
- A random per-user **DEK** (data encryption key) encrypts the vault items.
- The DEK is stored *wrapped* with a **KEK** = Argon2id(master_password, salt).
- A recovery code wraps the same DEK with a second KEK, enabling a master-password
  reset without data loss.

So the global server ENCRYPTION_KEY alone cannot decrypt the vault — the user's
master password (or recovery code) is required to unwrap the DEK.

KEK derivation is **deterministic** (raw Argon2id over password+stored salt), unlike
Django's password hashers which embed a random salt. The master/recovery *verifiers*
do use Django's hashers (random salt is fine there — they only confirm the secret).
"""
import base64
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet, InvalidToken

# Argon2id parameters for KEK derivation (OWASP-ish minimums; fast enough for a
# per-request unlock, deterministic given the same password+salt).
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536  # 64 MiB
_ARGON2_PARALLELISM = 4
_KEY_LEN = 32  # 256-bit key → Fernet key after urlsafe base64

# Re-export so callers can catch a single "bad key/ciphertext" error.
VaultCryptoError = InvalidToken


def generate_salt() -> str:
    """Return a fresh random salt as a base64 string (stored on VaultKey)."""
    return base64.b64encode(os.urandom(16)).decode()


def generate_dek() -> bytes:
    """Return a fresh random 256-bit data encryption key."""
    return os.urandom(_KEY_LEN)


def derive_kek(password: str, salt_b64: str) -> bytes:
    """Deterministically derive a 256-bit KEK from a password + stored salt."""
    return hash_secret_raw(
        secret=password.encode(),
        salt=base64.b64decode(salt_b64),
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_KEY_LEN,
        type=Type.ID,
    )


def _fernet(key32: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(key32))


def wrap_dek(dek: bytes, kek: bytes) -> str:
    """Encrypt (wrap) the DEK with a KEK. Returns base64 token string."""
    return _fernet(kek).encrypt(dek).decode()


def unwrap_dek(wrapped: str, kek: bytes) -> bytes:
    """Decrypt (unwrap) the DEK with a KEK. Raises VaultCryptoError if KEK is wrong."""
    return _fernet(kek).decrypt(wrapped.encode())


def encrypt_blob(plaintext: str, dek: bytes) -> str:
    """Encrypt an item's JSON blob with the DEK."""
    return _fernet(dek).encrypt(plaintext.encode()).decode()


def decrypt_blob(ciphertext: str, dek: bytes) -> str:
    """Decrypt an item's JSON blob with the DEK."""
    return _fernet(dek).decrypt(ciphertext.encode()).decode()


def dek_to_str(dek: bytes) -> str:
    """Serialize a DEK to a base64 string (for caching)."""
    return base64.b64encode(dek).decode()


def dek_from_str(value: str) -> bytes:
    """Deserialize a DEK from a base64 string."""
    return base64.b64decode(value)
