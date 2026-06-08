"""Health check routers."""

from datetime import datetime

from fastapi import APIRouter

from adshare.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.config import get_settings
from adshare.models.schemas import HealthResponse, LoginStatusResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    settings = get_settings()
    adapter = get_adapter()
    cache = get_cache_manager()

    cache_health = cache.health()

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        timestamp=datetime.now(),
        amazingdata_connected=adapter.is_logged_in,
        redis_connected=cache_health["redis_connected"],
        auth_enabled=settings.auth_enabled,
        rate_limit_enabled=settings.rate_limit_enabled,
        metrics_enabled=settings.metrics_enabled,
    )


@router.get("/login/status", response_model=LoginStatusResponse)
async def login_status():
    """AmazingData login status."""
    adapter = get_adapter()
    return LoginStatusResponse(
        is_logged_in=adapter.is_logged_in,
        login_info=adapter.login_info,
    )


@router.post("/login")
async def do_login():
    """Login to AmazingData."""
    adapter = get_adapter()
    success = adapter.login()
    return {"success": success, "logged_in": adapter.is_logged_in}


@router.post("/logout")
async def do_logout():
    """Logout from AmazingData."""
    adapter = get_adapter()
    adapter.logout()
    return {"success": True, "logged_in": False}
