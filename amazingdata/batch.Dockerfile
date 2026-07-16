# amazingdata/batch.Dockerfile
# Batch service — APScheduler drives periodic sync jobs (K-line / meta /
# reference) into the local Parquet warehouse. Separate container from
# realtime; same SDK session constraint applies.
#
# Build:
#   docker compose -f amazingdata/docker-compose.batch.yml build
# Run:
#   docker compose -f amazingdata/docker-compose.batch.yml up -d

FROM adshare-base:latest

WORKDIR /app

# Project-level files
COPY pyproject.toml README.md ./

# Install worker-level Python deps (warehouse + scheduler)
# Note: numba/scipy/statsmodels/pydantic are inherited from base image.
RUN pip install --no-cache-dir \
    duckdb>=1.0.0,<2.0 \
    pandas==2.0.3 \
    numpy==1.26.4 \
    pyarrow==15.0.0 \
    redis>=5.0 \
    pydantic>=2.9 \
    pydantic-settings>=2.6 \
    python-dotenv>=1.0 \
    apscheduler>=3.10 \
    structlog>=24.4 \
    tables>=3.9

# Install adshare package itself (no deps)
RUN pip install --no-cache-dir --no-deps .

# Copy application code
COPY adshare/ ./adshare/
COPY amazingdata/ ./amazingdata/
COPY scripts/ ./scripts/

# Runtime directories
RUN mkdir -p cache logs data

# Healthcheck: worker has no HTTP endpoint, check PID 1 alive
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD sh -c "kill -0 1"

CMD ["python", "-m", "amazingdata.batch"]
