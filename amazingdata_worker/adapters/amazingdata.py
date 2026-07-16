"""AmazingData SDK adapter for adshare.

This module wraps the AmazingData SDK (Linux/amd64 only) and provides
a unified interface for all data queries with connection pooling and retry logic.
"""

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)

# Period map: logical name -> AmazingData SDK constant value.
# Shared by ``get_kline`` and the realtime ``period_value`` contract.
_KLINE_PERIOD_MAP = {
    "tick": 0,
    "min1": 10000,
    "min3": 10001,
    "min5": 10002,
    "min10": 10003,
    "min15": 10004,
    "min30": 10005,
    "min60": 10006,
    "min120": 10007,
    "day": 10008,
    "week": 10009,
    "month": 10010,
}


class AmazingDataAdapter:
    """Adapter for AmazingData SDK with connection pooling and caching."""

    _instance: Optional["AmazingDataAdapter"] = None
    _lock = threading.Lock()

    def __new__(cls, settings: Optional[Settings] = None) -> "AmazingDataAdapter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, settings: Optional[Settings] = None) -> None:
        if self._initialized:
            return
        self.settings = settings or get_settings()
        self._client: Optional[Any] = None
        self._base_data: Optional[Any] = None
        self._info_data: Optional[Any] = None
        self._market_data: Optional[Any] = None
        self._login_info: Optional[Dict[str, Any]] = None
        self._lock = threading.RLock()
        self._initialized = True

    # ============================================================
    # Connection Management
    # ============================================================

    def _get_client(self) -> Any:
        """Get or create AmazingData module reference.

        This method only loads the SDK module; it does **not** create any
        data interface objects such as ``InfoData`` or ``BaseData``.  Those
        are created lazily by ``_ensure_info_data()`` / ``_ensure_base_data()``
        so that accounts with a single TGW connection do not pay for objects
        they never use.
        """
        if self._client is None:
            try:
                import AmazingData as ad

                self._client = ad
                logger.info("AmazingData SDK loaded successfully")
            except ImportError:
                logger.error(
                    "AmazingData SDK not found. "
                    "Please install: pip install /path/to/amazingdata-*.whl"
                )
                raise RuntimeError("AmazingData SDK not installed")
        return self._client

    def _ensure_info_data(self) -> None:
        """Lazy initialize InfoData."""
        if self._info_data is None:
            import AmazingData as ad

            self._info_data = ad.query_api.info_data.InfoData()

    def _ensure_base_data(self):
        """Lazy initialize BaseData and MarketData (may block)."""
        if self._base_data is not None and self._market_data is not None:
            return
        import AmazingData as ad
        try:
            if self._base_data is None:
                self._base_data = ad.BaseData()
            calendar = self._base_data.get_calendar()
            logger.info(f"BaseData.get_calendar() returned type={type(calendar).__name__}")
            if calendar is None:
                logger.warning("BaseData.get_calendar() returned None, using empty calendar")
                calendar = []
            self._market_data = ad.query_api.market_data.MarketData(calendar=calendar)
        except Exception as e:
            logger.warning(f"BaseData/MarketData initialization failed: {e}, retrying with empty calendar")
            # Reset BaseData so the next attempt can create a fresh instance.
            self._base_data = None
            self._market_data = None
            try:
                self._base_data = ad.BaseData()
                self._market_data = ad.query_api.market_data.MarketData(calendar=[])
            except Exception as e2:
                self._base_data = None
                self._market_data = None
                logger.error(f"MarketData initialization failed completely: {e2}")
                raise RuntimeError(f"Cannot initialize MarketData: {e2}")


    def login(self) -> bool:
        """Login to AmazingData server."""
        if self._login_info is not None:
            return True
        try:
            client = self._get_client()
            with self._lock:
                result = client.login(
                    username=self.settings.ad_username,
                    password=self.settings.ad_password,
                    host=self.settings.ad_host,
                    port=self.settings.ad_port,
                )
                if result:
                    # Only cache successful logins; failed attempts must be
                    # retryable for accounts with a single TGW connection that
                    # may be temporarily exhausted.
                    self._login_info = {"status": result, "timestamp": time.time()}
                    logger.info(
                        f"AmazingData login successful: "
                        f"{self.settings.amazingdata_connection_string}"
                    )
                else:
                    logger.error("AmazingData login returned false")
                return result
        except Exception as e:
            logger.error(f"AmazingData login failed: {e}")
            return False

    def ensure_login(self) -> bool:
        """Ensure logged in, try login if not."""
        if self._login_info is not None:
            return True
        return self.login()

    def logout(self) -> None:
        """Logout from AmazingData server."""
        with self._lock:
            self._login_info = None
            self._client = None
            logger.info("AmazingData logged out")

    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        return self._login_info is not None

    @property
    def login_info(self) -> Optional[Dict[str, Any]]:
        """Get current login info."""
        return self._login_info

    # ============================================================
    # Retry Decorator
    # ============================================================

    def _with_retry(self, func, *args, **kwargs):
        """Execute function with retry logic."""
        last_exception = None
        for attempt in range(self.settings.ad_max_retries):
            try:
                if not self.ensure_login():
                    raise RuntimeError("Not logged in to AmazingData")
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.settings.ad_max_retries - 1:
                    time.sleep(self.settings.ad_retry_delay * (attempt + 1))
                    # Only re-login on non-connection-limit errors
                    err_str = str(e).lower()
                    if "exceed the max limitation" not in err_str and "status[-98]" not in err_str:
                        self.login()
        raise last_exception

    # ============================================================
    # Data APIs
    # ============================================================

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> List[str]:
        """Get list of security codes."""
        def _fetch():
            self._get_client()
            self._ensure_base_data()
            raw = self._base_data.get_code_list(security_type=security_type)
            if raw is None:
                raise RuntimeError(f"BaseData.get_code_list({security_type!r}) returned None")
            return list(raw)

        return self._with_retry(_fetch)

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        """Get security code information."""
        def _fetch():
            self._get_client()
            self._ensure_base_data()
            return self._base_data.get_code_info(security_type=security_type)

        return self._with_retry(_fetch)

    def get_calendar(
        self, market: str = "SH", date: Optional[int] = None
    ) -> pd.DataFrame:
        """Get trading calendar."""
        def _fetch():
            self._get_client()
            self._ensure_base_data()
            # get_calendar returns List[int] per SDK manual §3.5.2.8
            try:
                calendar_list = self._base_data.get_calendar(market=market)
            except TypeError:
                calendar_list = self._base_data.get_calendar()
            if isinstance(calendar_list, pd.DataFrame):
                return calendar_list
            if isinstance(calendar_list, list):
                return pd.DataFrame({"date": calendar_list})
            return pd.DataFrame({"date": []})

        result = self._with_retry(_fetch)
        # Filter by specific date if requested
        if date is not None and "date" in result.columns:
            result = result[result["date"] == date]
        return result

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Get K-line data."""
        period_code = _KLINE_PERIOD_MAP.get(period, 10000)

        def _ensure_suffix(code: str) -> str:
            """Append .SH/.SZ/.BJ suffix if missing so TGW SDK can route the code."""
            c = code.strip()
            if "." in c:
                return c
            # Heuristic: 6-digit codes
            if len(c) == 6 and c.isdigit():
                if c.startswith(("60", "68", "69")):
                    return f"{c}.SH"
                elif c.startswith(("00", "30", "39")):
                    return f"{c}.SZ"
                elif c.startswith(("8", "4", "9")):
                    return f"{c}.BJ"
            return c

        def _fetch():
            self._get_client()
            self._ensure_base_data()
            code_list = [_ensure_suffix(c) for c in codes.split(",")] if "," in codes else [_ensure_suffix(codes)]
            result_dict = self._market_data.query_kline(
                code_list=code_list,
                begin_date=int(begin_date),
                end_date=int(end_date),
                period=period_code,
            )
            dfs = []
            for code, df in result_dict.items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    df = df.copy()
                    df["code"] = code
                    dfs.append(df)
            if dfs:
                df = pd.concat(dfs, ignore_index=True)
            else:
                df = pd.DataFrame()
            if limit is not None and not df.empty:
                df = df.iloc[offset:offset + limit]
            return df

        return self._with_retry(_fetch)

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get snapshot data."""
        def _fetch():
            self._get_client()
            self._ensure_base_data()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            # Only query today to minimize data
            from datetime import datetime
            today = int(datetime.now().strftime("%Y%m%d"))
            result_dict = self._market_data.query_snapshot(
                code_list=code_list,
                begin_date=today,
                end_date=today,
            )
            dfs = []
            for date_key, code_dict in result_dict.items():
                if isinstance(code_dict, dict):
                    for code, df in code_dict.items():
                        if isinstance(df, pd.DataFrame) and not df.empty:
                            # Only take the latest row
                            df = df.iloc[[-1]].copy()
                            df["code"] = code
                            df["date"] = date_key
                            dfs.append(df)
            if dfs:
                df = pd.concat(dfs, ignore_index=True)
            else:
                df = pd.DataFrame()
            return df

        return self._with_retry(_fetch)

    def get_stock_basic(
        self, codes: Optional[str] = None, summary_only: bool = False
    ) -> pd.DataFrame:
        """Get stock basic information."""
        def _fetch():
            self._get_client()
            if codes:
                code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            else:
                self._ensure_base_data()
                code_list = list(self._base_data.get_code_list("EXTRA_STOCK_A_SH_SZ"))
            self._ensure_info_data()
            df = self._info_data.get_stock_basic(code_list=code_list)
            # Normalize column names to lowercase snake_case
            col_map = {
                "MARKET_CODE": "code",
                "SECURITY_NAME": "name",
                "COMP_NAME": "comp_name",
                "LISTDATE": "list_date",
                "DELISTDATE": "delist_date",
                "LISTPLATE_NAME": "list_plate",
                "IS_LISTED": "is_listed",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            if summary_only and "code" in df.columns and "name" in df.columns:
                df = df[["code", "name"]]
            return df

        return self._with_retry(_fetch)

    def get_financial(
        self,
        codes: str,
        statement_type: str = "balance",
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get financial statement data.

        Args:
            codes: Comma-separated stock codes
            statement_type: "balance", "income", or "cashflow"
            begin_date: Start date YYYYMMDD (optional, default: 19900101)
            end_date: End date YYYYMMDD (optional, default: 20980101)
        """
        def _fetch():
            self._get_client()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]

            # The DownloadInfoData download path has been disabled because it
            # hangs indefinitely in some TGW environments. We use the legacy
            # InfoData query path directly.
            self._ensure_info_data()
            statement_map = {
                "balance": self._info_data.get_balance_sheet,
                "income": self._info_data.get_income,
                "cashflow": self._info_data.get_cash_flow,
            }
            method = statement_map.get(statement_type)
            if method is None:
                raise ValueError(
                    f"Invalid statement_type: {statement_type}. "
                    "Must be one of: balance, income, cashflow"
                )
            raw = method(code_list=code_list)
            # InfoData returns a dict mapping code -> DataFrame.
            frames = []
            if isinstance(raw, dict):
                frames = [df for df in raw.values() if df is not None and not df.empty]
            elif raw is not None and not raw.empty:
                frames = [raw]
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)

        return self._with_retry(_fetch)

    def get_shareholder(
        self,
        codes: str,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get shareholder data."""
        def _fetch():
            self._get_client()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            # Use InfoData directly; DownloadInfoData is disabled because it
            # hangs indefinitely in some TGW environments.
            self._ensure_info_data()
            raw = self._info_data.get_share_holder(code_list=code_list)
            frames = []
            if isinstance(raw, dict):
                frames = [df for df in raw.values() if df is not None and not df.empty]
            elif raw is not None and not raw.empty:
                frames = [raw]
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)

        return self._with_retry(_fetch)

    def get_index_component(self, index_code: str) -> pd.DataFrame:
        """Get index component stocks.

        ``index_code`` may be a single code (e.g. ``000300.SH``) or a
        comma-separated list. Returns a DataFrame with columns
        ``INDEX_CODE``, ``CON_CODE``, ``INDATE``, ``OUTDATE``,
        ``INDEX_NAME``.
        """
        def _fetch():
            self._get_client()
            # Use the module-level index component API directly; the
            # DownloadInfoData path is disabled because it hangs in some TGW
            # environments.
            client = self._get_client()
            return client.get_index_component(index_code=index_code)

        return self._with_retry(_fetch)

    def get_industry_list(self, industry_type: str = "sw") -> pd.DataFrame:
        """Get industry classification list."""
        def _fetch():
            client = self._get_client()
            return client.get_industry_list(industry_type=industry_type)

        return self._with_retry(_fetch)

    def get_industry_component(self, industry_code: str) -> pd.DataFrame:
        """Get industry component stocks."""
        def _fetch():
            client = self._get_client()
            return client.get_industry_component(industry_code=industry_code)

        return self._with_retry(_fetch)

    # ============================================================
    # Realtime (push)
    # ============================================================

    def period_value(self, period: str) -> int:
        """Map a logical period name to the SDK's integer constant.

        Accepts ``"snapshot"`` plus the K-line periods in
        ``_KLINE_PERIOD_MAP``. Raises ``ValueError`` for unknown names.
        """
        if period == "snapshot":
            client = self._get_client()
            return int(client.constant.Period.snapshot.value)
        try:
            return _KLINE_PERIOD_MAP[period]
        except KeyError:
            raise ValueError(f"Unknown period: {period!r}") from None

    def create_subscription_source(self) -> Any:
        """Create a ``SubscribeData`` realtime subscription handle.

        The returned object satisfies
        :class:`~amazingdata_worker.adapters.base.SubscriptionSource`
        (``register`` / ``run`` / ``stop``). Requires an active session.
        """
        client = self._get_client()
        return client.SubscribeData()

    # ============================================================
    # Health Check
    # ============================================================

    def health(self) -> Dict[str, Any]:
        """Check adapter health."""
        return {
            "sdk_installed": self._check_sdk(),
            "logged_in": self.is_logged_in,
            "login_info": self._login_info,
        }

    def _check_sdk(self) -> bool:
        """Check if SDK is installed."""
        try:
            import AmazingData  # noqa: F401
            return True
        except ImportError:
            return False


# Singleton accessor
_adapter: Optional[AmazingDataAdapter] = None


def get_adapter() -> AmazingDataAdapter:
    """Get singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = AmazingDataAdapter()
    return _adapter
