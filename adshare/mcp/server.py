"""MCP Server for adshare.

Provides MCP protocol interface for adshare data services.
Compatible with Model Context Protocol (MCP) for AI agent integration.
"""

import json
import os
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from adshare.adapters.amazingdata import get_adapter
from adshare.core.config import get_settings

# Create FastMCP instance
mcp = FastMCP("adshare")

# Global adapter instance
_adapter = None


def _get_adapter():
    """Get or create adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = get_adapter()
    return _adapter


# ==================== MCP Tools ====================

@mcp.tool()
async def get_login_status() -> dict:
    """Get current login status."""
    adapter = _get_adapter()
    return {
        "is_logged_in": adapter.is_connected(),
        "host": adapter.host,
        "port": adapter.port,
    }


@mcp.tool()
async def get_code_list(security_type: str = "EXTRA_STOCK_A_SH_SZ") -> dict:
    """Get security code list.

    Args:
        security_type: Security type, e.g. EXTRA_STOCK_A_SH_SZ, EXTRA_ETF, EXTRA_KZZ
    """
    try:
        adapter = _get_adapter()
        codes = adapter.get_code_list(security_type=security_type)
        return {
            "success": True,
            "count": len(codes),
            "codes": codes[:100] if len(codes) > 100 else codes,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_kline(
    code: str,
    begin_date: int = 20240101,
    end_date: Optional[int] = None,
    period: str = "day",
    fields: Optional[List[str]] = None,
) -> dict:
    """Get K-line data.

    Args:
        code: Stock code, e.g. 000001.SZ
        begin_date: Start date YYYYMMDD
        end_date: End date YYYYMMDD (default: today)
        period: K-line period: day, min1, min5, week, month
        fields: Fields to return, e.g. ["open", "high", "low", "close", "volume"]
    """
    try:
        adapter = _get_adapter()
        if end_date is None:
            from datetime import datetime
            end_date = int(datetime.now().strftime("%Y%m%d"))

        df = adapter.get_kline(
            codes=code,
            begin_date=begin_date,
            end_date=end_date,
            period=period,
        )

        if df.empty:
            return {"success": False, "error": "No data found"}

        # Select fields
        if fields:
            available = [f for f in fields if f in df.columns]
            df = df[available]

        # Convert to records
        records = df.reset_index().to_dict("records")
        # Convert timestamps to strings
        for rec in records:
            for key, val in rec.items():
                if hasattr(val, "isoformat"):
                    rec[key] = val.isoformat()

        return {
            "success": True,
            "code": code,
            "count": len(records),
            "fields": list(df.columns),
            "data": records[:50] if len(records) > 50 else records,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_snapshot(
    code: str,
    date: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> dict:
    """Get snapshot data.

    Args:
        code: Stock code, e.g. 000001.SZ
        date: Date YYYYMMDD (default: latest)
        fields: Fields to return
    """
    try:
        adapter = _get_adapter()
        if date is None:
            from datetime import datetime
            date = int(datetime.now().strftime("%Y%m%d"))

        df = adapter.get_snapshot(codes=code, date=date)

        if df.empty:
            return {"success": False, "error": "No snapshot data found"}

        if fields:
            available = [f for f in fields if f in df.columns]
            df = df[available]

        records = df.reset_index().to_dict("records")
        for rec in records:
            for key, val in rec.items():
                if hasattr(val, "isoformat"):
                    rec[key] = val.isoformat()

        return {
            "success": True,
            "code": code,
            "count": len(records),
            "data": records,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_stock_basic(code: str) -> dict:
    """Get stock basic information.

    Args:
        code: Stock code, e.g. 000001.SZ
    """
    try:
        adapter = _get_adapter()
        df = adapter.get_stock_basic(codes=code)

        if df.empty:
            return {"success": False, "error": "No basic info found"}

        records = df.reset_index().to_dict("records")
        return {
            "success": True,
            "code": code,
            "data": records[0] if records else {},
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_technical_indicators(
    code: str,
    indicator: Optional[str] = None,
    category: Optional[str] = None,
    begin_date: int = 20240101,
    end_date: Optional[int] = None,
) -> dict:
    """Get technical indicators.

    Args:
        code: Stock code, e.g. 000001.SZ
        indicator: Specific indicator name, e.g. MACD, KDJ
        category: Indicator category, e.g. overbought_oversold, trend, energy, volume, ma, path, other
        begin_date: Start date YYYYMMDD
        end_date: End date YYYYMMDD
    """
    try:
        import httpx

        settings = get_settings()
        base_url = f"http://{settings.app.host}:{settings.app.port}"

        params = {"code": code, "begin_date": begin_date}
        if end_date:
            params["end_date"] = end_date
        if indicator:
            params["indicator"] = indicator
        if category:
            params["category"] = category

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/technical/analyze", params=params, timeout=60.0)
            data = resp.json()

        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_fundamental_factors(
    code: str,
    category: Optional[str] = None,
    factor: Optional[str] = None,
    begin_date: int = 20200101,
    end_date: Optional[int] = None,
) -> dict:
    """Get fundamental factors.

    Args:
        code: Stock code, e.g. 000001.SZ
        category: Factor category, e.g. profitability, growth, efficiency
        factor: Specific factor name
        begin_date: Start date YYYYMMDD
        end_date: End date YYYYMMDD
    """
    try:
        import httpx

        settings = get_settings()
        base_url = f"http://{settings.app.host}:{settings.app.port}"

        params = {"code": code, "begin_date": begin_date}
        if end_date:
            params["end_date"] = end_date
        if category:
            params["category"] = category
        if factor:
            params["factor"] = factor

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/fundamental/analyze", params=params, timeout=60.0)
            data = resp.json()

        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_factor_analysis(
    factor_name: str,
    stock_list: str,
    begin_date: int = 20240101,
    end_date: Optional[int] = None,
    benchmark: str = "000300.SH",
) -> dict:
    """Get factor analysis report.

    Args:
        factor_name: Factor name, e.g. ma5, momentum
        stock_list: Comma-separated stock codes
        begin_date: Start date YYYYMMDD
        end_date: End date YYYYMMDD
        benchmark: Benchmark index code
    """
    try:
        import httpx

        settings = get_settings()
        base_url = f"http://{settings.app.host}:{settings.app.port}"

        params = {
            "factor_name": factor_name,
            "stock_list": stock_list,
            "begin_date": begin_date,
            "benchmark": benchmark,
        }
        if end_date:
            params["end_date"] = end_date

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/factor/analyze", params=params, timeout=120.0)
            data = resp.json()

        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== MCP Resources ====================

@mcp.resource("adshare://doc/api")
async def get_api_doc() -> str:
    """Get API documentation."""
    return """# Adshare API Documentation

