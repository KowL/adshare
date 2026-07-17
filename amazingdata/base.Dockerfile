# amazingdata/base.Dockerfile
# Base image for the AmazingData SDK (Linux/amd64 only).
#
# Purpose:
#   Cache the slow, rarely-changing setup (apt + SDK whl + C-extension pip
#   dependencies) so realtime.Dockerfile / batch.Dockerfile only need to
#   install application code. Editing adshare/ source code should NEVER
#   trigger re-installing tgw/scipy/numba.
#
# Build:
#   bin/build-base.sh
#   (DO NOT add to docker-compose.yml — base is built once and tagged,
#   not a service)
#
# Maintenance:
#   - Upgrade python:3.11-slim       → re-run bin/build-base.sh
#   - Upgrade AmazingData/tgw whl   → drop new .whl in amazingdata/wheels/ + re-run
#   - Add new system apt package    → edit + re-run bin/build-base.sh
#   - Add new pip dep for SDK       → add here + re-run bin/build-base.sh
#
# Platform: linux/amd64 (required by AmazingData C extensions)

FROM python:3.11-slim

# Use Alibaba Cloud pip mirror for faster downloads in CN
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# System dependencies (gcc/curl for compile + healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libhdf5-dev \
    rsync \
    openssh-client \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /base

# Copy SDK wheels (AmazingData requires pydantic + numba + scipy + statsmodels)
COPY amazingdata/wheels/ ./wheels/

# Install SDK — AmazingData METADATA doesn't declare deps, so we list them.
# This layer takes 1-3 min (llvmlite compile), so it belongs in base.
RUN pip install --no-cache-dir \
    ./wheels/tgw-1.0.8.7-py3-none-any.whl \
    ./wheels/AmazingData-1.1.8-cp311-none-any.whl \
    pydantic>=2.6.4 \
    numba \
    scipy \
    statsmodels

LABEL org.adshare.base="adshare-base:1.1"
LABEL org.adshare.base.python="3.11-slim"
LABEL org.adshare.base.amazingdata="1.1.8"
LABEL org.adshare.base.tgw="1.0.8.7"
