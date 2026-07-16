# adshare/Dockerfile
# adshare API service image.
#
# Build context: adshare/  (this directory)
# Build command:
#   docker build -t adshare-api -f adshare/Dockerfile adshare
# Or via compose:
#   docker compose -f adshare/docker-compose.yml build
#
# Self-contained: pyproject.toml sits next to this Dockerfile and declares
# all runtime dependencies. No external build-context needed.
#
# Notes:
#   - The README referenced by pyproject.toml (../README.md) is only needed
#     by hatch when building the wheel; at runtime the package doesn't read
#     it, so we don't COPY it into the image.

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE}

WORKDIR /app

# Use Alibaba Cloud pip mirror for faster downloads in CN
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# System deps (gcc for any C-extension deps, curl for healthcheck, libhdf5 for tables)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install adshare and all its declared dependencies.
# pyproject.toml is self-contained under adshare/.
COPY pyproject.toml ./

# Place a minimal README so pyproject.toml's `readme = "../README.md"`
# reference doesn't fail when hatch builds the wheel inside the image.
# (We don't actually need it at runtime; hatch only validates the path.)
COPY README.adshare-stub.md ./README.md

RUN pip install --no-cache-dir .

# Copy application code
COPY . ./adshare/

# Runtime directories
RUN mkdir -p cache logs data

EXPOSE 8000

# Healthcheck: API exposes /health
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "adshare.main:app", "--host", "0.0.0.0", "--port", "8000"]
