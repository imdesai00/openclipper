"""Short-lived signed URLs for <video>/<img>, which cannot send an Authorization header."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings
from app.errors import unauthorized


def sign_asset(asset_id: uuid.UUID, settings: Settings) -> tuple[str, datetime]:
    exp = datetime.now(UTC) + timedelta(seconds=settings.media_url_ttl_seconds)
    token = jwt.encode(
        {"sub": str(asset_id), "typ": "media", "exp": exp},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return token, exp


def verify_asset(token: str, asset_id: uuid.UUID, settings: Settings) -> None:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise unauthorized("Link expired or invalid", "invalid_media_token") from exc
    if data.get("typ") != "media" or data.get("sub") != str(asset_id):
        raise unauthorized("Link expired or invalid", "invalid_media_token")


def media_url(asset_id: uuid.UUID, settings: Settings) -> str:
    token, _ = sign_asset(asset_id, settings)
    return f"/api/v1/media/{asset_id}?t={token}"
