"""Financial data routers."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.core.logging import get_logger
from adshare.models.schemas import FinancialResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/financial", tags=["financial"])


@router.get("/statement", response_model=FinancialResponse)
async def get_financial(
    codes: str = Query(..., description="Comma-separated stock codes"),
    statement_type: str = Query(default="balance", description="balance, income, cashflow, profit_express, profit_notice"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
):
    """Get financial statement data.

    Not available in API-only mode — requires AmazingData SDK (worker service).
    """
    raise HTTPException(
        status_code=503,
        detail="Financial data requires AmazingData SDK. Use the worker service.",
    )


@router.get("/shareholder")
async def get_shareholder(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
):
    """Get shareholder data.

    Not available in API-only mode — requires AmazingData SDK (worker service).
    """
    raise HTTPException(
        status_code=503,
        detail="Shareholder data requires AmazingData SDK. Use the worker service.",
    )
