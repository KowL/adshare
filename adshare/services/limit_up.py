"""Application service for limit-up stock calculations from daily K-lines."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from adshare.core.config import get_settings
from adshare.core.logging import get_logger
from adshare.historical.models import (
    CODES_COLUMNS,
    kline_file_path,
    standardize_codes_df,
    standardize_kline_df,
    validate_kline_df,
)
from adshare.historical.warehouse import get_warehouse
from adshare.models.schemas import (
    LimitUpItem,
    LimitUpLadderItem,
    LimitUpLadderLevel,
    LimitUpLadderResponse,
    LimitUpResponse,
    MarketActivityDistribution,
    MarketActivityResponse,
)

logger = get_logger(__name__)

LIMIT_UP_RATES = {
    "主板": Decimal("0.10"),
    "创业板": Decimal("0.20"),
    "科创板": Decimal("0.30"),
    "北交所": Decimal("0.30"),
}


class LimitUpService:
    """Calculate limit-up lists and ladder views from daily K-lines."""

    def __init__(self, adapter=None, warehouse=None, batch_size: int = 200) -> None:
        self.adapter = adapter
        self.warehouse = warehouse
        self.batch_size = batch_size

    def get_limit_up(
        self,
        days: int = 1,
        date: Optional[int] = None,
        board_filter: Optional[str] = None,
        exclude_st: bool = True,
    ) -> LimitUpResponse:
        """Return the current limit-up stocks."""
        target_date = int(date or _today_int())
        date_str = _date_str(target_date)
        df_info = self._get_code_info()
        codes = _codes_from_info(df_info)
        if not codes:
            return LimitUpResponse(date=date_str, stocks=[], count=0)

        name_map = build_name_map(df_info)
        board_map = build_board_map(df_info)
        codes = _filter_codes_by_board(codes, board_map, board_filter)
        if not codes:
            return LimitUpResponse(date=date_str, stocks=[], count=0)
        kline = self._get_daily_kline(codes, target_date)
        stocks = self._calculate_limit_up_stocks(
            kline,
            name_map,
            board_map,
            date_str,
            target_date,
            board_filter,
            exclude_st,
        )

        return LimitUpResponse(date=date_str, stocks=stocks, count=len(stocks), data=stocks)

    def get_ladder(
        self,
        days: int = 15,
        date: Optional[int] = None,
        board_filter: Optional[str] = None,
        exclude_st: bool = True,
    ) -> LimitUpLadderResponse:
        """Return the current limit-up ladder.

        Today this is daily-K-line based, so every hit is classified as first-board.
        ``days`` remains part of the public contract for future consecutive-day
        calculation.
        """
        limit_up = self.get_limit_up(days=1, date=date, board_filter=board_filter, exclude_st=exclude_st)
        stocks = limit_up.stocks
        if not stocks:
            return LimitUpLadderResponse(date=_date_str(int(date or _today_int())), total=0, maxLevel=0, levels=[])

        # Group stocks by limitUpDays. Sort by:
        #   1. limitUpDays DESC (highest first — that's the "top of the ladder")
        #   2. Within the same level, by code ASC (deterministic, stable order)
        # Skip the previous changePct sort — that put 北交所 30%-limit stocks
        # ahead of 4-board 主板 stocks, which doesn't match 同花顺 convention.
        levels_map: dict[int, list] = {}
        for stock in stocks:
            levels_map.setdefault(stock.limitUpDays, []).append(stock)
        for level_stocks in levels_map.values():
            level_stocks.sort(key=lambda s: s.code)
        levels = [
            LimitUpLadderLevel(
                level=level,
                name="首板" if level == 1 else f"{level}连板",
                count=len(level_stocks),
                stocks=[
                    LimitUpLadderItem(
                        code=stock.code,
                        name=stock.name,
                        level=level,
                        industry=stock.industry,
                        firstTime=stock.firstTime,
                        finalTime=stock.finalTime,
                        reason=stock.reason,
                        price=stock.price,
                        changePct=stock.changePct,
                        limitUpDate=stock.limitUpDate,
                    )
                    for stock in level_stocks
                ],
            )
            for level, level_stocks in sorted(levels_map.items(), key=lambda kv: kv[0], reverse=True)
        ]
        max_level = max(levels_map.keys()) if levels_map else 0

        return LimitUpLadderResponse(
            date=_date_str(int(date or _today_int())),
            total=len(stocks),
            maxLevel=max_level,
            levels=levels,
        )

    def _get_code_info(self) -> pd.DataFrame:
        warehouse = self._get_warehouse()
        if warehouse is not None:
            try:
                local = warehouse.query_codes(is_listed=True)
                if isinstance(local, pd.DataFrame) and not local.empty:
                    return local
            except Exception as e:  # noqa: BLE001
                logger.warning("Local code metadata lookup failed: %s", e)
        return pd.DataFrame(columns=list(CODES_COLUMNS))

    def _get_daily_kline(self, codes: Sequence[str], target_date: int) -> pd.DataFrame:
        begin_date = _lookback_begin_date(target_date)
        warehouse = self._get_warehouse()
        local = pd.DataFrame()
        if warehouse is not None:
            try:
                local = warehouse.query_kline(codes, begin_date, target_date, period="day")
            except Exception as e:  # noqa: BLE001
                logger.warning("Local daily K-line lookup failed: %s", e)
                local = pd.DataFrame()
        return local

    def _calculate_limit_up_stocks(
        self,
        kline: pd.DataFrame,
        name_map: dict[str, str],
        board_map: dict[str, str],
        date_str: str,
        target_date: int,
        board_filter: Optional[str],
        exclude_st: bool,
    ) -> list[LimitUpItem]:
        stocks: list[LimitUpItem] = []
        for row, pre_close, history in _iter_target_rows_with_pre_close(kline, target_date):
            item = build_limit_up_item(row, pre_close, history, name_map, board_map, date_str, board_filter, exclude_st)
            if item is not None:
                stocks.append(item)
        return stocks

    def _get_warehouse(self):
        if self.warehouse is False:
            return None
        if self.warehouse is None:
            if not get_settings().historical_enabled:
                self.warehouse = False
                return None
            try:
                self.warehouse = get_warehouse()
            except Exception as e:  # noqa: BLE001
                logger.warning("Historical warehouse unavailable: %s", e)
                self.warehouse = False
                return None
        return self.warehouse


def get_limit_up_service() -> LimitUpService:
    """Create a limit-up service for the current process."""
    return LimitUpService()


def build_limit_up_item(
    row: dict,
    pre_close: float,
    history: list[tuple[int, float]],
    name_map: dict[str, str],
    board_map: dict[str, str],
    date_str: str,
    board_filter: Optional[str],
    exclude_st: bool,
) -> Optional[LimitUpItem]:
    """Build a limit-up response item from one daily K-line row.

    ``history`` is a list of (date, pre_close) tuples for trading days strictly
    before ``target_date``, sorted ascending. It is used to compute
    ``limitUpDays`` — the number of consecutive trading days up to and
    including today where the close hit the limit-up price.
    """
    code = str(row.get("code", ""))
    if not code:
        return None

    board = board_map.get(code) or board_map.get(code.split(".")[0]) or detect_board(code)
    if board_filter and board != board_filter:
        return None

    close = float(row.get("close", 0) or 0)
    open_price = float(row.get("open", 0) or 0)
    high = float(row.get("high", 0) or 0)
    low = float(row.get("low", 0) or 0)
    volume = int(row.get("volume", 0) or 0)
    amount = float(row.get("amount", 0) or 0)

    if pre_close <= 0:
        return None

    limit_up_price = calculate_limit_up_price(pre_close, board)
    if not is_limit_up_price(close, limit_up_price):
        return None

    change_pct = (close - pre_close) / pre_close
    name = name_map.get(code) or name_map.get(code.split(".")[0]) or code
    if exclude_st and ("ST" in name or "*ST" in name or name.startswith("ST") or name.startswith("*ST")):
        return None

    # Count consecutive limit-up days. The first row of ``history`` is the
    # most recent prior trading day; we walk backward from there and stop at
    # the first day that did not hit limit up.
    limit_up_days = _count_consecutive_limit_up_days(history, board, pre_close)

    return LimitUpItem(
        code=code.split(".")[0] if "." in code else code,
        name=name,
        limitUpDate=date_str,
        changePct=round(change_pct, 4),
        board=board,
        limitUpDays=limit_up_days,
        price=round(close, 2),
        preClose=round(pre_close, 2),
        open=round(open_price, 2),
        high=round(high, 2),
        low=round(low, 2),
        amount=round(amount, 2),
        volume=volume,
        amplitude=round((high - low) / pre_close, 4) if pre_close > 0 else 0,
        turnover=0,
        firstTime="",
        finalTime="",
        reason="",
        industry="",
        concept="",
    )


def _count_consecutive_limit_up_days(
    history: list[tuple[int, float]],
    board: str,
    today_pre_close: float,
) -> int:
    """Count consecutive trading days, up to and including today, that hit the
    limit-up price.

    Inputs:
        history: list of (date, pre_close) for days strictly before today,
            ordered ascending. pre_close[i] is the close of the day before
            history[i][0].
        today_pre_close: today's pre_close (= close of the most recent day in
            history, i.e. yesterday).

    Algorithm: today hit limit up (verified by caller, count starts at 1).
    Walk backward through history. For each (date_d, pre_close_d) we need
    (close_d, pre_close_d). close_d == pre_close of the next entry, which for
    the last history entry is today_pre_close. So for d = history[-1][0]:
    close_d = today_pre_close, pre_close_d = history[-1][1]. Compare.
    For earlier entries d = history[i][0]: close_d = history[i+1][1],
    pre_close_d = history[i][1]. Compare. Stop at the first non-limit-up day.
    """
    count = 1
    if not history:
        return count
    # Build a (date, pre_close, close) tuple for each history day.
    enriched: list[tuple[int, float, float]] = []
    for i, (d, pc) in enumerate(history):
        if i + 1 < len(history):
            close = history[i + 1][1]
        else:
            # Most recent history day closed at today's pre_close
            close = today_pre_close
        enriched.append((int(d), float(pc or 0), float(close or 0)))
    # Walk backward from yesterday
    for d, pre_close_d, close_d in reversed(enriched):
        if pre_close_d <= 0 or close_d <= 0:
            break
        limit_price = calculate_limit_up_price(pre_close_d, board)
        if is_limit_up_price(close_d, limit_price):
            count += 1
        else:
            break
    return count


def detect_board(code: str) -> str:
    """Detect stock board from code."""
    clean = code.split(".")[0] if "." in code else code
    if clean.startswith("68"):
        return "科创板"
    if clean.startswith("8") or clean.startswith("4"):
        return "北交所"
    if clean.startswith("30"):
        return "创业板"
    if clean.startswith("60") or clean.startswith("00"):
        return "主板"
    return "主板"


def calculate_limit_up_price(pre_close: float, board: str) -> float:
    """Calculate the theoretical limit-up price rounded to cents."""
    rate = LIMIT_UP_RATES.get(board, LIMIT_UP_RATES["主板"])
    price = Decimal(str(pre_close)) * (Decimal("1") + rate)
    return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_limit_up_price(close: float, limit_up_price: float) -> bool:
    """Check if close reached the theoretical limit-up price."""
    return Decimal(str(close)) >= Decimal(str(limit_up_price))


def code_aliases(code: str) -> set[str]:
    """Return equivalent code keys used by different AmazingData APIs."""
    clean = str(code).strip()
    if not clean:
        return set()
    aliases = {clean}
    if "." in clean:
        aliases.add(clean.split(".", 1)[0])
    return aliases


def build_name_map(df_info: pd.DataFrame) -> dict[str, str]:
    """Build a stock name map from common AmazingData code-info layouts."""
    if not isinstance(df_info, pd.DataFrame) or df_info.empty:
        return {}

    code_columns = ("code", "MARKET_CODE", "market_code", "security_code", "SECURITY_CODE")
    name_columns = ("name", "symbol", "SECURITY_NAME", "security_name", "SHORT_NAME", "short_name")

    code_col = next((col for col in code_columns if col in df_info.columns), None)
    name_col = next((col for col in name_columns if col in df_info.columns), None)
    if name_col is None:
        return {}

    name_map: dict[str, str] = {}
    if code_col is not None:
        rows = zip(df_info[code_col], df_info[name_col])
    else:
        rows = zip(df_info.index, df_info[name_col])

    for raw_code, raw_name in rows:
        if pd.isna(raw_code) or pd.isna(raw_name):
            continue
        name = str(raw_name).strip()
        if not name:
            continue
        for alias in code_aliases(str(raw_code)):
            name_map[alias] = name
    return name_map


def build_board_map(df_info: pd.DataFrame) -> dict[str, str]:
    """Build code -> board map from standardized or raw code metadata."""
    if not isinstance(df_info, pd.DataFrame) or df_info.empty:
        return {}
    code_columns = ("code", "MARKET_CODE", "market_code", "security_code", "SECURITY_CODE")
    code_col = next((col for col in code_columns if col in df_info.columns), None)
    if code_col is None:
        rows = zip(df_info.index, df_info.get("board", pd.Series(dtype=str)))
    elif "board" in df_info.columns:
        rows = zip(df_info[code_col], df_info["board"])
    else:
        return {}

    board_map: dict[str, str] = {}
    for raw_code, raw_board in rows:
        if pd.isna(raw_code) or pd.isna(raw_board):
            continue
        board = str(raw_board).strip()
        if not board:
            continue
        for alias in code_aliases(str(raw_code)):
            board_map[alias] = board
    return board_map


def _codes_from_info(df_info: pd.DataFrame) -> list[str]:
    if not isinstance(df_info, pd.DataFrame) or df_info.empty:
        return []
    if "code" in df_info.columns:
        return [str(code).strip() for code in df_info["code"].tolist() if str(code).strip()]
    return [str(code).strip() for code in df_info.index.tolist() if str(code).strip()]


def _filter_codes_by_board(
    codes: Sequence[str],
    board_map: dict[str, str],
    board_filter: Optional[str],
) -> list[str]:
    if not board_filter:
        return list(codes)
    matched = []
    for code in codes:
        board = board_map.get(code) or board_map.get(code.split(".")[0]) or detect_board(code)
        if board == board_filter:
            matched.append(code)
    return matched


def _codes_missing_target(local: pd.DataFrame, codes: Sequence[str], target_date: int) -> list[str]:
    if local is None or local.empty or "code" not in local.columns or "date" not in local.columns:
        return list(codes)
    target = local[pd.to_numeric(local["date"], errors="coerce").fillna(0).astype(int) == int(target_date)]
    present = {str(code) for code in target["code"].tolist()}
    return [code for code in codes if code not in present]


def _iter_target_rows_with_pre_close(kline: pd.DataFrame, target_date: int):
    """Yield (row, pre_close, history) for each code that has a row on target_date.

    ``history`` is a list of (date, pre_close) pairs sorted ascending by date,
    where ``pre_close`` is the previous trading day's close. The history covers
    up to the lookback window fetched by the caller (default 14 days) so the
    caller can compute consecutive limit-up days without re-querying.
    """
    if kline is None or kline.empty or "code" not in kline.columns or "date" not in kline.columns:
        return
    df = kline.copy()
    df["date"] = pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values(["code", "date"])
    for code, group in df.groupby("code"):
        up_to_today = group[group["date"] <= int(target_date)]
        current = up_to_today[up_to_today["date"] == int(target_date)]
        if current.empty:
            continue
        previous = up_to_today[up_to_today["date"] < int(target_date)]
        if previous.empty:
            continue
        row = current.iloc[-1].to_dict()
        row["code"] = str(code)
        # History: (date, pre_close_for_that_date) for every day before today
        # in the lookback window. pre_close_for_a_given_date is the close of
        # the trading day immediately before that date.
        history: list[tuple[int, float]] = []
        sorted_prev = previous.sort_values("date")
        prev_closes = sorted_prev["close"].tolist()
        prev_dates = sorted_prev["date"].tolist()
        for i, d in enumerate(prev_dates):
            if i == 0:
                history.append((int(d), 0.0))
            else:
                history.append((int(d), float(prev_closes[i - 1] or 0)))
        yield row, float(prev_closes[-1] or 0), history


def _persist_codes(df: pd.DataFrame, warehouse) -> None:
    path = warehouse.meta_dir() / "codes.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    warehouse.refresh_views()


def _persist_kline_to_warehouse(df: pd.DataFrame, warehouse) -> None:
    if df is None or df.empty:
        return
    if "code" not in df.columns:
        return
    root = Path(warehouse.root)
    for code, code_df in df.groupby(df["code"].astype(str)):
        std = standardize_kline_df(code_df, code=code)
        if std.empty:
            continue
        path = kline_file_path(root, "day", code)
        if path.exists():
            try:
                existing = pd.read_parquet(path)
                std = pd.concat([existing, std], ignore_index=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to read existing K-line file %s: %s", path, e)
        std = validate_kline_df(std)
        if std.empty:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        std.to_parquet(path, engine="pyarrow", compression="zstd", index=False)


def _lookback_begin_date(target_date: int, days: int = 14) -> int:
    dt = datetime.strptime(str(int(target_date)), "%Y%m%d")
    return int((dt - timedelta(days=days)).strftime("%Y%m%d"))


def _today_int() -> int:
    return int(datetime.now().strftime("%Y%m%d"))


def _date_str(date: int) -> str:
    return datetime.strptime(str(int(date)), "%Y%m%d").strftime("%Y-%m-%d")
LIMIT_DOWN_RATES = LIMIT_UP_RATES


def calculate_limit_down_price(pre_close: float, board: str) -> float:
    """Calculate the theoretical limit-down price rounded to cents."""
    rate = LIMIT_DOWN_RATES.get(board, LIMIT_DOWN_RATES["主板"])
    price = Decimal(str(pre_close)) * (Decimal("1") - rate)
    return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_limit_down_price(close: float, limit_down_price: float) -> bool:
    """Check if close reached the theoretical limit-down price."""
    return Decimal(str(close)) <= Decimal(str(limit_down_price))


def build_limit_down_item(
    row: dict,
    pre_close: float,
    name_map: dict[str, str],
    board_map: dict[str, str],
    date_str: str,
    board_filter: Optional[str],
    exclude_st: bool,
) -> Optional[LimitDownItem]:
    """Build a limit-down response item from one daily K-line row."""
    code = str(row.get("code", ""))
    if not code:
        return None

    board = board_map.get(code) or board_map.get(code.split(".")[0]) or detect_board(code)
    if board_filter and board != board_filter:
        return None

    close = float(row.get("close", 0) or 0)
    open_price = float(row.get("open", 0) or 0)
    high = float(row.get("high", 0) or 0)
    low = float(row.get("low", 0) or 0)
    volume = int(row.get("volume", 0) or 0)
    amount = float(row.get("amount", 0) or 0)

    if pre_close <= 0:
        return None

    limit_down_price = calculate_limit_down_price(pre_close, board)
    if not is_limit_down_price(close, limit_down_price):
        return None

    change_pct = (close - pre_close) / pre_close
    name = name_map.get(code) or name_map.get(code.split(".")[0]) or code
    if exclude_st and ("ST" in name or "*ST" in name or name.startswith("ST") or name.startswith("*ST")):
        return None

    return LimitDownItem(
        code=code.split(".")[0] if "." in code else code,
        name=name,
        limitDownDate=date_str,
        changePct=round(change_pct, 4),
        board=board,
        limitDownDays=1,
        price=round(close, 2),
        preClose=round(pre_close, 2),
        open=round(open_price, 2),
        high=round(high, 2),
        low=round(low, 2),
        amount=round(amount, 2),
        volume=volume,
        amplitude=round((high - low) / pre_close, 4) if pre_close > 0 else 0,
        turnover=0,
        firstTime="",
        finalTime="",
        reason="",
        industry="",
        concept="",
    )


# ============================================================
# Limit-Down Service
# ============================================================


class LimitDownService(LimitUpService):
    """Calculate limit-down lists from daily K-lines."""

    def get_limit_down(
        self,
        days: int = 1,
        date: Optional[int] = None,
        board_filter: Optional[str] = None,
        exclude_st: bool = True,
    ) -> LimitDownResponse:
        """Return the current limit-down stocks."""
        target_date = int(date or _today_int())
        date_str = _date_str(target_date)
        df_info = self._get_code_info()
        codes = _codes_from_info(df_info)
        if not codes:
            return LimitDownResponse(date=date_str, stocks=[], count=0)

        name_map = build_name_map(df_info)
        board_map = build_board_map(df_info)
        codes = _filter_codes_by_board(codes, board_map, board_filter)
        if not codes:
            return LimitDownResponse(date=date_str, stocks=[], count=0)
        kline = self._get_daily_kline(codes, target_date)
        stocks = self._calculate_limit_down_stocks(
            kline,
            name_map,
            board_map,
            date_str,
            target_date,
            board_filter,
            exclude_st,
        )

        return LimitDownResponse(date=date_str, stocks=stocks, count=len(stocks), data=stocks)

    def _calculate_limit_down_stocks(
        self,
        kline: pd.DataFrame,
        name_map: dict[str, str],
        board_map: dict[str, str],
        date_str: str,
        target_date: int,
        board_filter: Optional[str],
        exclude_st: bool,
    ) -> list[LimitDownItem]:
        stocks: list[LimitDownItem] = []
        for row, pre_close, _history in _iter_target_rows_with_pre_close(kline, target_date):
            item = build_limit_down_item(row, pre_close, name_map, board_map, date_str, board_filter, exclude_st)
            if item is not None:
                stocks.append(item)
        return stocks


# ============================================================
# Market Activity Service (赚钱效应)
# ============================================================


class MarketActivityService:
    """Calculate market-wide activity stats (赚钱效应) from daily K-lines."""

    def __init__(self, adapter=None, warehouse=None, batch_size: int = 200) -> None:
        self._base = LimitUpService(adapter=adapter, warehouse=warehouse, batch_size=batch_size)

    def get_market_activity(self, date: Optional[int] = None) -> MarketActivityResponse:
        """Return market activity distribution for a given date."""
        target_date = int(date or _today_int())
        date_str = _date_str(target_date)
        df_info = self._base._get_code_info()
        codes = _codes_from_info(df_info)
        if not codes:
            return MarketActivityResponse(
                date=date_str,
                distribution=MarketActivityDistribution(),
                count=0,
                data={},
            )

        kline = self._base._get_daily_kline(codes, target_date)
        return self._calculate_activity(kline, target_date, date_str)

    def _calculate_activity(
        self,
        kline: pd.DataFrame,
        target_date: int,
        date_str: str,
    ) -> MarketActivityResponse:
        if kline is None or kline.empty or "code" not in kline.columns or "date" not in kline.columns:
            return MarketActivityResponse(
                date=date_str,
                distribution=MarketActivityDistribution(),
                count=0,
                data={},
            )

        df = kline.copy()
        df["date"] = pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int)
        df = df.sort_values(["code", "date"])

        rising = 0
        falling = 0
        flat = 0
        suspended = 0
        limit_up = 0
        limit_down = 0
        total = 0

        for code, group in df.groupby("code"):
            group = group[group["date"] <= int(target_date)]
            current = group[group["date"] == int(target_date)]
            if current.empty:
                continue
            previous = group[group["date"] < int(target_date)]
            if previous.empty:
                continue

            row = current.iloc[-1]
            pre_close = float(previous.iloc[-1].get("close", 0) or 0)
            close = float(row.get("close", 0) or 0)
            volume = int(row.get("volume", 0) or 0)
            code_str = str(code)
            board = detect_board(code_str)

            total += 1

            if volume == 0 or pre_close <= 0:
                suspended += 1
                continue

            if close > pre_close:
                rising += 1
            elif close < pre_close:
                falling += 1
            else:
                flat += 1

            limit_up_price = calculate_limit_up_price(pre_close, board)
            if is_limit_up_price(close, limit_up_price):
                limit_up += 1

            limit_down_price = calculate_limit_down_price(pre_close, board)
            if is_limit_down_price(close, limit_down_price):
                limit_down += 1

        activity_rate = round(rising / total, 4) if total > 0 else 0.0
        distribution = MarketActivityDistribution(
            rising=rising,
            limit_up=limit_up,
            real_limit_up=limit_up,
            falling=falling,
            limit_down=limit_down,
            real_limit_down=limit_down,
            flat=flat,
            suspended=suspended,
            total=total,
        )

        return MarketActivityResponse(
            date=date_str,
            distribution=distribution,
            activity_rate=activity_rate,
            count=total,
            data={
                "activity_rate": activity_rate,
                "distribution": distribution.model_dump(),
            },
        )


# ============================================================
# Strong Stock Pool Service (强势股池)
# ============================================================


class StrongStockPoolService:
    """Screen strong stocks from daily K-lines (60-day high, limit-up genes, volume ratio)."""

    def __init__(self, adapter=None, warehouse=None, batch_size: int = 200) -> None:
        self.adapter = adapter
        self.warehouse = warehouse
        self.batch_size = batch_size
        self._base = LimitUpService(adapter=adapter, warehouse=warehouse, batch_size=batch_size)

    def get_strong_pool(
        self,
        date: Optional[int] = None,
        lookback_days: int = 20,
        min_change_pct: float = 0.03,
    ) -> StrongStockPoolResponse:
        """Return strong stock pool for a given date."""
        target_date = int(date or _today_int())
        date_str = _date_str(target_date)
        df_info = self._base._get_code_info()
        codes = _codes_from_info(df_info)
        if not codes:
            return StrongStockPoolResponse(date=date_str, stocks=[], count=0)

        name_map = build_name_map(df_info)
        board_map = build_board_map(df_info)

        # Fetch longer history for new-high detection and volume ratio
        begin_date = _lookback_begin_date(target_date, days=lookback_days + 5)
        kline = self._get_kline_range(codes, begin_date, target_date)

        stocks = self._calculate_strong_stocks(
            kline,
            name_map,
            board_map,
            date_str,
            target_date,
            lookback_days,
            min_change_pct,
        )
        return StrongStockPoolResponse(date=date_str, stocks=stocks, count=len(stocks), data=stocks)

    def _get_kline_range(self, codes: Sequence[str], begin_date: int, end_date: int) -> pd.DataFrame:
        warehouse = self._base._get_warehouse()
        local = pd.DataFrame()
        if warehouse is not None:
            try:
                local = warehouse.query_kline(codes, begin_date, end_date, period="day")
            except Exception as e:  # noqa: BLE001
                logger.warning("Local K-line lookup failed: %s", e)
                local = pd.DataFrame()
        return local

    def _calculate_strong_stocks(
        self,
        kline: pd.DataFrame,
        name_map: dict[str, str],
        board_map: dict[str, str],
        date_str: str,
        target_date: int,
        lookback_days: int,
        min_change_pct: float,
    ) -> list[StrongStockItem]:
        if kline is None or kline.empty or "code" not in kline.columns or "date" not in kline.columns:
            return []

        df = kline.copy()
        df["date"] = pd.to_numeric(df["date"], errors="coerce").fillna(0).astype(int)
        df = df.sort_values(["code", "date"])

        stocks: list[StrongStockItem] = []

        for code, group in df.groupby("code"):
            group = group[group["date"] <= int(target_date)]
            current = group[group["date"] == int(target_date)]
            if current.empty:
                continue

            # Need at least 2 days for change-pct calculation
            previous = group[group["date"] < int(target_date)]
            if previous.empty:
                continue

            row = current.iloc[-1]
            pre_close = float(previous.iloc[-1].get("close", 0) or 0)
            close = float(row.get("close", 0) or 0)
            volume = int(row.get("volume", 0) or 0)
            amount = float(row.get("amount", 0) or 0)
            high = float(row.get("high", 0) or 0)
            low = float(row.get("low", 0) or 0)

            if pre_close <= 0:
                continue

            change_pct = (close - pre_close) / pre_close
            code_str = str(code)
            board = board_map.get(code_str) or board_map.get(code_str.split(".")[0]) or detect_board(code_str)
            name = name_map.get(code_str) or name_map.get(code_str.split(".")[0]) or code_str

            # Lookback window for new-high and volume ratio
            lookback = group[group["date"] >= int(_lookback_begin_date(target_date, days=lookback_days))]
            lookback_before_today = lookback[lookback["date"] < int(target_date)]

            # lookback_days new high
            is_new_high = False
            if not lookback.empty and len(lookback) >= 2:
                highs = pd.to_numeric(lookback["high"], errors="coerce").fillna(0)
                if not highs.empty and high >= highs.max():
                    is_new_high = True

            # Volume ratio: today volume / avg volume of last 5 trading days
            volume_ratio = 0.0
            if not lookback_before_today.empty:
                vol_series = pd.to_numeric(lookback_before_today["volume"], errors="coerce").fillna(0)
                if len(vol_series) >= 1:
                    avg_vol = vol_series.tail(5).mean()
                    if avg_vol > 0:
                        volume_ratio = round(volume / avg_vol, 2)

            # Limit-up count in lookback period
            limit_up_count = 0
            for _, prev_row in lookback.iterrows():
                prev_date = int(prev_row.get("date", 0))
                if prev_date >= int(target_date):
                    continue
                prev_close = float(prev_row.get("close", 0) or 0)
                if prev_close <= 0:
                    continue
                prev_high = float(prev_row.get("high", 0) or 0)
                lup_price = calculate_limit_up_price(prev_close, board)
                if is_limit_up_price(prev_high, lup_price):
                    limit_up_count += 1

            # Filter: strong if change_pct >= min_change_pct OR new high OR has limit-up genes
            is_strong = (
                change_pct >= min_change_pct
                or is_new_high
                or limit_up_count > 0
                or volume_ratio >= 1.5
            )
            if not is_strong:
                continue

            # Build reason string
            reasons: list[str] = []
            if change_pct >= min_change_pct:
                reasons.append(f"涨幅{change_pct*100:.1f}%")
            if is_new_high:
                reasons.append(f"{lookback_days}日新高")
            if limit_up_count > 0:
                reasons.append(f"近{lookback_days}日涨停{limit_up_count}次")
            if volume_ratio >= 1.5:
                reasons.append(f"量比{volume_ratio}")

            stocks.append(
                StrongStockItem(
                    code=code_str.split(".")[0] if "." in code_str else code_str,
                    name=name,
                    changePct=round(change_pct, 4),
                    price=round(close, 2),
                    amount=round(amount, 2),
                    volume=volume,
                    turnover=0,
                    is_new_high=is_new_high,
                    limit_up_count=limit_up_count,
                    volume_ratio=volume_ratio,
                    industry="",
                    reason=";".join(reasons) if reasons else "强势",
                )
            )

        # Sort by changePct desc
        stocks.sort(key=lambda s: s.changePct, reverse=True)
        return stocks


# ============================================================
# Service factories
# ============================================================


def get_limit_down_service() -> LimitDownService:
    """Create a limit-down service for the current process."""
    return LimitDownService()


def get_market_activity_service() -> MarketActivityService:
    """Create a market activity service for the current process."""
    return MarketActivityService()


def get_strong_stock_pool_service() -> StrongStockPoolService:
    """Create a strong stock pool service for the current process."""
    return StrongStockPoolService()
