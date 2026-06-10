"""Stub adapter for API-only mode.

The real AmazingData SDK adapter lives in ``amazingdata_worker/adapters/amazingdata.py``.
This stub is used by the adshare-api service which does not install the SDK.
"""

from typing import Any, Dict, List, Optional

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger

logger = get_logger(__name__)


class AmazingDataAdapter:
    """Stub adapter for API-only mode (no AmazingData SDK installed)."""

    _instance: Optional["AmazingDataAdapter"] = None

    def __new__(cls, settings: Optional[Settings] = None) -> "AmazingDataAdapter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, settings: Optional[Settings] = None) -> None:
        if self._initialized:
            return
        self.settings = settings or get_settings()
        self._initialized = True

    @property
    def is_logged_in(self) -> bool:
        return False

    @property
    def login_info(self) -> Optional[Dict[str, Any]]:
        return None

    def login(self) -> bool:
        logger.warning("AmazingData SDK not installed in API-only mode")
        return False

    def ensure_login(self) -> bool:
        return False

    def logout(self) -> None:
        pass

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> List[str]:
        return []

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        return pd.DataFrame()

    def get_calendar(self, market: str = "SH", date: Optional[int] = None) -> pd.DataFrame:
        return pd.DataFrame()

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_stock_basic(
        self, codes: Optional[str] = None, summary_only: bool = False
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_financial(
        self,
        codes: str,
        statement_type: str = "balance",
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_shareholder(
        self,
        codes: str,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_index_component(self, index_code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_industry_list(self, industry_type: str = "sw") -> pd.DataFrame:
        return pd.DataFrame()

    def get_industry_component(self, industry_code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def health(self) -> Dict[str, Any]:
        return {"sdk_installed": False, "logged_in": False, "login_info": None}


# Singleton accessor
_adapter: Optional[AmazingDataAdapter] = None


def get_adapter() -> AmazingDataAdapter:
    """Get singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = AmazingDataAdapter()
    return _adapter
