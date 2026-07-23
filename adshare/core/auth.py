"""API Key authentication middleware for adshare."""

from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from fastapi.security.api_key import APIKeyQuery
from starlette.requests import HTTPConnection

from adshare.core.config import get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)

# API Key sources: header (preferred) or query param
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


async def get_optional_api_key(
    header_key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """Extract an API key without requiring one.

    Whether a missing key is allowed depends on ``AUTH_ENABLED`` and is
    decided by :class:`APIKeyAuth`.
    """
    return header_key or query_key


async def get_api_key(
    header_key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Security(api_key_query),
) -> str:
    """Extract API key from header or query parameter."""
    key = header_key or query_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required. Pass via X-API-Key header or api_key query parameter.",
        )
    return key


class APIKeyAuth:
    """API Key authentication dependency."""

    def __init__(self, enabled: Optional[bool] = None):
        self.enabled = enabled

    async def __call__(
        self,
        api_key: Optional[str] = Depends(get_optional_api_key),
    ) -> str:
        settings = get_settings()
        is_enabled = self.enabled if self.enabled is not None else settings.auth_enabled

        if not is_enabled:
            return api_key or ""

        # Validate key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key required. Pass via X-API-Key header or api_key query parameter.",
            )

        valid_key = settings.api_key
        if not valid_key:
            logger.warning("Auth enabled but no API_KEY configured")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server misconfiguration: API_KEY not set",
            )

        if api_key != valid_key:
            logger.warning(f"Invalid API key attempt: {api_key[:8]}...")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid API key",
            )

        return api_key


# Convenience instances
require_auth = APIKeyAuth()
optional_auth = APIKeyAuth(enabled=False)


async def require_connection_auth(connection: HTTPConnection) -> str:
    """Authenticate both HTTP requests and WebSocket handshakes."""
    settings = get_settings()
    key = (
        connection.headers.get("X-API-Key")
        or connection.query_params.get("api_key")
    )
    if not settings.auth_enabled:
        return key or ""
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required. Pass via X-API-Key header or api_key query parameter.",
        )
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY not configured",
        )
    if key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return key


async def verify_api_key(
    api_key: Optional[str] = Depends(get_optional_api_key),
) -> str:
    """Fast dependency to verify API key when auth is enabled."""
    settings = get_settings()
    if not settings.auth_enabled:
        return api_key or ""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required. Pass via X-API-Key header or api_key query parameter.",
        )
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY not configured",
        )
    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return api_key
