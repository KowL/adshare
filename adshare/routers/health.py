"""Health check routers."""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    settings = get_settings()
    cache = get_cache_manager()

    cache_health = cache.health()

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        timestamp=datetime.now(),
        amazingdata_connected=False,
        redis_connected=cache_health["redis_connected"],
        auth_enabled=settings.auth_enabled,
        rate_limit_enabled=settings.rate_limit_enabled,
        metrics_enabled=settings.metrics_enabled,
    )


@router.get("/login/status")
async def login_status():
    """AmazingData login status — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="AmazingData SDK is not available in the API service. Use the worker service.",
    )


@router.post("/login")
async def do_login():
    """Login to AmazingData — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="AmazingData SDK is not available in the API service. Use the worker service.",
    )


@router.post("/logout")
async def do_logout():
    """Logout from AmazingData — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="AmazingData SDK is not available in the API service. Use the worker service.",
    )
