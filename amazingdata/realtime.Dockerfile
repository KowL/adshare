# amazingdata/realtime.Dockerfile
# Realtime service — subscribes to AmazingData tick streams and writes
# Redis + Pub/Sub. Runs the same SDK session as batch, but in a separate
# container; operators schedule which one is up at a time (TGW single
# connection constraint).
#
# Build:
#   docker compose -f amazingdata/docker-compose.realtime.yml build
# Run:
#   docker compose -f amazingdata/docker-compose.realtime.yml up -d

FROM adshare-base:latest

WORKDIR /app

# Install worker-level Python deps (Redis client + adapter libs)
# NOTE: no pyarrow/duckdb here — realtime doesn't touch the warehouse.
RUN pip install --no-cache-dir \
    redis>=5.0 \
    pydantic>=2.9 \
    pydantic-settings>=2.6 \
    python-dotenv>=1.0 \
    structlog>=24.4

# Copy application code. Packages are imported straight from /app
# (PYTHONPATH=/app, and realtime.py also prepends the project root to
# sys.path), so no `pip install .` is needed — the workspace root
# pyproject is a hatch workspace and intentionally not installable.
COPY adshare/ ./adshare/
COPY amazingdata/ ./amazingdata/

# Runtime directories
RUN mkdir -p cache logs data

# Healthcheck: worker has no HTTP endpoint, check PID 1 alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD sh -c "kill -0 1"

CMD ["python", "-m", "amazingdata.realtime"]
