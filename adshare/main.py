"""FastAPI application entry point for adshare.

This is the API-only service. It does NOT connect to AmazingData SDK directly.
It reads from:
- L3 historical warehouse (Parquet/DuckDB) for historical data
- Redis for real-time data (written by amazingdata-worker)
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from adshare.core.config import get_settings
from adshare.core.logging import setup_logging
from adshare.core.metrics import REQUEST_COUNT, REQUEST_DURATION, SERVICE_INFO, get_metrics
from adshare.core.ratelimit import get_limiter
from adshare.historical.admin import router as historical_admin_router
from adshare.historical.warehouse import get_warehouse
from adshare.routers import (
    factor,
    financial,
    fundamental,
    health,
    historical,
    market,
    realtime,
    technical,
)

# Setup logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Set service info for metrics
    SERVICE_INFO.info({"version": settings.app_version, "name": settings.app_name})

    # Initialise historical warehouse (L3) — API reads from local Parquet files
    try:
        if settings.historical_enabled:
            warehouse = get_warehouse(settings)
            health_info = warehouse.health()
            print(
                f"📦 Historical warehouse ready: root={health_info['root']} "
                f"duckdb_connected={health_info['duckdb_connected']}"
            )
        else:
            print("ℹ️  Historical warehouse disabled (HISTORICAL_ENABLED=false)")
    except Exception as e:
        print(f"⚠️  Historical warehouse init failed: {e}")

    # Start realtime broadcast service (Redis Pub/Sub → WebSocket/SSE)
    try:
        from adshare.services.realtime_broadcast import get_broadcast_service

        broadcast = get_broadcast_service()
        await broadcast.start()
        print("📡 Realtime broadcast service started")
    except Exception as e:
        print(f"⚠️  Realtime broadcast service init failed: {e}")

    yield

    # Shutdown broadcast service
    try:
        from adshare.services.realtime_broadcast import get_broadcast_service

        broadcast = get_broadcast_service()
        await broadcast.stop()
    except Exception:
        pass

    print("👋 adshare api shutting down")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AmazingData shared data service - Financial data middleware (API only)",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Rate limiter
    limiter = get_limiter()
    app.state.limiter = limiter

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Metrics middleware
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        method = request.method
        endpoint = request.url.path
        status = str(response.status_code)

        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
        REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

        return response

    # Register routers
    app.include_router(health.router)
    app.include_router(market.router)
    app.include_router(financial.router)
    app.include_router(technical.router)
    app.include_router(fundamental.router)
    app.include_router(factor.router)
    app.include_router(realtime.router)
    if settings.historical_enabled:
        app.include_router(historical.router)
        app.include_router(historical_admin_router)

    # Metrics endpoint
    if settings.metrics_enabled:
        @app.get(settings.metrics_path, response_class=PlainTextResponse)
        async def metrics():
            return get_metrics()

    @app.get("/")
    async def root():
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "mode": "api",
            "docs": "/docs",
            "health": "/health",
            "metrics": settings.metrics_path if settings.metrics_enabled else None,
            "realtime": "/realtime",
            "websocket": "/realtime/ws",
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "adshare.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
        log_level=settings.app_log_level.lower(),
    )
