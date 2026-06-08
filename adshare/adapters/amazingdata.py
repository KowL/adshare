"""AmazingData SDK adapter for adshare.

This module wraps the AmazingData SDK (Linux/amd64 only) and provides
a unified interface for all data queries with connection pooling and retry logic.
"""

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from adshare.core.cache import get_cache_manager
from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)


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
        self._cache = get_cache_manager()

    # ============================================================
    # Connection Management
    # ============================================================

    def _get_client(self) -> Any:
        """Get or create AmazingData client."""
        if self._client is None:
            try:
                import AmazingData as ad

                self._client = ad
                # Initialize InfoData first (no push server needed)
                self._info_data = ad.query_api.info_data.InfoData()
                logger.info("AmazingData SDK loaded successfully")
            except ImportError:
                logger.error(
                    "AmazingData SDK not found. "
                    "Please install: pip install /path/to/amazingdata-*.whl"
                )
                raise RuntimeError("AmazingData SDK not installed")
        return self._client

    def _ensure_base_data(self):
        """Lazy initialize BaseData and MarketData (may block)."""
        if self._base_data is None:
            import AmazingData as ad
            self._base_data = ad.BaseData()
            try:
                calendar = self._base_data.get_calendar()
                if calendar is None:
                    logger.warning("BaseData.get_calendar() returned None, using empty calendar")
                    calendar = []
                self._market_data = ad.query_api.market_data.MarketData(calendar=calendar)
            except Exception as e:
                logger.warning(f"BaseData/MarketData initialization failed: {e}, retrying with empty calendar")
                # Fallback: try with empty calendar
                try:
                    self._market_data = ad.query_api.market_data.MarketData(calendar=[])
                except Exception as e2:
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
                # login returns bool, store a simple dict instead
                self._login_info = {"status": result, "timestamp": time.time()}
                if result:
                    logger.info(
                        f"AmazingData login successful: "
                        f"{self.settings.amazingdata_connection_string}"
                    )
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
        cache_key = ("code_list", security_type)
        cached = self._cache.get("code_list", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            self._ensure_base_data()
            return list(self._base_data.get_code_list("EXTRA_STOCK_A_SH_SZ"))

        result = self._with_retry(_fetch)
        self._cache.set("code_list", result, *cache_key)
        return result

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        """Get security code information."""
        cache_key = ("code_info", security_type)
        cached = self._cache.get("code_info", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            self._ensure_base_data()
            return self._base_data.get_code_info(security_type=security_type)

        result = self._with_retry(_fetch)
        self._cache.set("code_info", result, *cache_key)
        return result

    def get_calendar(
        self, market: str = "SH", date: Optional[int] = None
    ) -> pd.DataFrame:
        """Get trading calendar."""
        cache_key = ("calendar", market, str(date or "all"))
        cached = self._cache.get("calendar", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            self._ensure_base_data()
            # get_calendar returns List[int] per SDK manual §3.5.2.8
            calendar_list = self._base_data.get_calendar(market=market)
            if isinstance(calendar_list, pd.DataFrame):
                return calendar_list
            if isinstance(calendar_list, list):
                return pd.DataFrame({"date": calendar_list})
            return pd.DataFrame({"date": []})

        result = self._with_retry(_fetch)
        # Filter by specific date if requested
        if date is not None and "date" in result.columns:
            result = result[result["date"] == date]
        self._cache.set("calendar", result, *cache_key)
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
        cache_key = ("kline", codes, str(begin_date), str(end_date), period, str(limit), str(offset))
        cached = self._cache.get_unified("kline", *cache_key)
        if cached is not None:
            return cached

        period_map = {
            "tick": 0, "min1": 10000, "min3": 10001, "min5": 10002,
            "min10": 10003, "min15": 10004, "min30": 10005, "min60": 10006,
            "min120": 10007, "day": 10008,
            "week": 10009, "month": 10010,
        }
        period_code = period_map.get(period, 10000)

        def _fetch():
            self._get_client()
            self._ensure_base_data()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
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

        result = self._with_retry(_fetch)
        self._cache.set_unified("kline", result, *cache_key)
        return result

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get snapshot data."""
        cache_key = ("snapshot", codes, str(date or "latest"), str(time or "latest"))
        cached = self._cache.get_unified("snapshot", *cache_key)
        if cached is not None:
            return cached

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

        result = self._with_retry(_fetch)
        self._cache.set_unified("snapshot", result, *cache_key)
        return result

    def get_stock_basic(
        self, codes: Optional[str] = None, summary_only: bool = False
    ) -> pd.DataFrame:
        """Get stock basic information."""
        cache_key = ("stock_basic", codes or "all", str(summary_only))
        cached = self._cache.get("stock_basic", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            if codes:
                code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            else:
                self._ensure_base_data()
                code_list = list(self._base_data.get_code_list("EXTRA_STOCK_A_SH_SZ"))
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

        result = self._with_retry(_fetch)
        self._cache.set("stock_basic", result, *cache_key)
        return result

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
            begin_date: Start date YYYYMMDD (optional)
            end_date: End date YYYYMMDD (optional)
        """
        cache_key = (
            "financial",
            codes,
            statement_type,
            str(begin_date or "all"),
            str(end_date or "all"),
        )
        cached = self._cache.get_unified("financial", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            
            # Map statement_type to InfoData method
            statement_map = {
                "balance": self._info_data.get_balance_sheet,
                "income": self._info_data.get_income,
                "cashflow": self._info_data.get_cash_flow,
            }
            
            if statement_type not in statement_map:
                raise ValueError(f"Invalid statement_type: {statement_type}. Must be one of: balance, income, cashflow")
            
            method = statement_map[statement_type]
            df = method(code_list=code_list)
            return df

        result = self._with_retry(_fetch)
        self._cache.set_unified("financial", result, *cache_key)
        return result

    def get_shareholder(
        self,
        codes: str,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Get shareholder data."""
        cache_key = ("shareholder", codes, str(begin_date or "all"), str(end_date or "all"))
        cached = self._cache.get_unified("shareholder", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            self._get_client()
            code_list = [c.strip() for c in codes.split(",")] if "," in codes else [codes]
            df = self._info_data.get_share_holder(code_list=code_list)
            return df

        result = self._with_retry(_fetch)
        self._cache.set_unified("shareholder", result, *cache_key)
        return result

    def get_index_component(self, index_code: str) -> pd.DataFrame:
        """Get index component stocks."""
        cache_key = ("index_component", index_code)
        cached = self._cache.get("index_component", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            client = self._get_client()
            return client.get_index_component(index_code=index_code)

        result = self._with_retry(_fetch)
        self._cache.set("index_component", result, *cache_key)
        return result

    def get_industry_list(self, industry_type: str = "sw") -> pd.DataFrame:
        """Get industry classification list."""
        cache_key = ("industry_list", industry_type)
        cached = self._cache.get("industry_list", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            client = self._get_client()
            return client.get_industry_list(industry_type=industry_type)

        result = self._with_retry(_fetch)
        self._cache.set("industry_list", result, *cache_key)
        return result

    def get_industry_component(self, industry_code: str) -> pd.DataFrame:
        """Get industry component stocks."""
        cache_key = ("industry_component", industry_code)
        cached = self._cache.get("industry_component", *cache_key)
        if cached is not None:
            return cached

        def _fetch():
            client = self._get_client()
            return client.get_industry_component(industry_code=industry_code)

        result = self._with_retry(_fetch)
        self._cache.set("industry_component", result, *cache_key)
        return result

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
