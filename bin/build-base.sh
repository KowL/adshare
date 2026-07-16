#!/usr/bin/env bash
# bin/build-base.sh — 手动构建 adshare base image
#
# 为什么不在 docker-compose.yml 里 build？
# - base 是"中间产物"，不是服务
# - base 一年改 2-3 次（升级 whl / 换 base 镜像 / 加 apt 包）
# - realtime 和 batch 经常改，频繁触发 base 重 build 是浪费
# - 手动 build + tag 锁版本，出问题能快速回滚
#
# 用法:
#   bin/build-base.sh              # 默认 tag: adshare-base:latest
#   bin/build-base.sh 1.1          # tag: adshare-base:1.1
#   bin/build-base.sh --no-cache 2.0  # 从零 build，tag: adshare-base:2.0
#
# 预计耗时：
#   首次（cold）：3-5 分钟（apt 80s + SDK whl + numba 编译 1-2 分钟）
#   增量（warm）：10-30 秒（只下变更层）
#
# wheels 位置: amazingdata/wheels/
# Dockerfile 位置: amazingdata/base.Dockerfile

set -euo pipefail

# 参数解析
TAG="latest"
USE_CACHE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache)
      USE_CACHE="--no-cache"
      shift
      ;;
    *)
      TAG="$1"
      shift
      ;;
  esac
done

# 定位项目根
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKERFILE="$PROJECT_ROOT/amazingdata/base.Dockerfile"

# 检查 whl 文件（位于 amazingdata/wheels/）
for f in AmazingData-1.0.30-cp311-none-any.whl tgw-1.0.8.7-py3-none-any.whl; do
  if [[ ! -f "$PROJECT_ROOT/amazingdata/wheels/$f" ]]; then
    echo "❌ Missing $PROJECT_ROOT/amazingdata/wheels/$f"
    echo "   Run from project root with whl files present."
    exit 1
  fi
done

# 检查 Dockerfile
if [[ ! -f "$DOCKERFILE" ]]; then
  echo "❌ Missing $DOCKERFILE"
  exit 1
fi

# 计时
START=$(date +%s)
echo "🔨 Building adshare-base:$TAG (from $DOCKERFILE)"
echo "   Build context: $PROJECT_ROOT"
echo ""

# Build
cd "$PROJECT_ROOT"
docker build \
  --platform linux/amd64 \
  $USE_CACHE \
  -f "$DOCKERFILE" \
  -t "adshare-base:$TAG" \
  -t "adshare-base:latest" \
  .

# 报告
END=$(date +%s)
DURATION=$((END - START))
echo ""
echo "✅ Built adshare-base:$TAG in ${DURATION}s"
echo ""
echo "📦 Image info:"
docker images adshare-base --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}"
echo ""
echo "🔍 Next steps:"
echo "  - Batch:    docker compose -f amazingdata/docker-compose.batch.yml build && docker compose -f amazingdata/docker-compose.batch.yml up -d
  - Realtime: docker compose -f amazingdata/docker-compose.realtime.yml build && docker compose -f amazingdata/docker-compose.realtime.yml up -d"
echo "  - Verify: docker run --rm adshare-base:latest python -c 'import tgw, AmazingData; print(\"SDK OK\")'"
