import base64
import os

import pytest

from app.config import Settings
from app.security.crypto import CryptoError, Sealed, Vault, fingerprint, mask

pytestmark = pytest.mark.unit
K1, K2, K3 = os.urandom(32), os.urandom(32), os.urandom(32)
AAD = b"provider:1234"


def test_roundtrip_and_ciphertext_hides_the_secret() -> None:
    v = Vault(K1)
    s = v.encrypt("sk-or-v1-supersecretvalue", AAD)
    assert b"supersecret" not in s.ciphertext and b"supersecret" not in s.wrapped_dek
    assert v.decrypt(s, AAD) == "sk-or-v1-supersecretvalue"


def test_every_encryption_is_different() -> None:
    v = Vault(K1)
    a, b = v.encrypt("same", AAD), v.encrypt("same", AAD)
    assert a.ciphertext != b.ciphertext and a.wrapped_dek != b.wrapped_dek


def test_a_ciphertext_cannot_be_moved_to_another_record() -> None:
    v = Vault(K1)
    s = v.encrypt("secret", b"provider:A")
    with pytest.raises(CryptoError):
        v.decrypt(s, b"provider:B")


@pytest.mark.parametrize("field", ["ciphertext", "wrapped_dek"])
def test_tampering_is_detected(field: str) -> None:
    v = Vault(K1)
    s = v.encrypt("secret", AAD)
    raw = bytearray(getattr(s, field))
    raw[-1] ^= 1
    bad = Sealed(**{**s.__dict__, field: bytes(raw)})
    with pytest.raises(CryptoError):
        v.decrypt(bad, AAD)


def test_wrong_master_key_cannot_decrypt() -> None:
    s = Vault(K1).encrypt("secret", AAD)
    with pytest.raises(CryptoError):
        Vault(K2).decrypt(s, AAD)


def test_rotation_rewraps_without_touching_the_ciphertext() -> None:
    old = Vault(K1)
    s = old.encrypt("secret", AAD)
    new = Vault(K2, previous=[K1])
    assert new.needs_rotation(s)
    r = new.rewrap(s, AAD)
    assert r.ciphertext == s.ciphertext  # the secret was never decrypted or re-encrypted
    assert r.key_version == fingerprint(K2) and not new.needs_rotation(r)
    assert Vault(K2).decrypt(r, AAD) == "secret"  # the old key is no longer needed
    with pytest.raises(CryptoError):
        Vault(K1).decrypt(r, AAD)  # and the old key can no longer open it


def test_unknown_key_version_still_decrypts_when_a_previous_key_matches() -> None:
    s = Vault(K1).encrypt("secret", AAD)
    forged = Sealed(s.ciphertext, s.wrapped_dek, "deadbeefcafe")
    assert Vault(K2, previous=[K1]).decrypt(forged, AAD) == "secret"


def test_from_settings_reads_current_and_previous_keys() -> None:
    def st(**kw: object) -> Settings:
        base = {"DATABASE_URL": "postgresql+asyncpg://u:p@h/d", "REDIS_URL": "redis://h", "APP_JWT_SECRET": "j" * 40,
                "APP_ENCRYPTION_KEY": base64.b64encode(K2).decode()}  # fmt: skip
        return Settings(**{**base, **kw})  # type: ignore[arg-type]

    s = Vault(K1).encrypt("secret", AAD)
    v = Vault.from_settings(
        st(APP_ENCRYPTION_KEY_PREVIOUS=base64.b64encode(K1).decode() + "," + base64.b64encode(K3).decode())
    )
    assert v.decrypt(s, AAD) == "secret"
    with pytest.raises(ValueError):
        st(APP_ENCRYPTION_KEY_PREVIOUS="not-base64!!")


def test_mask_never_reveals_the_middle() -> None:
    m = mask("sk-or-v1-0123456789abcdef0123456789abcdefWXYZ")
    assert m == "sk-…WXYZ" and "0123456789" not in m
    assert mask("short") == "••••"
