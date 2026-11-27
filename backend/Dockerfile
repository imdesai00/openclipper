# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Optional: behind a TLS-inspecting proxy, pass your CA with `--secret id=extra_ca,src=ca.pem`.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app app \
    && mkdir -p /data/storage /data/staging /data/scratch && chown -R app:app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Optional link-download worker (compose profile "ingest"). Adds yt-dlp and the Deno runtime YouTube now requires.
FROM runtime AS ingest
USER root
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=denoland/deno:bin-2.1.4 /deno /usr/local/bin/deno
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv pip install --python /app/.venv/bin/python "yt-dlp[default]" yt-dlp-ejs \
    && mkdir -p /data/ytdlp-lib && chown app:app /data/ytdlp-lib
ENV INGEST_WORKER=1 YTDLP_LIB_DIR=/data/ytdlp-lib UV_CACHE_DIR=/tmp/uv-cache
USER app
