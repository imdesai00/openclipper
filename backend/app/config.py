"""Application settings. The app refuses to start when required secrets are missing."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    env: Literal["development", "production", "test"] = Field("production", alias="APP_ENV")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")

    # Master key for envelope encryption of credentials (used from Milestone 3 on).
    # 32 random bytes, base64 encoded (see .env.example for the generator command).
    encryption_key: SecretStr = Field(alias="APP_ENCRYPTION_KEY")
    jwt_secret: SecretStr = Field(alias="APP_JWT_SECRET")

    access_token_ttl_seconds: int = Field(900, alias="ACCESS_TOKEN_TTL_SECONDS", ge=60)
    refresh_token_ttl_seconds: int = Field(60 * 60 * 24 * 14, alias="REFRESH_TOKEN_TTL_SECONDS")
    invite_ttl_seconds: int = Field(60 * 60 * 24 * 7, alias="INVITE_TTL_SECONDS")
    cookie_secure: bool = Field(True, alias="COOKIE_SECURE")

    cors_origins: list[str] = Field(default_factory=list, alias="CORS_ORIGINS")
    enable_api_docs: bool = Field(False, alias="ENABLE_API_DOCS")

    login_rate_limit_per_ip: int = Field(20, alias="LOGIN_RATE_LIMIT_PER_IP")
    login_rate_limit_per_email: int = Field(8, alias="LOGIN_RATE_LIMIT_PER_EMAIL")
    login_rate_window_seconds: int = Field(300, alias="LOGIN_RATE_WINDOW_SECONDS")
    api_rate_limit_per_minute: int = Field(600, alias="API_RATE_LIMIT_PER_MINUTE")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- Storage ---
    storage_backend: Literal["local", "s3"] = Field("local", alias="STORAGE_BACKEND")
    storage_local_root: str = Field("/data/storage", alias="STORAGE_LOCAL_ROOT")
    upload_staging_dir: str = Field("/data/staging", alias="UPLOAD_STAGING_DIR")
    scratch_dir: str = Field("/data/scratch", alias="SCRATCH_DIR")
    s3_endpoint_url: str | None = Field(None, alias="S3_ENDPOINT_URL")
    s3_bucket: str | None = Field(None, alias="S3_BUCKET")
    s3_region: str = Field("us-east-1", alias="S3_REGION")
    s3_access_key: SecretStr | None = Field(None, alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr | None = Field(None, alias="S3_SECRET_KEY")

    # --- Uploads / media safety ---
    max_upload_bytes: int = Field(20 * 1024**3, alias="MAX_UPLOAD_BYTES", ge=1)
    upload_max_chunk_bytes: int = Field(512 * 1024**2, alias="UPLOAD_MAX_CHUNK_BYTES", ge=1)
    upload_ttl_seconds: int = Field(60 * 60 * 24, alias="UPLOAD_TTL_SECONDS", ge=60)
    media_url_ttl_seconds: int = Field(600, alias="MEDIA_URL_TTL_SECONDS", ge=30)
    ffmpeg_timeout_seconds: int = Field(6 * 3600, alias="FFMPEG_TIMEOUT_SECONDS", ge=10)
    ffmpeg_threads: int = Field(0, alias="FFMPEG_THREADS", ge=0)  # 0 = ffmpeg decides
    proxy_height: int = Field(720, alias="PROXY_HEIGHT", ge=144, le=2160)
    proxy_crf: int = Field(23, alias="PROXY_CRF", ge=0, le=51)
    proxy_preset: str = Field("veryfast", alias="PROXY_PRESET")

    # --- Link ingestion (optional add-on, see DECISIONS D-010) ---
    ytdlp_max_height: int = Field(1080, alias="YTDLP_MAX_HEIGHT", ge=144)
    ytdlp_timeout_seconds: int = Field(4 * 3600, alias="YTDLP_TIMEOUT_SECONDS", ge=30)
    direct_url_max_bytes: int = Field(5 * 1024**3, alias="DIRECT_URL_MAX_BYTES", ge=1)
    direct_url_timeout_seconds: int = Field(3600, alias="DIRECT_URL_TIMEOUT_SECONDS", ge=5)
    # Home-lab option: allow fetching from private-network hosts (e.g. a NAS). Off by default (SSRF).
    direct_url_allow_private: bool = Field(False, alias="DIRECT_URL_ALLOW_PRIVATE")

    @field_validator("encryption_key")
    @classmethod
    def _check_encryption_key(cls, v: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(v.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("APP_ENCRYPTION_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("APP_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("APP_JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("database_url")
    @classmethod
    def _check_db_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// scheme")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
