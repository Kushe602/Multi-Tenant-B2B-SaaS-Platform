# TenantForge -- gateway (public edge: web UI + REST API).
#
# IMPORTANT: build from the REPOSITORY ROOT so the shared/ package is in context:
#   docker build -f gateway/Dockerfile -t tenantforge-gateway .
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Shared library + this service's package (includes Jinja2 templates).
COPY shared ./shared
COPY gateway ./gateway

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# $PORT-aware so the same image runs on compose, Render, Fly, Cloud Run, etc.
CMD ["sh", "-c", "exec uvicorn gateway.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
