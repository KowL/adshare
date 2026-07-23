"""Data-source contract for the AmazingData subsystem.

Every market-data source (currently AmazingData; future vendors) must
satisfy the :class:`DataSourceAdapter` protocol. Sync jobs
(:mod:`amazingdata.batch`) and the realtime publisher
(:mod:`amazingdata.realtime`) depend only on this
protocol plus plain ``pandas.DataFrame`` results — never on a concrete
SDK class — so swapping the source means writing one new adapter that
satisfies this protocol.

Column-name conventions of the returned DataFrames are part of the
contract: a new adapter must normalise its output to the shapes the
warehouse standardisers and downstream engines already consume (e.g.
``kline_time`` for raw K-line rows, canonical statement columns for
financial data).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol

import pandas as pd

# Callback invoked by a :class:`SubscriptionSource` for each realtime tick.
# Arguments are the raw tick payload (source-specific object or dict) and
# the source-specific integer period constant it was registered with.
RealtimeCallback = Callable[[Any, int], None]


class SubscriptionSource(Protocol):
    """Push-based realtime subscription handle (worker-side).

    Mirrors the decorator-style registration used by vendor SDKs:
    ``register`` returns a decorator that binds a callback to a code
    list and period; ``run`` dispatches ticks until stopped.
    """

    def register(
        self, code_list: List[str], period: int
    ) -> Callable[[RealtimeCallback], RealtimeCallback]:
        """Return a decorator registering a callback for codes at ``period``."""
        ...

    def run(self) -> None:
        """Blocking dispatch loop; returns when stopped or on error."""
        ...

    def stop(self) -> None:
        """Interrupt :meth:`run` (best-effort)."""
        ...


class DataSourceAdapter(Protocol):
    """Contract every market-data source adapter must satisfy."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Open the session; return True on success."""
        ...

    def logout(self) -> None:
        """Close the session (idempotent)."""
        ...

    def ensure_login(self) -> bool:
        """Login if needed; return whether a session is active."""
        ...

    @property
    def is_logged_in(self) -> bool:
        """Whether a session is currently active."""
        ...

    @property
    def login_info(self) -> Optional[Dict[str, Any]]:
        """Opaque session metadata, or None when logged out."""
        ...

    def health(self) -> Dict[str, Any]:
        """Return a health snapshot for diagnostics."""
        ...

    # ------------------------------------------------------------------
    # Meta / reference data
    # ------------------------------------------------------------------

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> List[str]:
        """Return security codes (with .SH/.SZ suffixes) for a type."""
        ...

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        """Return code metadata (code/name/list_date/...)."""
        ...

    def get_calendar(
        self, market: str = "SH", date: Optional[int] = None
    ) -> pd.DataFrame:
        """Return the trading calendar as a DataFrame with a ``date`` column."""
        ...

    def get_adjustment_factors(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        local_path: str,
        refresh: bool = True,
    ) -> pd.DataFrame:
        """Return cumulative adjustment factors in canonical long form."""
        ...

    def get_stock_basic(
        self, codes: Optional[str] = None, summary_only: bool = False
    ) -> pd.DataFrame:
        """Return stock basic info with snake_case columns (code/name/...)."""
        ...

    def get_financial(
        self,
        codes: str,
        statement_type: str = "balance",
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return financial statement rows (balance/income/cashflow)."""
        ...

    def get_shareholder(
        self,
        codes: str,
        begin_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return shareholder-number rows."""
        ...

    def get_index_component(self, index_code: str) -> pd.DataFrame:
        """Return index constituent rows for one or more index codes."""
        ...

    def get_industry_list(self, industry_type: str = "sw") -> pd.DataFrame:
        """Return the industry classification list."""
        ...

    def get_industry_component(self, industry_code: str) -> pd.DataFrame:
        """Return industry constituent rows."""
        ...

    # ------------------------------------------------------------------
    # Market data (pull)
    # ------------------------------------------------------------------

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        """Return K-line rows for comma-separated ``codes`` (YYYYMMDD range)."""
        ...

    def get_snapshot(
        self,
        codes: str,
        date: Optional[int] = None,
        time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return latest snapshot rows for comma-separated ``codes``."""
        ...

    # ------------------------------------------------------------------
    # Realtime (push)
    # ------------------------------------------------------------------

    def period_value(self, period: str) -> int:
        """Map a logical period name to the source's integer constant.

        Accepts ``"snapshot"`` plus the K-line periods (``"min1"`` ...
        ``"month"``). Raises ``ValueError`` for unknown names.
        """
        ...

    def create_subscription_source(self) -> SubscriptionSource:
        """Create a realtime subscription handle. Requires an active session."""
        ...
