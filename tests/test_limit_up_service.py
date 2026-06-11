"""Contract tests for the limit-up application service."""

from pathlib import Path

import pandas as pd

from adshare.services.limit_up import (
    LimitUpService,
    build_name_map,
    calculate_limit_up_price,
    is_limit_up_price,
)


class FakeWarehouse:
    def __init__(self, root: Path, codes_df: pd.DataFrame, kline_df: pd.DataFrame | None = None) -> None:
        self.root = root
        self._codes_df = codes_df
        self._kline_df = kline_df if kline_df is not None else pd.DataFrame()
        self.query_codes_calls = 0
        self.query_kline_calls = []
        self.refresh_count = 0
        (self.root / "meta").mkdir(parents=True, exist_ok=True)

    def query_codes(self, board=None, is_listed=None) -> pd.DataFrame:
        self.query_codes_calls += 1
        df = self._codes_df.copy()
        if is_listed is not None and "is_listed" in df.columns:
            df = df[df["is_listed"] == is_listed]
        return df

    def query_kline(self, codes, begin_date, end_date, period="day") -> pd.DataFrame:
        self.query_kline_calls.append(
            {
                "codes": list(codes),
                "begin_date": begin_date,
                "end_date": end_date,
                "period": period,
            }
        )
        if self._kline_df.empty:
            return pd.DataFrame()
        df = self._kline_df.copy()
        df = df[df["code"].isin(codes)]
        df = df[(df["date"] >= begin_date) & (df["date"] <= end_date)]
        return df.reset_index(drop=True)

    def meta_dir(self) -> Path:
        return self.root / "meta"

    def refresh_views(self) -> None:
        self.refresh_count += 1


class FakeLimitUpAdapter:
    def __init__(self, *, logged_in: bool = False, fail_second_batch: bool = False) -> None:
        self.is_logged_in = logged_in
        self.fail_second_batch = fail_second_batch
        self.code_info_calls = 0
        self.code_list_calls = 0
        self.kline_calls = []

    def get_code_list(self, security_type: str = "EXTRA_STOCK_A") -> list[str]:
        self.code_list_calls += 1
        return ["000001.SZ", "600000.SH", "300001.SZ", "688001.SH", "000002.SZ"]

    def get_code_info(self, security_type: str = "EXTRA_STOCK_A") -> pd.DataFrame:
        self.code_info_calls += 1
        return code_info_df()

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        **kwargs,
    ) -> pd.DataFrame:
        self.kline_calls.append(codes)
        if self.fail_second_batch and len(self.kline_calls) == 2:
            raise RuntimeError("kline unavailable")
        return kline_df([code for code in codes.split(",") if code])


def code_info_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["000001.SZ", "600000.SH", "300001.SZ", "688001.SH", "000002.SZ"],
            "name": ["平安银行", "浦发银行", "创业科技", "科创测试", "ST测试"],
            "board": ["主板", "主板", "创业板", "科创板", "主板"],
            "is_listed": [True, True, True, True, True],
        }
    )


def kline_df(codes: list[str]) -> pd.DataFrame:
    rows = []
    payload = {
        "000001.SZ": [(20240606, 10.0), (20240607, 11.0)],
        "600000.SH": [(20240606, 10.0), (20240607, 10.10)],
        "300001.SZ": [(20240606, 20.0), (20240607, 24.0)],
        "688001.SH": [(20240606, 10.0), (20240607, 13.0)],
        "000002.SZ": [(20240606, 10.0), (20240607, 11.0)],
    }
    for code in codes:
        for date, close in payload[code]:
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "open": close - 0.2,
                    "high": close,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 100,
                    "amount": close * 100,
                }
            )
    return pd.DataFrame(rows)


def test_build_name_map_supports_code_column_and_aliases():
    df = pd.DataFrame({"code": ["000001.SZ"], "name": ["平安银行"]})

    name_map = build_name_map(df)

    assert name_map["000001.SZ"] == "平安银行"
    assert name_map["000001"] == "平安银行"


