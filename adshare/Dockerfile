# adshare/Dockerfile
# adshare API service image.
#
# Build context: adshare/  (this directory)
# Build command:
#   docker build -t adshare-api -f adshare/Dockerfile adshare
# Or via compose:
#   docker compose -f adshare/docker-compose.yml build
#
# Dependencies are pinned in requirements.lock (generated from pyproject.toml):
#   uv pip compile adshare/pyproject.toml --python-version 3.11 \
#     --python-platform linux -o adshare/requirements.lock
# Re-run that command after changing pyproject.toml dependencies.
#
# Secrets: .env files are excluded via .dockerignore — runtime config is
# injected by compose `env_file`, never baked into the image.

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE}

# PIP_INDEX_URL replaces `pip config set` (which wrote /root/.pip/pip.conf and
# would stop working for the non-root runtime user).
ENV TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Runtime system deps only: curl for the compose healthcheck, tzdata so the
# ENV TZ resolves to a real zoneinfo entry. No gcc / libhdf5-dev — every pinned
# dependency ships a manylinux wheel (PyTables wheels bundle HDF5), so no
# compiler toolchain belongs in the runtime image.
# apt sources are switched to the Tsinghua mirror for CN build speed
# (trixie uses deb822 debian.sources; older bases fall back to sources.list).
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# Install pinned third-party dependencies. This layer is cached as long as
# requirements.lock is unchanged.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Non-root runtime user.
RUN useradd --uid 10001 --home /app app

# Copy application code (secrets excluded by .dockerignore).
COPY . ./adshare/

# Runtime directories (cache/ and logs/ are bind-mounted by compose; on the
# Linux host they must be writable by uid 10001).
RUN mkdir -p cache logs data && chown -R app:app /app

USER app

EXPOSE 8000

# No HEALTHCHECK here — docker-compose.yml defines the single source of truth.
CMD ["uvicorn", "adshare.main:app", "--host", "0.0.0.0", "--port", "8000"]
