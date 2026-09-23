# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install server dependencies
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "psycopg[binary,pool]>=3.2" \
    "pydantic>=2.7" \
    "jinja2>=3.1" \
    "itsdangerous>=2.2" \
    "httpx>=0.27" \
    "python-multipart>=0.0.12" \
    "cryptography>=43.0.0"

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create unprivileged system user
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# Copy application source code and packages
COPY apps/license_server /app/apps/license_server
COPY packages/licensing /app/packages/licensing
COPY packages/identity /app/packages/identity
COPY pyproject.toml /app/pyproject.toml

# Set permissions
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

CMD ["uvicorn", "apps.license_server.main:app", "--host", "0.0.0.0", "--port", "8080"]
