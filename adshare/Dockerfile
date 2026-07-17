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
#   - pyproject.toml references `readme = "README.adshare-stub.md"`, which
#     hatch validates when building the wheel; at runtime the package
#     doesn't read it. Keep the filename intact when COPYing.

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE}

WORKDIR /app

# Use Tsinghua pip mirror for faster downloads in CN
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# System deps (gcc for any C-extension deps, curl for healthcheck, libhdf5 for tables)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install adshare and all its declared dependencies.
# pyproject.toml is self-contained under adshare/.
COPY pyproject.toml ./

# pyproject.toml declares `readme = "README.adshare-stub.md"` — hatch
# validates that path when building the wheel, so copy it under its real
# name. (Not read at runtime.)
COPY README.adshare-stub.md ./

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
