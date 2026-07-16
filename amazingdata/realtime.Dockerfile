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

# Project-level files (pyproject + README needed for `pip install .`)
COPY pyproject.toml README.md ./

# Install worker-level Python deps (Redis client + adapter libs)
# NOTE: no pyarrow/duckdb here — realtime doesn't touch the warehouse.
RUN pip install --no-cache-dir \
    redis>=5.0 \
    pydantic>=2.9 \
    pydantic-settings>=2.6 \
    python-dotenv>=1.0 \
    structlog>=24.4

# Install adshare package itself (no deps — already covered above)
RUN pip install --no-cache-dir --no-deps .

# Copy application code
COPY adshare/ ./adshare/
COPY amazingdata/ ./amazingdata/

# Runtime directories
RUN mkdir -p cache logs data

# Healthcheck: worker has no HTTP endpoint, check PID 1 alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD sh -c "kill -0 1"

CMD ["python", "-m", "amazingdata.realtime"]
