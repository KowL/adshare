"""Domain exceptions for adshare.

Provides a structured exception hierarchy so that service code can raise
domain-specific errors and router code can map them to appropriate HTTP status
codes without coupling to implementation details.
"""


class AdshareException(Exception):
    """Base exception for all adshare domain errors."""


class ServiceError(AdshareException):
    """Domain error carrying an explicit HTTP status code.

    Base class for service-layer errors (e.g. analysis services) that know
    which HTTP status they should map to. ``message`` mirrors ``str(exc)``.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


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
    """Map a domain exception to an HTTP status code.

    An explicit ``status_code`` attribute on the instance (see
    :class:`ServiceError`) wins over the type-based mapping.
    """
    explicit = getattr(exc, "status_code", None)
    if isinstance(explicit, int):
        return explicit
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
