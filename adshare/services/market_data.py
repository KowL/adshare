"""Application service for market data access.

This module owns the query orchestration between the L3 historical
warehouse and the AmazingData adapter. Routers should stay thin: they
translate HTTP parameters and map service results to response models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from adshare.adapters.amazingdata import get_adapter
from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import standardize_codes_df
from adshare.historical.models import normalize_period
from adshare.historical.warehouse import get_warehouse

logger = get_logger(__name__)


@dataclass(frozen=True)
class KlineQueryResult:
    """Result of a K-line query and the source that served it."""

    df: pd.DataFrame
    source: str
    synced: bool


class MarketDataService:
    """Coordinate market data reads across warehouse and SDK-backed adapter."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        adapter=None,
        warehouse=None,
    ) -> None:
        self.settings = settings or get_settings()
        self.adapter = adapter
        self.warehouse = warehouse

    def get_kline(
        self,
        codes: str | Sequence[str],
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
        source: str = "auto",
    ) -> KlineQueryResult:
        """Return K-line data using the configured source policy.

        ``source`` accepts:

        - ``auto``: use L3 warehouse when fully synced, otherwise SDK adapter
        - ``warehouse``: use L3 warehouse only
        - ``sdk``: use SDK adapter only
        """
        code_list = _normalize_codes(codes)
        if not code_list:
            return KlineQueryResult(pd.DataFrame(), "none", False)

        source = (source or "auto").lower()
        if source not in {"auto", "warehouse", "sdk"}:
            raise ValueError("source must be one of: auto, warehouse, sdk")

        df = pd.DataFrame()
        synced = False
        used_source = "warehouse"

        if source in {"auto", "warehouse"} and self._warehouse_eligible(period):
            warehouse = self._get_warehouse()
            if warehouse is not None:
                try:
                    synced = warehouse.is_synced(begin_date, end_date, period, code_list)
                    if synced:
                        df = warehouse.query_kline(
                            code_list,
                            begin_date,
                            end_date,
                            period,
                            limit=limit,
                            offset=offset,
                        )
                except Exception as e:  # noqa: BLE001
                    logger.debug("L3 warehouse lookup failed, falling back: %s", e)
                    df = pd.DataFrame()
                    synced = False

        if (df is None or df.empty) and source in {"auto", "sdk"}:
            adapter = self._get_adapter()
            sdk_period = _sdk_period(period)
            df = adapter.get_kline(
                codes=",".join(code_list),
                begin_date=begin_date,
                end_date=end_date,
                period=sdk_period,
                limit=limit,
                offset=offset,
            )
            used_source = "sdk"

        return KlineQueryResult(
            df=df if df is not None else pd.DataFrame(),
            source=used_source,
            synced=synced,
        )

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> list[str]:
        """Return the market code list via the current data source policy."""
        warehouse = self._get_warehouse()
        if warehouse is not None:
            try:
                df = warehouse.query_codes(is_listed=True)
                if isinstance(df, pd.DataFrame) and not df.empty and "code" in df.columns:
                    return [str(code) for code in df["code"].tolist()]
            except Exception as e:  # noqa: BLE001
                logger.warning("Local code metadata lookup failed: %s", e)

        adapter = self._get_adapter()
        try:
            raw = adapter.get_code_info(security_type=security_type)
            std = standardize_codes_df(raw)
            if not std.empty and "code" in std.columns:
                if warehouse is not None:
                    path = warehouse.meta_dir() / "codes.parquet"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    std.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
                    warehouse.refresh_views()
                return [str(code) for code in std["code"].tolist()]
        except Exception as e:  # noqa: BLE001
            logger.warning("AmazingData code metadata lookup failed: %s", e)

        return list(adapter.get_code_list(security_type=security_type))

    def get_calendar(self, market: str = "SH", date: Optional[int] = None) -> pd.DataFrame:
        """Return the trading calendar via the current data source policy."""
        return self._get_adapter().get_calendar(market=market, date=date)

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return snapshot data via the current data source policy."""
        adapter = self._get_adapter()
        if not adapter.is_logged_in:
            logger.warning("AmazingData not logged in, returning empty snapshot data")
            return pd.DataFrame()
        return adapter.get_snapshot(codes=codes, date=date, time=time)

    def get_stock_basic(
        self,
        codes: Optional[str] = None,
        summary_only: bool = False,
    ) -> pd.DataFrame:
        """Return stock basic information via the current data source policy."""
        return self._get_adapter().get_stock_basic(codes=codes, summary_only=summary_only)

    def _warehouse_eligible(self, period: str) -> bool:
        if not self.settings.historical_enabled:
            return False
        try:
            normalize_period(period)
            return True
        except ValueError:
            return False

    def _get_adapter(self):
        if self.adapter is None:
            self.adapter = get_adapter()
        return self.adapter

    def _get_warehouse(self):
        if not self.settings.historical_enabled:
            return None
        if self.warehouse is None:
            self.warehouse = get_warehouse(self.settings)
        return self.warehouse


def get_market_data_service() -> MarketDataService:
    """Create a market data service for the current settings."""
    return MarketDataService()


def _normalize_codes(codes: str | Sequence[str]) -> list[str]:
    if isinstance(codes, str):
        return [c.strip() for c in codes.split(",") if c.strip()]
    return [str(c).strip() for c in codes if str(c).strip()]


def _sdk_period(period: str) -> str:
    """Map public period aliases to adapter period values."""
    try:
        subdir = normalize_period(period)
    except ValueError:
        return period
    return {"daily": "day", "weekly": "week", "monthly": "month"}[subdir]
