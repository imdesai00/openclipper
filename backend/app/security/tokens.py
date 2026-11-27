from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings
from app.errors import unauthorized

ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str


def create_access_token(user_id: uuid.UUID, role: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "typ"]},
        )
        if data.get("typ") != "access":
            raise unauthorized("Invalid token")
        return AccessClaims(user_id=uuid.UUID(data["sub"]), role=str(data.get("role", "")))
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Access token expired", "token_expired") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        raise unauthorized("Invalid token", "invalid_token") from exc


def new_opaque_token() -> tuple[str, str]:
    """Return (raw, sha256-hex). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
