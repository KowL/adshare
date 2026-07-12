# adshare_base/Dockerfile
# Base image for adshare services (currently amazingdata-worker).
#
# Purpose: cache the slow, rarely-changing setup steps (apt + SDK + C-extension
# pip dependencies) so that downstream service Dockerfiles only need to install
# application code. Editing adshare/ source code should NEVER trigger
# re-installing tgw/scipy/numba.
#
# Build manually:
#   bin/build-base.sh
# (DO NOT add to docker-compose.yml — base is built once and tagged, not a service)
#
# Maintenance:
#   - Update python:3.11-slim       → re-run bin/build-base.sh
#   - Upgrade AmazingData/tgw whl   → drop new .whl here + re-run bin/build-base.sh
#   - Add new system apt package    → edit + re-run bin/build-base.sh
#   - Add new pip dep for SDK       → add here + re-run bin/build-base.sh
#
# Platform: linux/amd64 (required by AmazingData C extensions)

FROM python:3.11-slim

# 使用阿里云 pip 镜像
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

# 系统依赖（gcc/curl 是 worker 编译 + 健康检查需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 工作目录
WORKDIR /base

# 复制 SDK whl 文件（AmazingData 强制要求 pydantic + numba + scipy + statsmodels）
COPY AmazingData-1.0.30-cp311-none-any.whl tgw-1.0.8.7-py3-none-any.whl ./

# 安装 SDK（AmazingData-1.0.30 的 METADATA 未声明依赖，需显式安装）
# 这层构建耗时 1-3 分钟（llvmlite 编译），所以放进 base
RUN pip install --no-cache-dir \
    tgw-1.0.8.7-py3-none-any.whl \
    AmazingData-1.0.30-cp311-none-any.whl \
    pydantic>=2.6.4 \
    numba \
    scipy \
    statsmodels

# 健康检查标记（base 本身不跑服务，但保持平台一致）
LABEL org.adshare.base="adshare-base:1.0"
LABEL org.adshare.base.python="3.11-slim"
LABEL org.adshare.base.amazingdata="1.0.30"
LABEL org.adshare.base.tgw="1.0.8.7"
