"""AES-256-GCM encryption with PBKDF2-HMAC-SHA512 key derivation.

Ported from tg-vault/crypto.py — proven, unchanged crypto params.

Parameters (fixed for backward compat with tg-vault):
    - Algorithm: AES-256-GCM (authenticated encryption with 128-bit tag)
    - KDF: PBKDF2-HMAC-SHA512, 600,000 iterations (OWASP 2025)
    - Salt: 32-byte random per-file salt
    - IV: 12-byte deterministic per-chunk — iv = (chunk_index).to_bytes(12, "big")
      Safe because key is unique per file (random salt).
    - Password verification hash: separate PBKDF2 with fixed domain-separated salt
      → allows fail-fast on wrong passwords without attempting decryption.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Constants (fixed for compat with tg-vault)
KEY_LENGTH = 32           # AES-256
IV_LENGTH = 12            # 96-bit (NIST recommended for GCM)
TAG_LENGTH = 16           # 128-bit auth tag (built into AESGCM.encrypt output)
SALT_LENGTH = 32          # per-file random salt
PBKDF2_ITERATIONS = 600_000
VERIFY_SALT_DOMAIN = b"tg-vault-password-verify-v1"  # domain separation


class Encryptor:
    """AES-256-GCM encryptor with PBKDF2 key derivation.

    Usage:
        enc = Encryptor("my_password", salt=None)  # generates random salt
        ciphertext, iv = enc.encrypt_chunk_with_iv(plaintext, iv)
        plaintext = enc.decrypt_chunk(ciphertext, iv)

        # Password verification (fail-fast):
        h = Encryptor.get_password_hash("my_password")
        Encryptor.verify_password_hash("my_password", h)  # True
        Encryptor.verify_password_hash("wrong", h)        # False
    """

    def __init__(self, password: str, salt: bytes | None = None):
        """Initialize with password and optional salt.

        If salt is None, a random 32-byte salt is generated.
        """
        self.salt = salt or os.urandom(SALT_LENGTH)
        self.key = self._derive_key(password, self.salt)
        self._aesgcm = AESGCM(self.key)

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        """Derive a 256-bit key from password + salt via PBKDF2-HMAC-SHA512."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Generate a deterministic verification hash for the password.

        Uses a FIXED domain-separated salt (different from encryption salt)
        so the same password always produces the same hash.
        This allows fail-fast on wrong passwords.

        Returns hex string.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA512(),
            length=32,
            salt=VERIFY_SALT_DOMAIN,
            iterations=PBKDF2_ITERATIONS,
        )
        derived = kdf.derive(password.encode("utf-8"))
        return derived.hex()

    @staticmethod
    def verify_password_hash(password: str, stored_hash: str) -> bool:
        """Verify a password against a stored hash (constant-time)."""
        computed = Encryptor.get_password_hash(password)
        return hmac.compare_digest(computed, stored_hash)

    def encrypt_chunk_with_iv(self, plaintext: bytes, iv: bytes) -> bytes:
        """Encrypt plaintext with a caller-supplied IV (deterministic).

        Returns ciphertext + auth tag (tag is appended by AESGCM).

        For chunked uploads, use iv = (chunk_index).to_bytes(12, "big")
        — deterministic per chunk, safe because key is unique per file.
        """
        return self._aesgcm.encrypt(iv, plaintext, associated_data=None)

    def decrypt_chunk(self, ciphertext: bytes, iv: bytes) -> bytes:
        """Decrypt ciphertext with IV. Raises InvalidTag on tamper/wrong key."""
        return self._aesgcm.decrypt(iv, ciphertext, associated_data=None)

    # ── Serialization helpers ──

    @staticmethod
    def salt_to_str(salt: bytes) -> str:
        return base64.b64encode(salt).decode("ascii")

    @staticmethod
    def salt_from_str(s: str) -> bytes:
        return base64.b64decode(s)

    @staticmethod
    def iv_to_str(iv: bytes) -> str:
        return base64.b64encode(iv).decode("ascii")

    @staticmethod
    def iv_from_str(s: str) -> bytes:
        return base64.b64decode(s)
