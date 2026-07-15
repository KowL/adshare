"""Tushare Pro compatible adapter for adshare.

This file can be copied as-is into any external project. Once it is placed
somewhere ahead of the real ``tushare`` package on ``sys.path``,
``import tushare as ts`` will use this adapter and route all data requests to
an adshare server instead of tushare's official API.

Usage::

    import tushare as ts

    ts.set_token("adshare-api-key")                 # optional
    pro = ts.pro_api("http://localhost:8000/tushare")

    df = pro.daily(ts_code="000001.SZ", start_date="20240101", end_date="20240131")

The adapter supports the standard tushare Pro request shape::

    POST {base_url}
    Body: {"api_name": "daily", "token": "...", "params": {...}, "fields": ""}

Only the API endpoints exposed by the target adshare server are available.

Dependencies
------------
- pandas (any recent version)
- One of: httpx, requests, or the Python standard library (urllib). The best
  available transport is selected automatically.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------

_httpx: Any = None
try:
    import httpx as _httpx  # type: ignore

    _HAS_HTTPX = True
except Exception:  # pragma: no cover
    _HAS_HTTPX = False

try:
    import requests  # type: ignore

    _HAS_REQUESTS = True
except Exception:  # pragma: no cover
    _HAS_REQUESTS = False


class _HttpTransport:
    """Minimal HTTP transport that tries httpx, requests, then urllib."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def post(self, url: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        if _HAS_HTTPX:
            return self._post_httpx(url, body, headers)
        if _HAS_REQUESTS:
            return self._post_requests(url, body, headers)
        return self._post_urllib(url, body, headers)

    def _post_httpx(
        self, url: str, body: bytes, headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            resp = _httpx.post(url, content=body, headers=headers, timeout=self.timeout)
        except Exception as exc:  # pragma: no cover
            raise TushareApiError(f"Request to {url} failed: {exc}") from exc
        return self._handle_response(resp.status_code, resp.text)

    def _post_requests(
        self, url: str, body: bytes, headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=self.timeout)
        except Exception as exc:  # pragma: no cover
            raise TushareApiError(f"Request to {url} failed: {exc}") from exc
        return self._handle_response(resp.status_code, resp.text)

    def _post_urllib(
        self, url: str, body: bytes, headers: dict[str, str]
    ) -> dict[str, Any]:
        from urllib import request

        req = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return self._handle_response(resp.status, text)
        except Exception as exc:  # pragma: no cover
            raise TushareApiError(f"Request to {url} failed: {exc}") from exc

    def _handle_response(self, status_code: int, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TushareApiError(
                f"Invalid JSON response (status {status_code}): {text[:200]}",
                status_code=status_code,
            ) from exc

        if status_code == 401:
            raise TushareAuthError("Authentication failed: invalid or missing API key")
        if status_code == 403:
            raise TushareAuthError("Authorization failed: insufficient permissions")
        if status_code != 200 or data.get("code") != 0:
            msg = data.get("msg", "unknown error") if isinstance(data, dict) else str(data)
            api_code = data.get("code") if isinstance(data, dict) else None
            raise TushareApiError(msg, status_code=status_code, api_code=api_code)

        return data


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TushareClientError(Exception):
    """Base exception for adapter errors."""


class TushareApiError(TushareClientError):
    """Raised when the adshare server returns an error."""

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
    """Raised when authentication or authorization fails."""


# ---------------------------------------------------------------------------
# Global state (tushare compatibility)
# ---------------------------------------------------------------------------

_default_token: str = ""


def set_token(token: str) -> None:
    """Set the default API token (adshare API key)."""
    global _default_token
    _default_token = token


def get_token() -> str:
    """Return the currently configured default token."""
    return _default_token


# ---------------------------------------------------------------------------
# Pro API object
# ---------------------------------------------------------------------------


class TushareProApi:
    """Drop-in replacement for the object returned by tushare.pro_api()."""

    DEFAULT_BASE_URL = "http://localhost:8000/tushare"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
        http_client: Optional[Any] = None,
    ) -> None:
        self.base_url = (base_url or self._default_base_url()).rstrip("/")
        self.token = token or _default_token or os.getenv("TUSHARE_API_TOKEN", "")
        self._http_client = http_client
        self._transport = None if http_client else _HttpTransport(timeout=timeout)

    @classmethod
    def _default_base_url(cls) -> str:
        return os.getenv("TUSHARE_API_URL", cls.DEFAULT_BASE_URL)

    def _send_with_http_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request using an injected httpx/requests client."""
        client = self._http_client
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        # httpx.Client / requests.Session
        if _HAS_HTTPX and isinstance(client, _httpx.Client):
            response = client.post(self.base_url, content=body, headers=headers)
        else:
            response = client.post(self.base_url, data=body, headers=headers)
        if response.status_code == 401:
            raise TushareAuthError("Authentication failed: invalid or missing API key")
        if response.status_code == 403:
            raise TushareAuthError("Authorization failed: insufficient permissions")

        data = response.json()
        if response.status_code != 200 or data.get("code") != 0:
            msg = data.get("msg", "unknown error") if isinstance(data, dict) else str(data)
            api_code = data.get("code") if isinstance(data, dict) else None
            raise TushareApiError(msg, status_code=response.status_code, api_code=api_code)
        return data

    def query(self, api_name: str, **params: Any) -> pd.DataFrame:
        """Generic query interface matching ``ts.pro_api().query(...)``."""
        fields = params.pop("fields", "")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields,
        }
        if self._http_client is not None:
            data = self._send_with_http_client(payload)
        else:
            data = self._transport.post(self.base_url, payload)
        return _response_to_df(data)

    def __getattr__(self, api_name: str):
        """Allow ``pro.daily(...)`` style calls for any api name."""
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def method(**params: Any) -> pd.DataFrame:
            return self.query(api_name, **params)

        method.__name__ = api_name
        return method


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def pro_api(
    token_or_url: Optional[str] = None,
    url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = 30.0,
    http_client: Optional[Any] = None,
) -> TushareProApi:
    """Create a Pro API instance pointing at adshare.

    The signature is intentionally compatible with ``tushare.pro_api``. The
    first positional argument is treated as a URL if it starts with ``http://``
    or ``https://``, otherwise it is treated as an adshare API token.

    Examples::

        pro = ts.pro_api("http://localhost:8000/tushare")
        pro = ts.pro_api(token="adshare-api-key")
        pro = ts.pro_api("adshare-api-key", url="http://localhost:8000/tushare")
    """
    resolved_url = url
    resolved_token = token

    if token_or_url is not None:
        if token_or_url.startswith(("http://", "https://")):
            resolved_url = token_or_url
        else:
            resolved_token = token_or_url

    return TushareProApi(
        base_url=resolved_url,
        token=resolved_token,
        timeout=timeout,
        http_client=http_client,
    )


def query(api_name: str, **params: Any) -> pd.DataFrame:
    """Module-level query using the default token and base URL."""
    return pro_api().query(api_name, **params)


# ---------------------------------------------------------------------------
# DataFrame conversion
# ---------------------------------------------------------------------------


def _response_to_df(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert a tushare Pro response to a pandas DataFrame."""
    inner = payload.get("data") or {}
    if not inner:
        return pd.DataFrame()

    fields = inner.get("fields") or []
    items = inner.get("items") or []
    if not fields or not items:
        return pd.DataFrame(columns=fields)

    df = pd.DataFrame(items, columns=fields)
    return _normalize_df_types(df)


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


# ---------------------------------------------------------------------------
# Optional: delegate to the real tushare library for unsupported attributes.
# ---------------------------------------------------------------------------

# If the real ``tushare`` package is installed and this module is being used
# as a shim, forward unknown top-level attributes to the real package so that
# users can still access news, macro, etc. that adshare does not provide.
_real_tushare: Any = None


def _load_real_tushare() -> Any:
    """Import the real tushare package while avoiding self-import."""
    global _real_tushare
    if _real_tushare is not None:
        return _real_tushare

    # Temporarily hide this module so ``import tushare`` resolves to the real
    # package installed in site-packages.
    saved = sys.modules.pop("tushare", None)
    try:
        import importlib

        _real_tushare = importlib.import_module("tushare")
    except Exception:  # pragma: no cover
        _real_tushare = None
    finally:
        if saved is not None:
            sys.modules["tushare"] = saved
    return _real_tushare


def __getattr__(name: str) -> Any:
    """Forward unknown attributes to the real tushare package if available."""
    if name.startswith("_"):
        raise AttributeError(name)
    real = _load_real_tushare()
    if real is not None and hasattr(real, name):
        return getattr(real, name)
    raise AttributeError(f"module 'tushare' has no attribute '{name}'")
