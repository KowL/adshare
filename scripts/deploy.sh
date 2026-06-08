#!/bin/bash
# adshare deployment script for x86_64 Linux servers

set -e

PROJECT_DIR="/opt/adshare"
IMAGE_NAME="adshare-adshare"

echo "=== adshare Deployment Script ==="

# Check architecture
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ]; then
    echo "❌ Error: AmazingData SDK requires x86_64 (amd64), current: $ARCH"
    exit 1
fi

echo "✓ Architecture: $ARCH"

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

# Build and start
echo "→ Building Docker image..."
docker compose build --no-cache

echo "→ Starting services..."
docker compose up -d

echo "→ Waiting for service to be ready..."
sleep 5

# Health check
echo "→ Health check..."
if curl -sf http://localhost:8000/health > /dev/null; then
    echo "✅ adshare is running at http://localhost:8000"
    curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || true
else
    echo "⚠️  Service may not be ready yet. Check logs: docker logs adshare"
fi

echo ""
echo "=== Deployment Complete ==="
echo "API:       http://localhost:8000"
echo "Docs:      http://localhost:8000/docs"
echo "Metrics:   http://localhost:8000/metrics"
echo "Logs:      docker logs -f adshare"
echo "Restart:   docker compose restart"
echo "Stop:      docker compose down"
