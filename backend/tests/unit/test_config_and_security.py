import base64
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.errors import AppError
from app.ids import uuid7
from app.security.passwords import DUMMY_HASH, hash_password, verify_password
from app.security.tokens import (
    create_access_token,
    decode_access_token,
    hash_token,
    new_opaque_token,
)

pytestmark = pytest.mark.unit
GOOD = base64.b64encode(b"k" * 32).decode()


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
        "REDIS_URL": "redis://h",
        "APP_ENCRYPTION_KEY": GOOD,
        "APP_JWT_SECRET": "x" * 40,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_settings_ok() -> None:
    assert Settings.model_fields["env"].default == "production"
    assert _settings().cookie_secure is True


@pytest.mark.parametrize(
    "override",
    [
        {"APP_ENCRYPTION_KEY": "not-base64!!"},
        {"APP_ENCRYPTION_KEY": base64.b64encode(b"short").decode()},
        {"APP_JWT_SECRET": "short"},
        {"DATABASE_URL": "postgresql://sync/driver"},
    ],
)
def test_settings_rejects_bad_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**override)


def test_settings_refuse_missing_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("APP_ENCRYPTION_KEY", "APP_JWT_SECRET", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings()  # type: ignore[call-arg]
    assert "APP_ENCRYPTION_KEY" in str(exc.value)


def test_password_roundtrip() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong")
    assert not verify_password(DUMMY_HASH, "anything")
    assert not verify_password("garbage", "x")


def test_access_token_roundtrip_and_failures() -> None:
    s = _settings()
    uid = uuid.uuid4()
    claims = decode_access_token(create_access_token(uid, "admin", s), s)
    assert claims.user_id == uid and claims.role == "admin"

    expired = jwt.encode(
        {"sub": str(uid), "typ": "access", "exp": datetime.now(UTC) - timedelta(seconds=5)},
        s.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(AppError) as e:
        decode_access_token(expired, s)
    assert e.value.code == "token_expired"

    other = _settings(APP_JWT_SECRET="y" * 40)
    with pytest.raises(AppError):
        decode_access_token(create_access_token(uid, "admin", other), s)

    wrong_type = jwt.encode(
        {"sub": str(uid), "typ": "refresh", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        s.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(AppError):
        decode_access_token(wrong_type, s)

    none_alg = jwt.encode({"sub": str(uid), "typ": "access"}, key="", algorithm="none")
    with pytest.raises(AppError):
        decode_access_token(none_alg, s)


def test_opaque_tokens_unique_and_hashed() -> None:
    raw1, h1 = new_opaque_token()
    raw2, _ = new_opaque_token()
    assert raw1 != raw2 and h1 == hash_token(raw1) and raw1 not in h1 and len(h1) == 64


def test_uuid7_version_and_ordering() -> None:
    a = uuid7()
    time.sleep(0.003)
    b = uuid7()
    assert a.version == 7 and b.version == 7 and a < b


def test_secret_not_in_repr() -> None:
    assert GOOD not in repr(_settings())
    assert isinstance(_settings().jwt_secret, SecretStr)