## Base URL
http://localhost:8000

## Endpoints

### Market Data
- GET /market/kline - K-line data
- GET /market/snapshot - Snapshot data
- GET /market/stock_basic - Stock basic info
- GET /market/code_list - Code list

### Technical Analysis
- GET /technical/analyze - Technical indicators
- GET /technical/indicators - List all indicators

### Fundamental Analysis
- GET /fundamental/analyze - Fundamental factors
- GET /fundamental/factors - List all factors

### Factor Analysis
- GET /factor/analyze - Factor analysis
- POST /factor/composite - Factor composite

### Health
- GET /health - Health check
- GET /health/metrics - Prometheus metrics

## Authentication
All endpoints require API key in header: X-API-Key: your_api_key
"""


@mcp.resource("adshare://doc/indicators")
async def get_indicators_doc() -> str:
    """Get technical indicators documentation."""
    return """# Technical Indicators (57 total)

## Overbought/Oversold (14)
KDJ, RSI, WR, CCI, ROC, MTM, BIAS, SKDJ, MFI, OSC, UDL, ACCER, RCCD, MARSI

## Trend (14)
MACD, DMI, DMA, TRIX, ARBR, EMV, DPO, VHF, CHO, DBCD, DDI, JS, QACD, UOS

## Energy (5)
CR, PSY, MASS, PCNT, WAD

## Volume (9)
OBV, VR, VOLMA, WVAD, VOSC, VRSI, VSTD, AMO, TAPI

## MA (4)
MA, EXPMA, BBI, AMV

## Path (6)
BOLL, ENE, MIKE, PBX, XS, BBIBOLL

## Other (4)
ASI, ATR, SAR, CDP
"""


# ==================== Entry Point ====================

def main():
    """Run MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
