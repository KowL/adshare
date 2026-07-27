"""FastAPI application entry point for adshare.

This is the API-only service. It does NOT connect to AmazingData SDK directly.
It reads from:
- PostgreSQL for historical data
- Redis for real-time data (written by amazingdata.realtime)
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from adshare.core.config import get_settings
from adshare.core.auth import require_connection_auth
from adshare.core.exceptions import AdshareException, map_exception_to_http_status
from adshare.core.logging import setup_logging
from adshare.core.metrics import REQUEST_COUNT, REQUEST_DURATION, SERVICE_INFO, get_metrics
from adshare.core.ratelimit import get_limiter
from adshare.historical.warehouse import get_warehouse
from adshare.routers import (
    factor,
    financial,
    fundamental,
    health,
    realtime,
    status,
    technical,
    tushare,
)

# Setup logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Set service info for metrics
    SERVICE_INFO.info({"version": settings.app_version, "name": settings.app_name})

    # Initialise PostgreSQL repository.
    try:
        if settings.historical_enabled:
            warehouse = get_warehouse(settings)
            health_info = warehouse.health()
            print(
                f"🐘 PostgreSQL repository ready: "
                f"connected={health_info['database_connected']}"
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
        description="A-share shared data service - Financial data middleware (API only)",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Rate limiter
    limiter = get_limiter()
    app.state.limiter = limiter

    # Safety net: any domain exception that escapes a router is mapped to
    # its canonical HTTP status (same shape as HTTPException responses).
    @app.exception_handler(AdshareException)
    async def adshare_exception_handler(request: Request, exc: AdshareException):
        return JSONResponse(
            status_code=map_exception_to_http_status(exc),
            content={"detail": str(exc) or type(exc).__name__},
        )

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
    protected = [Depends(require_connection_auth)]

    app.include_router(health.router)
    app.include_router(status.router, dependencies=protected)
    app.include_router(financial.router, dependencies=protected)
    app.include_router(technical.router, dependencies=protected)
    app.include_router(fundamental.router, dependencies=protected)
    app.include_router(factor.router, dependencies=protected)
    app.include_router(realtime.router, dependencies=protected)
    app.include_router(tushare.router)

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
            "skill": "/skill",
            "metrics": settings.metrics_path if settings.metrics_enabled else None,
            "realtime": "/realtime",
            "websocket": "/realtime/ws",
            "dashboard": "/dashboard",
            "status": "/status",
        }

    dashboard_dir = Path(__file__).resolve().parent / "dashboard"
    if dashboard_dir.exists():
        app.mount(
            "/dashboard",
            StaticFiles(directory=str(dashboard_dir), html=True),
            name="dashboard",
        )

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
        log_level=settings.log_level.lower(),
    )
