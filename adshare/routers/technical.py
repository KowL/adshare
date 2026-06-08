"""Technical analysis routers."""

import inspect
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from adshare.adapters.amazingdata import get_adapter
from adshare.core.cache import get_cache_manager
from adshare.core.logging import get_logger
from adshare.engines.technical.indicators import (
    ALL_INDICATORS,
    CATEGORY_MAP,
    get_category,
    get_indicator,
)
from adshare.models.schemas import TechnicalResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/technical", tags=["technical"])


def _format_value(value):
    """Format numeric value with appropriate precision."""
    if pd.isna(value):
        return None
    if isinstance(value, (int, pd.Int64Dtype)):
        return int(value)
    abs_val = abs(float(value))
    if abs_val >= 100:
        return round(float(value), 2)
    elif abs_val >= 10:
        return round(float(value), 3)
    elif abs_val >= 1:
        return round(float(value), 4)
    else:
        return round(float(value), 6)


def _get_indicator_params(ind_func, close, high, low, volume, open_, amount):
    """Build parameter dict based on indicator function signature."""
    sig = inspect.signature(ind_func)
    params = {}
    param_names = list(sig.parameters.keys())
    
    if 'close' in param_names:
        params['close'] = close
    if 'high' in param_names:
        params['high'] = high
    if 'low' in param_names:
        params['low'] = low
    if 'volume' in param_names:
        params['volume'] = volume
    if 'open_' in param_names:
        params['open_'] = open_
    if 'amount' in param_names:
        params['amount'] = amount
    
    return params


def _get_kline_data(
    code: str,
    begin_date: Optional[int] = None,
    end_date: Optional[int] = None,
) -> pd.DataFrame:
    """Get K-line data for technical analysis."""
    adapter = get_adapter()
    cache = get_cache_manager()

    # Default date range
    if end_date is None:
        from datetime import datetime
        end_date = int(datetime.now().strftime("%Y%m%d"))
    if begin_date is None:
        begin_date = 20240101

    cache_key = ("kline_tech", code, str(begin_date), str(end_date))
    cached = cache.get_unified("kline", *cache_key)
    if cached is not None:
        return cached

    df = adapter.get_kline(
        codes=code,
        begin_date=begin_date,
        end_date=end_date,
        period="day",
    )
    cache.set_unified("kline", df, *cache_key)
    return df


@router.get("/analyze", response_model=TechnicalResponse)
async def analyze_technical(
    code: str = Query(..., description="Stock code, e.g. 000001.SZ"),
    begin_date: Optional[int] = Query(default=None, description="Start date YYYYMMDD"),
    end_date: Optional[int] = Query(default=None, description="End date YYYYMMDD"),
    indicator: Optional[str] = Query(default=None, description="Specific indicator name"),
    category: Optional[str] = Query(
        default=None,
        description="Category: overbought_oversold, trend, energy, volume, ma, path, other",
    ),
):
    """Run technical analysis for a stock."""
    try:
        df = _get_kline_data(code, begin_date, end_date)

        if len(df) == 0:
            raise HTTPException(status_code=404, detail=f"No K-line data for {code}")

        close = df["close"]
        open_ = df["open"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        amount = df.get("amount", close * volume)

        # Latest price info
        last_date = str(df.index[-1]) if hasattr(df, "index") else "N/A"
        if "kline_time" in df.columns:
            last_date = str(df["kline_time"].iloc[-1])

        price_info = {
            "open": round(float(open_.iloc[-1]), 2),
            "high": round(float(high.iloc[-1]), 2),
            "low": round(float(low.iloc[-1]), 2),
            "close": round(float(close.iloc[-1]), 2),
            "volume": int(volume.iloc[-1]),
            "amount": round(float(amount.iloc[-1]), 2),
        }

        # Determine which indicators to calculate
        if indicator:
            # Single indicator mode
            ind_func = get_indicator(indicator)
            if ind_func is None:
                raise HTTPException(status_code=404, detail=f"Indicator {indicator} not found")

            try:
                params = _get_indicator_params(ind_func, close, high, low, volume, open_, amount)
                result = ind_func(**params)
                values = {k: _format_value(v.iloc[-1]) for k, v in result.items()}
                # Wrap single indicator in proper category structure
                categories = {
                    "indicator": {
                        "name": indicator.upper(),
                        "indicators": [
                            {
                                "name": indicator.upper(),
                                "values": values,
                            }
                        ],
                    }
                }
            except Exception as e:
                logger.error(f"Indicator {indicator} calculation failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        else:
            # Category or all mode
            categories_to_calc = {}
            if category:
                if category not in CATEGORY_MAP:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Category {category} not found. Available: {list(CATEGORY_MAP.keys())}",
                    )
                categories_to_calc = {category: CATEGORY_MAP[category]}
            else:
                categories_to_calc = CATEGORY_MAP

            categories = {}
            for cat_key, indicators in categories_to_calc.items():
                cat_results = {}
                for ind_name, ind_func in indicators:
                    try:
                        params = _get_indicator_params(ind_func, close, high, low, volume, open_, amount)
                        result = ind_func(**params)
                        values = {k: _format_value(v.iloc[-1]) for k, v in result.items()}
                        cat_results[ind_name] = values
                    except Exception as e:
                        logger.warning(f"Indicator {ind_name} failed: {e}")
                        cat_results[ind_name] = {"error": str(e)}

                categories[cat_key] = {
                    "name": cat_key,
                    "indicators": cat_results,
                }

        return TechnicalResponse(
            code=code,
            date=last_date,
            price=price_info,
            categories=categories,
            count=len(categories),
            data=categories,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Technical analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indicators")
async def list_indicators():
    """List all available technical indicators."""
    result = {}
    for cat_key, indicators in CATEGORY_MAP.items():
        result[cat_key] = [name for name, _ in indicators]
    return result
