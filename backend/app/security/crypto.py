"""AES-GCM envelope encryption for stored secrets (SPEC §6.I).

Each secret gets its own random data key (DEK). The DEK encrypts the secret; the master key (KEK, from
APP_ENCRYPTION_KEY) encrypts the DEK. Rotating the master key only re-wraps the small DEKs, so no secret is
ever decrypted to rotate, and the ciphertexts stay untouched. Both layers bind `aad` (what the secret belongs
to), so a ciphertext copied onto another row will not decrypt.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings

NONCE = 12


class CryptoError(Exception):
    """Wrong key, tampered data, or a ciphertext bound to a different owner. Never includes secret material."""


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes  # nonce || AESGCM(DEK)(plaintext)
    wrapped_dek: bytes  # nonce || AESGCM(KEK)(DEK)
    key_version: str  # fingerprint of the master key that wrapped the DEK


def fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"openclipper-kek|" + key).hexdigest()[:12]


def _decode(b64: str) -> bytes:
    raw = base64.b64decode(b64, validate=True)
    if len(raw) != 32:
        raise CryptoError("encryption key must be 32 bytes")
    return raw


class Vault:
    def __init__(self, current: bytes, previous: list[bytes] | None = None) -> None:
        self._current = current
        self._by_version = {fingerprint(k): k for k in [current, *(previous or [])]}
        self.version = fingerprint(current)

    @classmethod
    def from_settings(cls, s: Settings) -> Vault:
        prev: list[bytes] = []
        if s.encryption_key_previous:
            prev = [_decode(p.strip()) for p in s.encryption_key_previous.get_secret_value().split(",") if p.strip()]
        return cls(_decode(s.encryption_key.get_secret_value()), prev)

    def encrypt(self, plaintext: str, aad: bytes) -> Sealed:
        dek = AESGCM.generate_key(bit_length=256)
        n1, n2 = os.urandom(NONCE), os.urandom(NONCE)
        ct = n1 + AESGCM(dek).encrypt(n1, plaintext.encode(), aad)
        wrapped = n2 + AESGCM(self._current).encrypt(n2, dek, b"dek|" + aad)
        return Sealed(ct, wrapped, self.version)

    def _unwrap(self, sealed: Sealed, aad: bytes) -> bytes:
        kek = self._by_version.get(sealed.key_version)
        candidates = [kek] if kek else list(self._by_version.values())  # unknown version: try every known key
        for k in candidates:
            try:
                return AESGCM(k).decrypt(sealed.wrapped_dek[:NONCE], sealed.wrapped_dek[NONCE:], b"dek|" + aad)
            except InvalidTag:
                continue
        raise CryptoError("cannot unwrap the data key: wrong master key, or the record was altered or moved")

    def decrypt(self, sealed: Sealed, aad: bytes) -> str:
        dek = self._unwrap(sealed, aad)
        try:
            return AESGCM(dek).decrypt(sealed.ciphertext[:NONCE], sealed.ciphertext[NONCE:], aad).decode()
        except InvalidTag as exc:
            raise CryptoError("ciphertext failed authentication") from exc

    def rewrap(self, sealed: Sealed, aad: bytes) -> Sealed:
        """Wrap the same data key with the current master key. The secret itself is never decrypted."""
        dek = self._unwrap(sealed, aad)
        n = os.urandom(NONCE)
        wrapped = n + AESGCM(self._current).encrypt(n, dek, b"dek|" + aad)
        return Sealed(sealed.ciphertext, wrapped, self.version)

    def needs_rotation(self, sealed: Sealed) -> bool:
        return sealed.key_version != self.version


def mask(secret: str) -> str:
    """`sk-or-v1-abcdef...wxyz` -> `sk-…wxyz`. Short secrets reveal nothing but their length class."""
    s = secret.strip()
    if len(s) < 12:
        return "••••"
    return f"{s[:3]}…{s[-4:]}"
