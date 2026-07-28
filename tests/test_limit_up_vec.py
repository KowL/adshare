"""Vectorised vs legacy limit-up / limit-down equivalence tests.

These tests verify that the new ``_vectorized_limit_list`` produces the
same items as the legacy row-by-row implementation for a fixed input.

Strategy:
    1.  Build a synthetic 14-day window covering 3 boards (主板 / 创业板 /
        科创板) with known outcomes — some hit limit-up, some hit limit-down,
        some ST, some don't move.
    2.  Run both implementations on the same input.
    3.  Compare resulting items field-by-field. Any diff fails the test.

The synthetic dataset is deliberately small (<30 stocks) so the test
runs in milliseconds and can be a CI gate.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from adshare.services.limit_up import (
    _iter_target_rows_with_pre_close,
    _vectorized_limit_list,
    build_board_map,
    build_limit_down_item,
    build_limit_up_item,
    build_name_map,
    detect_board,
)


# ---------------------------------------------------------------------------
# Synthetic dataset construction
# ---------------------------------------------------------------------------


def _make_kline() -> pd.DataFrame:
    """Build a deterministic 14-day window ending on 20260727.

    Stocks (target_date=20260727 close vs 20260724 close):
      000001.SZ 主板  10.00 -> 11.00  (+10%)  HIT (limit-up)
      000002.SZ 主板  20.00 -> 21.00  (+5%)   miss
      300001.SZ 创业板 30.00 -> 36.00  (+20%)  HIT (limit-up)
      300002.SZ 创业板 50.00 -> 49.00  (-2%)   miss
      688001.SH 科创板 100.00 -> 130.00 (+30%)  HIT (limit-up)
      000003.SZ 主板  10.00 -> 9.00   (-10%)  HIT (limit-down)
      300003.SZ 创业板 30.00 -> 24.00 (-20%)  HIT (limit-down)
      400001.SJ 北交所 5.00  -> 6.00  (+20%)  HIT (北交所 30%, but our threshold is 20%)  HIT
      000099.SZ 主板  10.00 -> 11.00  (+10%)  HIT but name is "ST 测试" -> EXCLUDED
      000100.SZ 主板  10.00 -> 11.00  (+10%)  HIT, also previous day was limit-up
                                                  -> consecutive=2
    """
    rows: list[dict] = []
    dates = [20260714, 20260715, 20260716, 20260721, 20260722, 20260723,
             20260724, 20260727]  # 8 trading days

    def add(code: str, name: str, board: str, closes: list[float]) -> None:
        for d, c in zip(dates, closes):
            rows.append({
                "code": code,
                "name": name,
                "date": d,
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000000,
                "amount": c * 1000000,
                "adj_factor": 1.0,
            })

    # target=20260727, pre_close = 20260724 close
    add("000001.SZ", "平安银行", "主板",  [9.50, 9.60, 9.70, 9.80, 9.90, 9.95, 10.00, 11.00])
    add("000002.SZ", "万科A",    "主板",  [19.0, 19.5, 19.7, 19.9, 20.0, 20.0, 20.00, 21.00])
    add("300001.SZ", "宁德时代", "创业板", [27.0, 28.0, 28.5, 29.0, 29.5, 29.8, 30.00, 36.00])
    add("300002.SZ", "迈瑞医疗", "创业板", [49.0, 49.5, 49.7, 49.9, 50.0, 50.0, 50.00, 49.00])
    add("688001.SH", "中芯国际", "科创板", [90.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.00, 130.00])
    add("000003.SZ", "国农科技", "主板",  [10.5, 10.3, 10.2, 10.1, 10.0, 10.0, 10.00, 9.00])
    add("300003.SZ", "蓝思科技", "创业板", [32.0, 31.5, 31.0, 30.5, 30.2, 30.1, 30.00, 24.00])
    add("400001.SJ", "华岭股份", "北交所", [4.50, 4.80, 5.00, 5.20, 5.40, 5.80, 5.00, 6.50])
    add("000099.SZ", "ST 测试",  "主板",  [9.50, 9.60, 9.70, 9.80, 9.90, 9.95, 10.00, 11.00])
    # consecutive limit-up: 20260724 already 10% up from 20260723 (9.00->9.90 -> 10%, hit),
    # 20260727 from 10.00 -> 11.00 hit, so consecutive = 2
    add("000100.SZ", "连续涨停", "主板",  [8.50, 8.60, 8.70, 8.80, 8.90, 9.00, 9.90, 11.00])

    return pd.DataFrame(rows)


def _make_name_map() -> dict[str, str]:
    df_info = pd.DataFrame([
        {"code": "000001.SZ", "name": "平安银行", "board": "主板"},
        {"code": "000002.SZ", "name": "万科A", "board": "主板"},
        {"code": "300001.SZ", "name": "宁德时代", "board": "创业板"},
        {"code": "300002.SZ", "name": "迈瑞医疗", "board": "创业板"},
        {"code": "688001.SH", "name": "中芯国际", "board": "科创板"},
        {"code": "000003.SZ", "name": "国农科技", "board": "主板"},
        {"code": "300003.SZ", "name": "蓝思科技", "board": "创业板"},
        {"code": "400001.SJ", "name": "华岭股份", "board": "北交所"},
        {"code": "000099.SZ", "name": "ST 测试", "board": "主板"},
        {"code": "000100.SZ", "name": "连续涨停", "board": "主板"},
    ])
    return build_name_map(df_info)


def _make_board_map() -> dict[str, str]:
    df_info = pd.DataFrame([
        {"code": "000001.SZ", "board": "主板"},
        {"code": "000002.SZ", "board": "主板"},
        {"code": "300001.SZ", "board": "创业板"},
        {"code": "300002.SZ", "board": "创业板"},
        {"code": "688001.SH", "board": "科创板"},
        {"code": "000003.SZ", "board": "主板"},
        {"code": "300003.SZ", "board": "创业板"},
        {"code": "400001.SJ", "board": "北交所"},
        {"code": "000099.SZ", "board": "主板"},
        {"code": "000100.SZ", "board": "主板"},
    ])
    return build_board_map(df_info)


# ---------------------------------------------------------------------------
# Legacy implementation (kept here verbatim so the test stays self-contained
# and decoupled from any future refactor of limit_up.py).
# ---------------------------------------------------------------------------


def _legacy_limit_up(kline: pd.DataFrame, name_map, board_map, date_str, target_date, board_filter, exclude_st):
    items = []
    for row, pre_close, history in _iter_target_rows_with_pre_close(kline, target_date):
        item = build_limit_up_item(row, pre_close, history, name_map, board_map, date_str, board_filter, exclude_st)
        if item is not None:
            items.append(item)
    return items


def _legacy_limit_down(kline: pd.DataFrame, name_map, board_map, date_str, target_date, board_filter, exclude_st):
    items = []
    for row, pre_close, _history in _iter_target_rows_with_pre_close(kline, target_date):
        item = build_limit_down_item(row, pre_close, name_map, board_map, date_str, board_filter, exclude_st)
        if item is not None:
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# Field-by-field comparator
# ---------------------------------------------------------------------------


def _item_to_dict(item) -> dict:
    """Pydantic v1/v2 compatible dump."""
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item.dict()


def _diff_items(label, legacy_items, vec_items) -> list[str]:
    diffs = []
    if len(legacy_items) != len(vec_items):
        diffs.append(f"{label}: item count {len(legacy_items)} (legacy) != {len(vec_items)} (vec)")
    legacy_by_code = {_item_to_dict(i).get("code"): _item_to_dict(i) for i in legacy_items}
    vec_by_code = {_item_to_dict(i).get("code"): _item_to_dict(i) for i in vec_items}
    all_codes = sorted(set(legacy_by_code) | set(vec_by_code))
    for code in all_codes:
        l = legacy_by_code.get(code)
        v = vec_by_code.get(code)
        if l is None:
            diffs.append(f"{label}: legacy missing code={code}")
            continue
        if v is None:
            diffs.append(f"{label}: vec missing code={code}")
            continue
        # Compare fields that are deterministic (skip turnover/firstTime/finalTime/reason
        # which are always "" / 0 placeholders).
        for field in ("name", "board", "price", "preClose", "open", "high", "low",
                      "changePct", "limitUpDays", "amplitude", "amount", "volume"):
            lv = l.get(field)
            vv = v.get(field)
            # Allow tiny float rounding diffs (< 1e-6).
            if isinstance(lv, float) and isinstance(vv, float):
                if abs(lv - vv) > 1e-6:
                    diffs.append(f"{label}: code={code} field={field} legacy={lv} vec={vv}")
            elif lv != vv:
                diffs.append(f"{label}: code={code} field={field} legacy={lv!r} vec={vv!r}")
    return diffs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detect_board_basic():
    """Sanity check on detect_board before any vectorized logic runs."""
    assert detect_board("000001.SZ") == "主板"
    assert detect_board("300001.SZ") == "创业板"
    assert detect_board("688001.SH") == "科创板"
    assert detect_board("400001.SJ") == "北交所"
    assert detect_board("830001.BJ") == "北交所"


def test_vectorized_limit_up_matches_legacy():
    """Vectorized and legacy limit-up must produce identical items."""
    kline = _make_kline()
    name_map = _make_name_map()
    board_map = _make_board_map()

    legacy = _legacy_limit_up(kline, name_map, board_map, "2026-07-27", 20260727, None, True)
    vec = _vectorized_limit_list(
        kline=kline,
        board_map=board_map,
        name_map=name_map,
        date_str="2026-07-27",
        target_date=20260727,
        board_filter=None,
        exclude_st=True,
        direction="up",
    )

    diffs = _diff_items("limit-up", legacy, vec)
    assert not diffs, "\n".join(diffs)

    # Sanity: we expect 5 limit-ups excluding ST (000001, 300001, 688001, 400001, 000100).
    assert len(legacy) == 5, f"expected 5 limit-ups, got {len(legacy)}"
    # Verify the ST stock is excluded.
    legacy_codes = {_item_to_dict(i).get("code") for i in legacy}
    assert "000099" not in legacy_codes


def test_vectorized_limit_down_matches_legacy():
    """Vectorized and legacy limit-down must produce identical items."""
    kline = _make_kline()
    name_map = _make_name_map()
    board_map = _make_board_map()

    legacy = _legacy_limit_down(kline, name_map, board_map, "2026-07-27", 20260727, None, True)
    vec = _vectorized_limit_list(
        kline=kline,
        board_map=board_map,
        name_map=name_map,
        date_str="2026-07-27",
        target_date=20260727,
        board_filter=None,
        exclude_st=True,
        direction="down",
    )

    diffs = _diff_items("limit-down", legacy, vec)
    assert not diffs, "\n".join(diffs)
    assert len(legacy) == 2, f"expected 2 limit-downs (000003, 300003), got {len(legacy)}"


def test_vectorized_consecutive_days():
    """000100.SZ hit limit-up on 20260724 and 20260727 — limitUpDays must be 2."""
    kline = _make_kline()
    name_map = _make_name_map()
    board_map = _make_board_map()

    vec = _vectorized_limit_list(
        kline=kline,
        board_map=board_map,
        name_map=name_map,
        date_str="2026-07-27",
        target_date=20260727,
        board_filter=None,
        exclude_st=True,
        direction="up",
    )
    by_code = {_item_to_dict(i).get("code"): i for i in vec}
    assert "000100" in by_code, "000100 should be a limit-up candidate"
    days = _item_to_dict(by_code["000100"]).get("limitUpDays")
    assert days == 2, f"expected 2 consecutive limit-up days, got {days}"


def test_vectorized_board_filter():
    """board_filter='创业板' should only return 创业板 candidates."""
    kline = _make_kline()
    name_map = _make_name_map()
    board_map = _make_board_map()

    vec = _vectorized_limit_list(
        kline=kline,
        board_map=board_map,
        name_map=name_map,
        date_str="2026-07-27",
        target_date=20260727,
        board_filter="创业板",
        exclude_st=True,
        direction="up",
    )
    codes = sorted(_item_to_dict(i).get("code") for i in vec)
    assert codes == ["300001"], f"expected only 300001 (创业板), got {codes}"


def test_vectorized_empty_kline():
    """Empty input must return empty list, not raise."""
    vec = _vectorized_limit_list(
        kline=pd.DataFrame(),
        board_map={},
        name_map={},
        date_str="2026-07-27",
        target_date=20260727,
        board_filter=None,
        exclude_st=True,
        direction="up",
    )
    assert vec == []


def test_vectorized_no_pre_close():
    """First-day IPO scenario: no prior trading day -> empty list."""
    kline = pd.DataFrame([
        {"code": "000001.SZ", "name": "新股", "date": 20260727,
         "open": 10.0, "high": 10.0, "low": 10.0, "close": 11.0,
         "volume": 100, "amount": 1100, "adj_factor": 1.0},
    ])
    vec = _vectorized_limit_list(
        kline=kline,
        board_map={"000001.SZ": "主板", "000001": "主板"},
        name_map={"000001.SZ": "新股", "000001": "新股"},
        date_str="2026-07-27",
        target_date=20260727,
        board_filter=None,
        exclude_st=True,
        direction="up",
    )
    assert vec == [], "No pre_close means no limit-up can be detected"