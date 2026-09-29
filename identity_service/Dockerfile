# TenantForge -- identity-service (tenants, users, auth, RBAC, invites).
#
# IMPORTANT: build from the REPOSITORY ROOT so the shared/ package is in context:
#   docker build -f identity_service/Dockerfile -t tenantforge-identity .
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY shared ./shared
COPY identity_service ./identity_service

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn identity_service.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
