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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

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


# ─── Vault sharing: X25519 sealed-box (per-recipient encryption) ──────────────
#
# Lets a vault item be re-encrypted so that only a *specific other user* can
# read it — without ever needing that user's master password at share time.
# Equivalent in spirit to nacl.public.SealedBox, built from primitives already
# available via `cryptography` (no new dependency):
#   seal:   generate an ephemeral X25519 keypair, ECDH with the recipient's
#           public key, HKDF the shared secret into a symmetric key, Fernet-
#           encrypt the plaintext with it, and ship the ephemeral public key
#           alongside the ciphertext (needed to redo the ECDH on the way out).
#   unseal: recipient repeats the ECDH using the ephemeral public key + their
#           own private key, derives the same symmetric key, decrypts.
# The recipient's private key is itself wrapped with their own KEK (via
# wrap_dek/unwrap_dek below — those functions operate on generic 32-byte
# secrets, not just DEKs), so it's protected exactly like their DEK.

_HKDF_INFO = b'rbac-vault-share-v1'


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh X25519 keypair. Returns (private_key_bytes, public_key_bytes)."""
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    return (
        private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()),
        public_key.public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


def _derive_shared_key(shared_secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=_KEY_LEN, salt=None, info=_HKDF_INFO,
    ).derive(shared_secret)


def seal_for_recipient(plaintext: str, recipient_public_key: bytes) -> str:
    """Encrypt `plaintext` so only the holder of `recipient_public_key`'s matching
    private key can read it. Returns a single opaque string safe to store as-is."""
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public_bytes = ephemeral_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    recipient_public = X25519PublicKey.from_public_bytes(recipient_public_key)
    shared_secret = ephemeral_private.exchange(recipient_public)
    token = _fernet(_derive_shared_key(shared_secret)).encrypt(plaintext.encode())
    return f'{base64.urlsafe_b64encode(ephemeral_public_bytes).decode()}.{token.decode()}'


def unseal_with_private_key(sealed: str, recipient_private_key: bytes) -> str:
    """Decrypt a payload produced by `seal_for_recipient`, using the recipient's
    own (unwrapped) private key. Raises VaultCryptoError if the key is wrong or
    the payload was tampered with."""
    ephemeral_public_b64, token = sealed.split('.', 1)
    ephemeral_public = X25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(ephemeral_public_b64.encode())
    )
    private_key = X25519PrivateKey.from_private_bytes(recipient_private_key)
    shared_secret = private_key.exchange(ephemeral_public)
    return _fernet(_derive_shared_key(shared_secret)).decrypt(token.encode()).decode()
