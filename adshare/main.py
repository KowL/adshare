"""FastAPI application entry point for adshare."""

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
from adshare.routers import financial, health, market, technical, fundamental, factor

# Setup logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Startup
    from adshare.adapters.amazingdata import get_adapter

    adapter = get_adapter()
    # Login on startup (local Mac has enough memory)
    try:
        login_ok = adapter.login()
        if login_ok:
            print("✅ AmazingData startup login successful")
        else:
            print("⚠️  AmazingData startup login failed, will retry on first request")
    except Exception as e:
        print(f"⚠️  AmazingData startup login error: {e}, will retry on first request")
        login_ok = False

    # Set service info for metrics
    SERVICE_INFO.info({"version": settings.app_version, "name": settings.app_name})

    yield

    # Shutdown
    adapter.logout()
    print("👋 adshare shutting down")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AmazingData shared data service - Financial data middleware",
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
            "docs": "/docs",
            "health": "/health",
            "metrics": settings.metrics_path if settings.metrics_enabled else None,
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