def test_limit_up_price_uses_board_rate_and_cent_rounding():
    assert calculate_limit_up_price(10.0, "主板") == 11.0
    assert calculate_limit_up_price(20.0, "创业板") == 24.0
    assert calculate_limit_up_price(10.0, "科创板") == 13.0
    assert calculate_limit_up_price(10.005, "主板") == 11.01
    assert is_limit_up_price(11.01, 11.01)
    assert not is_limit_up_price(11.0, 11.01)


def test_limit_up_uses_local_codes_and_local_daily_kline(tmp_path):
    adapter = FakeLimitUpAdapter(logged_in=False)
    warehouse = FakeWarehouse(tmp_path, code_info_df(), kline_df(["000001.SZ", "600000.SH", "300001.SZ", "000002.SZ"]))
    service = LimitUpService(adapter=adapter, warehouse=warehouse)

    result = service.get_limit_up(date=20240607, board_filter="主板", exclude_st=True)

    assert [stock.code for stock in result.stocks] == ["000001"]
    assert result.stocks[0].name == "平安银行"
    assert adapter.code_info_calls == 0
    assert adapter.kline_calls == []


def test_limit_up_empty_warehouse_returns_empty(tmp_path):
    """When warehouse has no data, service returns empty result (no adapter fallback)."""
    adapter = FakeLimitUpAdapter(logged_in=False)
    warehouse = FakeWarehouse(tmp_path, pd.DataFrame(), pd.DataFrame())
    service = LimitUpService(adapter=adapter, warehouse=warehouse, batch_size=10)

    result = service.get_limit_up(date=20240607, exclude_st=False)

    assert result.stocks == []
    assert result.count == 0
    # Adapter should NOT be consulted after service化
    assert adapter.code_info_calls == 0
    assert adapter.kline_calls == []


def test_limit_up_reads_from_warehouse_and_calculates_correctly(tmp_path):
    """Service reads K-line from warehouse and calculates limit-up correctly."""
    warehouse = FakeWarehouse(
        tmp_path,
        code_info_df(),
        kline_df(["000001.SZ", "600000.SH", "300001.SZ", "000002.SZ"]),
    )
    service = LimitUpService(warehouse=warehouse)

    result = service.get_limit_up(date=20240607, exclude_st=False)

    # 000001.SZ (主板 10.0→11.0 = 10%涨停), 300001.SZ (创业板 20.0→24.0 = 20%涨停),
    # 000002.SZ (主板 10.0→11.0 = 10%涨停)
    # 600000.SH (主板 10.0→10.10 = 1% 不涨停)
    codes = {stock.code for stock in result.stocks}
    assert codes == {"000001", "300001", "000002"}
    assert result.count == 3


def test_limit_up_warehouse_error_returns_empty(tmp_path):
    """When warehouse query raises, service returns empty (no adapter fallback)."""
    class BrokenWarehouse:
        root = tmp_path
        def query_codes(self, **kwargs):
            raise RuntimeError("warehouse down")
        def query_kline(self, **kwargs):
            raise RuntimeError("warehouse down")
        def meta_dir(self):
            return tmp_path / "meta"
        def refresh_views(self):
            pass

    service = LimitUpService(warehouse=BrokenWarehouse())

    result = service.get_limit_up(date=20240607, exclude_st=False)

    assert result.stocks == []
    assert result.count == 0


def test_limit_up_ladder_groups_current_hits_as_first_board(tmp_path):
    service = LimitUpService(
        adapter=FakeLimitUpAdapter(logged_in=False),
        warehouse=FakeWarehouse(tmp_path, code_info_df(), kline_df(["000001.SZ", "300001.SZ", "000002.SZ"])),
    )

    result = service.get_ladder(date=20240607, exclude_st=False)

    assert result.maxLevel == 1
    assert result.levels[0].name == "首板"
    # 3 stocks in kline data: 000001 (主板 10%), 300001 (创业板 20%), 000002 (主板 10%)
    assert result.levels[0].count == 3
