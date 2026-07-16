"""Application service for market data access.

Reads from the L3 historical warehouse (Parquet/DuckDB) only.
SDK fallback has been removed — data is populated by amazingdata_worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from adshare.core.config import Settings, get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import normalize_period
from adshare.historical.warehouse import get_warehouse

logger = get_logger(__name__)


@dataclass(frozen=True)
class KlineQueryResult:
    """Result of a K-line query."""

    df: pd.DataFrame
    source: str
    synced: bool


class MarketDataService:
    """Read market data from the L3 historical warehouse."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        warehouse=None,
    ) -> None:
        self.settings = settings or get_settings()
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
        """Return K-line data from the L3 warehouse."""
        code_list = _normalize_codes(codes)
        if not code_list:
            return KlineQueryResult(pd.DataFrame(), "none", False)

        df = pd.DataFrame()
        synced = False

        if self._warehouse_eligible(period):
            warehouse = self._get_warehouse()
            if warehouse is not None:
                try:
                    # Partial-hit: query whatever files exist, skip missing ones.
                    df = warehouse.query_kline(
                        code_list,
                        begin_date,
                        end_date,
                        period,
                        limit=limit,
                        offset=offset,
                    )
                    synced = warehouse.is_synced(begin_date, end_date, period, code_list)
                except Exception as e:  # noqa: BLE001
                    logger.debug("L3 warehouse lookup failed: %s", e)
                    df = pd.DataFrame()
                    synced = False

        return KlineQueryResult(
            df=df if df is not None else pd.DataFrame(),
            source="warehouse",
            synced=synced,
        )

    def get_code_list(self, security_type: str = "stock_a") -> list[str]:
        """Return the market code list from the L3 warehouse.

        ``security_type`` is accepted for API compatibility but currently
        does not filter — the warehouse stores the SH/SZ A-share universe.
        """
        warehouse = self._get_warehouse()
        if warehouse is not None:
            try:
                df = warehouse.query_codes(is_listed=True)
                if isinstance(df, pd.DataFrame) and not df.empty and "code" in df.columns:
                    return [str(code) for code in df["code"].tolist()]
            except Exception as e:  # noqa: BLE001
                logger.warning("Local code metadata lookup failed: %s", e)
        return []

    def get_calendar(self, market: str = "SH", date: Optional[int] = None) -> pd.DataFrame:
        """Return the trading calendar from the L3 warehouse."""
        warehouse = self._get_warehouse()
        if warehouse is not None:
            try:
                df = warehouse.query_calendar(market=market)
                if isinstance(df, pd.DataFrame) and not df.empty and "date" in df.columns:
                    if date is not None:
                        df = df[df["date"] == date]
                    return df
            except Exception as e:  # noqa: BLE001
                logger.warning("Local calendar lookup failed: %s", e)
        return pd.DataFrame()

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return snapshot data.

        Note: Snapshot is not stored in the L3 warehouse.
        This endpoint returns empty data in API-only mode.
        """
        logger.warning("Snapshot not available in API-only mode")
        return pd.DataFrame()

    def get_stock_basic(
        self,
        codes: Optional[str] = None,
        summary_only: bool = False,
    ) -> pd.DataFrame:
        """Return stock basic information from the L3 warehouse."""
        warehouse = self._get_warehouse()
        if warehouse is not None:
            try:
                df = warehouse.query_codes(is_listed=True)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    if codes and "code" in df.columns:
                        code_list = [c.strip() for c in codes.split(",") if c.strip()]
                        df = df[df["code"].isin(code_list)]
                    if summary_only and "code" in df.columns and "name" in df.columns:
                        df = df[["code", "name"]]
                    return df
            except Exception as e:  # noqa: BLE001
                logger.warning("Local stock basic lookup failed: %s", e)
        return pd.DataFrame()

    def _warehouse_eligible(self, period: str) -> bool:
        if not self.settings.historical_enabled:
            return False
        try:
            normalize_period(period)
            return True
        except ValueError:
            return False

    def _get_warehouse(self):
        if not self.settings.historical_enabled:
            return None
        if self.warehouse is None:
            self.warehouse = get_warehouse(self.settings)
        return self.warehouse


def get_market_data_service() -> MarketDataService:
    """Create a market data service for the current settings."""
    settings = get_settings()
    warehouse = get_warehouse(settings) if settings.historical_enabled else None
    return MarketDataService(settings=settings, warehouse=warehouse)


def _normalize_codes(codes: str | Sequence[str]) -> list[str]:
    if isinstance(codes, str):
        return [c.strip() for c in codes.split(",") if c.strip()]
    return [str(c).strip() for c in codes if str(c).strip()]
