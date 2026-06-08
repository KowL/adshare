"""Rate limiting middleware for adshare using SlowAPI."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from adshare.core.config import get_settings


def get_limiter() -> Limiter:
    """Get configured rate limiter instance."""
    settings = get_settings()
    return Limiter(
        key_func=get_remote_address,
        default_limits=[
            f"{settings.rate_limit_per_minute} per minute",
            f"{settings.rate_limit_per_second} per second",
        ],
        enabled=settings.rate_limit_enabled,
    )
