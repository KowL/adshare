#!/bin/bash
# adshare 双服务部署脚本
#
# 架构:
#   - adshare-api        : FastAPI HTTP API (任意平台)
#   - amazingdata-worker : AmazingData SDK 数据拉取 (必须 linux/amd64)
#
# 部署步骤:
#   1. 在 amd64 服务器上执行此脚本
#   2. API 服务可在任意 Docker 宿主机运行
#   3. Worker 服务必须在支持 amd64 的宿主机运行 (或使用 QEMU 模拟)

set -e

PROJECT_DIR="/opt/adshare"

echo "=== adshare 双服务部署脚本 ==="

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose first."
    exit 1
fi

echo "✓ Docker available"

# Create project directory
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

echo "✓ Project directory: $PROJECT_DIR"

# Copy project files (run this on local machine first: scp -r adshare/ server:/opt/)
if [ ! -f "docker-compose.yml" ]; then
    echo "⚠️  Project files not found. Please copy adshare project to $PROJECT_DIR first:"
    echo "   scp -r /path/to/adshare server:$PROJECT_DIR"
    exit 1
fi

echo "✓ Project files found"

# Check architecture for worker
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ]; then
    echo "⚠️  Warning: Current architecture is $ARCH"
    echo "   amazingdata-worker requires linux/amd64."
    echo "   Options:"
    echo "   1. Deploy on an x86_64 server"
    echo "   2. Enable QEMU binfmt emulation: docker run --privileged --rm tonistiigi/binfmt --install amd64"
    echo "   3. Skip worker build and run worker on a separate amd64 host"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Build and start
echo "→ Building Docker images..."
docker compose build --no-cache

echo "→ Starting services..."
docker compose up -d

echo "→ Waiting for services to be ready..."
sleep 5

# Health check
echo "→ Health check..."
if curl -sf http://localhost:8000/health > /dev/null; then
    echo "✅ adshare-api is running at http://localhost:8000"
    curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || true
else
    echo "⚠️  API may not be ready yet. Check logs: docker logs adshare-api"
fi

if docker ps | grep -q amazingdata-worker; then
    echo "✅ amazingdata-worker is running"
else
    echo "⚠️  Worker not running. Check logs: docker logs amazingdata-worker"
fi

echo ""
echo "=== 部署完成 ==="
echo "API:       http://localhost:8000"
echo "Docs:      http://localhost:8000/docs"
echo "Metrics:   http://localhost:8000/metrics"
echo ""
echo "日志查看:"
echo "  API:     docker logs -f adshare-api"
echo "  Worker:  docker logs -f amazingdata-worker"
echo ""
echo "常用命令:"
echo "  重启:    docker compose restart"
echo "  停止:    docker compose down"
echo "  更新:    docker compose pull && docker compose up -d"
