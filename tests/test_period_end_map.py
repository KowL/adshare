"""Unit tests for the weekly/monthly period-end date remap."""

from __future__ import annotations

from datetime import date

from adshare.historical.warehouse import build_period_end_map


# Two full trading weeks: 2026-07-20..24 and 2026-07-27..31 (Mon-Fri).
TRADING_DAYS = [
    date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22),
    date(2026, 7, 23), date(2026, 7, 24),
    date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29),
    date(2026, 7, 30), date(2026, 7, 31),
]


def test_weekly_first_day_maps_to_friday():
    result = build_period_end_map([date(2026, 7, 27)], "weekly", TRADING_DAYS)
    assert result == {date(2026, 7, 27): date(2026, 7, 31)}


def test_weekly_suspended_stock_midweek_label_maps_to_same_friday():
    # A stock suspended Mon-Thu gets its weekly bar labelled 2026-07-31 by
    # the SDK; it must land on the same period-end as normal stocks.
    result = build_period_end_map(
        [date(2026, 7, 27), date(2026, 7, 29), date(2026, 7, 31)],
        "weekly",
        TRADING_DAYS,
    )
    assert result == {
        date(2026, 7, 27): date(2026, 7, 31),
        date(2026, 7, 29): date(2026, 7, 31),
        date(2026, 7, 31): date(2026, 7, 31),
    }


def test_weekly_previous_week_maps_to_its_own_friday():
    result = build_period_end_map([date(2026, 7, 20)], "weekly", TRADING_DAYS)
    assert result == {date(2026, 7, 20): date(2026, 7, 24)}


def test_monthly_first_day_maps_to_month_end():
    result = build_period_end_map([date(2026, 7, 1)], "monthly", TRADING_DAYS)
    assert result == {date(2026, 7, 1): date(2026, 7, 31)}


def test_period_missing_from_calendar_is_left_unmapped():
    result = build_period_end_map([date(2026, 8, 3)], "weekly", TRADING_DAYS)
    assert result == {}


def test_daily_period_and_empty_calendar_return_empty():
    assert build_period_end_map([date(2026, 7, 27)], "daily", TRADING_DAYS) == {}
    assert build_period_end_map([date(2026, 7, 27)], "weekly", []) == {}
