# ==============================================================================
# Production Dockerfile for Cleaning One FastAPI Backend
# ==============================================================================

# --- Base Image ---
FROM python:3.13-slim-bookworm AS base

# Install uv package manager from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Environment Variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=8080 \
    SERVICE_NAME=all \
    PATH="/app/.venv/bin:$PATH"

# Install essential system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# --- Dependency Installation (Cached Layer) ---
# Copy only package metadata first to optimize Docker build caching
COPY pyproject.toml uv.lock .python-version README.md ./

# Install project dependencies into virtual environment
RUN uv sync --frozen --no-install-project --no-dev

# --- Application Source Code ---
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY main.py ./

# Finalize project installation and byte-compilation
RUN uv sync --frozen --no-dev

# --- User & Permissions Setup ---
# Create a dedicated non-root user for security
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser && \
    mkdir -p /app/uploads && \
    chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose default HTTP port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/')" || exit 1

# Start the FastAPI application with uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
