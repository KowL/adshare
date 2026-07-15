"""Tushare Pro compatible index data endpoints.

This module reserves the ``/tushare/index/*`` namespace. Currently adshare's
L3 warehouse focuses on A-share stock data; index endpoints will be expanded
as warehouse coverage grows.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter

from adshare.core.exceptions import NotImplementedApiError
from adshare.routers.tushare.common import handle_tushare_exception

router = APIRouter(prefix="/index", tags=["tushare-index"])


# ---------------------------------------------------------------------------
# Core handlers
# ---------------------------------------------------------------------------


def handle_index_basic(
    params: dict[str, Any], fields: Optional[list[str]], **kwargs
) -> dict[str, Any]:
    """Tushare Pro ``index_basic`` endpoint (reserved)."""
    raise NotImplementedApiError("index_basic is not yet implemented")


def handle_index_daily(
    params: dict[str, Any], fields: Optional[list[str]], **kwargs
) -> dict[str, Any]:
    """Tushare Pro ``index_daily`` endpoint (reserved)."""
    raise NotImplementedApiError("index_daily is not yet implemented")


# ---------------------------------------------------------------------------
# RESTful route wrappers
# ---------------------------------------------------------------------------


@router.post("/basic")
@router.get("/basic")
async def tushare_index_basic():
    """Tushare Pro ``index_basic`` endpoint (reserved)."""
    try:
        return handle_index_basic({}, None)
    except Exception as exc:
        return handle_tushare_exception(exc)


@router.post("/daily")
@router.get("/daily")
async def tushare_index_daily():
    """Tushare Pro ``index_daily`` endpoint (reserved)."""
    try:
        return handle_index_daily({}, None)
    except Exception as exc:
        return handle_tushare_exception(exc)


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    "index_basic": handle_index_basic,
    "index_daily": handle_index_daily,
}
