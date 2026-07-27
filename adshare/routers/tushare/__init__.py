"""Tushare Pro compatible router.

Single-entry protocol matching the official Tushare Pro HTTP API:

* ``POST /tushare`` — body ``{"api_name": "...", "token": "...", "params": {...}, "fields": ""}``

The server dispatches to the appropriate handler based on ``api_name``.
All handlers live in this package's submodules and are registered in
their ``HANDLERS`` dicts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from adshare import dependencies as deps
from adshare.core.exceptions import NotImplementedApiError
from adshare.core.logging import get_logger
from adshare.routers.tushare.common import (
    check_tushare_auth,
    extract_tushare_params,
    handle_tushare_exception,
    parse_request_body,
)
from adshare.routers.tushare import index, realtime, stock
from adshare.services.limit_up import LimitDownService, LimitUpService
from adshare.services.market_data import MarketDataService

logger = get_logger(__name__)

router = APIRouter(prefix="/tushare", tags=["tushare"])


@router.post("")
async def tushare_unified_entry(
    request: Request,
    service: MarketDataService = Depends(deps.get_market_data_service_dep),
    up_service: LimitUpService = Depends(deps.get_limit_up_service_dep),
    down_service: LimitDownService = Depends(deps.get_limit_down_service_dep),
):
    """Tushare Pro protocol compatible unified entry point."""
    # Auth check is performed in-handler so the response stays HTTP 200
    # on failure (Tushare Pro clients inspect ``code``, not HTTP status).
    auth_result = await check_tushare_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    try:
        body = await parse_request_body(request)
        api_name, params, fields, token = extract_tushare_params(body)
        logger.info(f"tushare unified api_name={api_name} params={params}")

        handler = _resolve_handler(api_name)
        if handler is None:
            return handle_tushare_exception(
                NotImplementedApiError(f"api_name={api_name} not supported")
            )

        return handler(
            params,
            fields,
            service=service,
            up_service=up_service,
            down_service=down_service,
        )
    except Exception as exc:
        return handle_tushare_exception(exc)


def _resolve_handler(api_name: str) -> Any:
    """Return the handler for a tushare api_name, or None if unsupported."""
    return (
        stock.HANDLERS.get(api_name)
        or index.HANDLERS.get(api_name)
        or realtime.HANDLERS.get(api_name)
    )
