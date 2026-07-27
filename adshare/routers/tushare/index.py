"""Tushare Pro compatible index data handlers.

This module reserves the index namespace in the unified ``POST /tushare``
entry point. Currently adshare's L3 warehouse focuses on A-share stock
data; index endpoints will be expanded as warehouse coverage grows.
"""

from __future__ import annotations

from typing import Any, Optional

from adshare.core.exceptions import NotImplementedApiError


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


HANDLERS: dict[str, Any] = {
    "index_basic": handle_index_basic,
    "index_daily": handle_index_daily,
}
