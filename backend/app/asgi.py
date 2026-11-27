"""Uvicorn entrypoint: `uvicorn app.asgi:app`. Fails fast if required secrets are missing."""

from app.main import create_app

app = create_app()
