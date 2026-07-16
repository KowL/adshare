"""Health check routers."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

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
        datasource_connected=False,
        redis_connected=cache_health["redis_connected"],
        auth_enabled=settings.auth_enabled,
        rate_limit_enabled=settings.rate_limit_enabled,
        metrics_enabled=settings.metrics_enabled,
    )


@router.get("/skill", response_class=Response)
async def get_skill():
    """Return the agent skill guide for this adshare deployment.

    This endpoint makes the skill.md available to external agents that can
    reach the API over HTTP but do not have SSH access to the server.
    """
    skill_path = Path("/opt/adshare/skills/adshare-remote-api-usage/SKILL.md")
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill file not found at {skill_path}")
    content = skill_path.read_text(encoding="utf-8")
    return Response(content=content, media_type="text/markdown; charset=utf-8")


@router.get("/login/status")
async def login_status():
    """Data-source login status — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="The data-source session is held by the worker service; "
        "login status is not available in the API service.",
    )


@router.post("/login")
async def do_login():
    """Login to the data source — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="The data-source session is held by the worker service; "
        "login is not available in the API service.",
    )


@router.post("/logout")
async def do_logout():
    """Logout from the data source — not available in API-only mode."""
    raise HTTPException(
        status_code=503,
        detail="The data-source session is held by the worker service; "
        "logout is not available in the API service.",
    )
