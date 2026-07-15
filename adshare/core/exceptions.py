"""Domain exceptions for adshare.

Provides a structured exception hierarchy so that service code can raise
domain-specific errors and router code can map them to appropriate HTTP status
codes without coupling to implementation details.
"""


class AdshareException(Exception):
    """Base exception for all adshare domain errors."""


class InvalidParameterError(AdshareException):
    """Raised when request parameters fail validation."""


class AuthenticationError(AdshareException):
    """Raised when authentication credentials are missing or invalid."""


class AuthorizationError(AdshareException):
    """Raised when the caller lacks permission for the operation."""


class DataNotFoundError(AdshareException):
    """Raised when requested data does not exist."""


class WarehouseNotAvailableError(AdshareException):
    """Raised when the historical warehouse is disabled or unreachable."""


class UpstreamError(AdshareException):
    """Raised when an upstream dependency (e.g. SDK, Redis) fails."""


class NotImplementedApiError(AdshareException):
    """Raised when the requested API is not implemented."""


class CacheError(AdshareException):
    """Raised when cache read/write fails in a non-degradable way."""


# ---------------------------------------------------------------------------
# HTTP status mapping
# ---------------------------------------------------------------------------


def map_exception_to_http_status(exc: AdshareException) -> int:
    """Map a domain exception to an HTTP status code."""
    mapping = {
        InvalidParameterError: 400,
        AuthenticationError: 401,
        AuthorizationError: 403,
        DataNotFoundError: 404,
        WarehouseNotAvailableError: 404,
        UpstreamError: 503,
        NotImplementedApiError: 501,
        CacheError: 503,
    }
    return mapping.get(type(exc), 500)


def exception_message(exc: Exception) -> str:
    """Return a safe message string for an exception."""
    return str(exc) or type(exc).__name__
