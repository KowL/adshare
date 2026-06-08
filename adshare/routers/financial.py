"""Financial data routers."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.logging import get_logger
from adshare.models.schemas import DataResponse, ErrorResponse, FinancialRequest, FinancialResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/financial", tags=["financial"])


@router.get("/statement", response_model=FinancialResponse)
async def get_financial(
    codes: str = Query(..., description="Comma-separated stock codes"),
    statement_type: str = Query(default="balance", description="balance, income, cashflow, profit_express, profit_notice"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
):
    """Get financial statement data."""
    try:
        adapter = get_adapter()
        df = adapter.get_financial(
            codes=codes,
            statement_type=statement_type,
            begin_date=begin_date,
            end_date=end_date,
        )

        data = df.to_dict(orient="records") if hasattr(df, "to_dict") else []

        return FinancialResponse(
            statement_type=statement_type,
            count=len(data),
            data=data,
        )
    except Exception as e:
        logger.error(f"get_financial failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shareholder", response_model=DataResponse)
async def get_shareholder(
    codes: str = Query(..., description="Comma-separated stock codes"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
):
    """Get shareholder data."""
    try:
        adapter = get_adapter()
        df = adapter.get_shareholder(
            codes=codes,
            begin_date=begin_date,
            end_date=end_date,
        )

        data = df.to_dict(orient="records") if hasattr(df, "to_dict") else []

        return DataResponse(count=len(data), data=data)
    except Exception as e:
        logger.error(f"get_shareholder failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
