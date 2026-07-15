"""Tushare Pro compatible HTTP client for adshare.

This client can be used directly or via the project-root ``tushare.py``
shim. It speaks the tushare Pro request/response protocol:

    POST {base_url}
    Body: {"api_name": "daily", "token": "...", "params": {...}, "fields": ""}

    Response: {"code": 0, "msg": "", "data": {"fields": [...], "items": [...]}}
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import pandas as pd

# adshare uses httpx; if it is missing, raise a helpful error.
try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "adshare.clients.tushare_client requires 'httpx'. "
        "Install it with: pip install httpx"
    ) from exc


class TushareClientError(Exception):
    """Base exception for tushare client errors."""


class TushareApiError(TushareClientError):
    """Raised when adshare returns a non-200 or business error response."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        api_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code


class TushareAuthError(TushareClientError):
    """Raised when authentication fails."""


class TushareClient:
    """Tushare Pro compatible client that sends requests to adshare."""

    DEFAULT_BASE_URL = "http://localhost:8000/tushare"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url or self._default_base_url()).rstrip("/")
        self.api_key = api_key or os.getenv("TUSHARE_API_TOKEN", "")
        self.timeout = timeout
        self._http_client = http_client

    @classmethod
    def _default_base_url(cls) -> str:
        return os.getenv("TUSHARE_API_URL", cls.DEFAULT_BASE_URL)

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.timeout)
        return self._http_client

    def close(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> "TushareClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def query(self, api_name: str, **params: Any) -> pd.DataFrame:
        """Call an arbitrary tushare API by name and return a DataFrame."""
        payload: dict[str, Any] = {
            "api_name": api_name,
            "token": self.api_key,
            "params": params,
            "fields": params.pop("fields", ""),
        }
        try:
            response = self._client().post(
                self.base_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except httpx.RequestError as exc:
            raise TushareApiError(f"Request to {self.base_url} failed: {exc}") from exc

        if response.status_code == 401:
            raise TushareAuthError("Authentication failed: invalid or missing API key")
        if response.status_code == 403:
            raise TushareAuthError("Authorization failed: insufficient permissions")

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise TushareApiError(
                f"Invalid JSON response (status {response.status_code})",
                status_code=response.status_code,
            ) from exc

        if response.status_code != 200 or data.get("code") != 0:
            msg = data.get("msg", "unknown error") if isinstance(data, dict) else str(data)
            api_code = data.get("code") if isinstance(data, dict) else None
            raise TushareApiError(
                msg,
                status_code=response.status_code,
                api_code=api_code,
            )

        return self._response_to_df(data)

    def _response_to_df(self, payload: dict[str, Any]) -> pd.DataFrame:
        """Convert a tushare Pro response payload to a pandas DataFrame."""
        inner = payload.get("data") or {}
        if not inner:
            return pd.DataFrame()

        fields = inner.get("fields") or []
        items = inner.get("items") or []
        if not fields or not items:
            return pd.DataFrame(columns=fields)

        df = pd.DataFrame(items, columns=fields)
        return _normalize_df_types(df)

    def __getattr__(self, api_name: str):
        """Allow ``client.daily(...)`` style calls for any api name."""
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def method(**params: Any) -> pd.DataFrame:
            return self.query(api_name, **params)

        method.__name__ = api_name
        return method


def _normalize_df_types(df: pd.DataFrame) -> pd.DataFrame:
    """Apply sensible dtypes to common tushare fields."""
    if df.empty:
        return df

    int_date_fields = {
        "trade_date",
        "cal_date",
        "list_date",
        "delist_date",
        "ipo_date",
        "issue_date",
        "suspend_date",
        "resume_date",
        "ann_date",
    }
    float_price_fields = {
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
        "adj_factor",
    }
    string_fields = {"ts_code", "name", "exchange", "market", "industry"}

    for col in df.columns:
        if col in int_date_fields:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif col in float_price_fields:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
        elif col in string_fields:
            df[col] = df[col].astype(str).replace("nan", "")

    return df
