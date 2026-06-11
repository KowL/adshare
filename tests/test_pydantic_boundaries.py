"""Pydantic boundary tests for request/response schemas.

Validates edge cases: empty codes, invalid dates, oversized ranges, etc.
"""

import pytest
from pydantic import ValidationError

from adshare.models.schemas import (
    KlineRequest,
    CalendarRequest,
    SnapshotRequest,
    StockBasicRequest,
)


class TestKlineRequest:
    def test_valid_request(self):
        req = KlineRequest(codes="000001.SZ", begin_date=20240101, end_date=20241231)
        assert req.codes == "000001.SZ"
        assert req.begin_date == 20240101
        assert req.end_date == 20241231

    def test_empty_codes_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            KlineRequest(codes="", begin_date=20240101, end_date=20241231)
        assert "codes cannot be empty" in str(exc_info.value)

    def test_date_too_short_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            KlineRequest(codes="000001.SZ", begin_date=2024010, end_date=20241231)
        assert "date must be YYYYMMDD format" in str(exc_info.value)

    def test_date_too_long_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            KlineRequest(codes="000001.SZ", begin_date=202401011, end_date=20241231)
        assert "date must be YYYYMMDD format" in str(exc_info.value)

    def test_date_zero_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            KlineRequest(codes="000001.SZ", begin_date=0, end_date=20241231)
        assert "date must be YYYYMMDD format" in str(exc_info.value)

    def test_multi_codes(self):
        req = KlineRequest(
            codes="000001.SZ,600000.SH,300001.SZ",
            begin_date=20240101,
            end_date=20241231,
        )
        assert req.codes == "000001.SZ,600000.SH,300001.SZ"

    def test_period_default(self):
        req = KlineRequest(codes="000001.SZ", begin_date=20240101, end_date=20241231)
        assert req.period == "day"

    def test_limit_and_offset(self):
        req = KlineRequest(
            codes="000001.SZ", begin_date=20240101, end_date=20241231, limit=100, offset=10
        )
        assert req.limit == 100
        assert req.offset == 10


class TestCalendarRequest:
    def test_defaults(self):
        req = CalendarRequest()
        assert req.market == "SH"
        assert req.date is None

    def test_custom_market(self):
        req = CalendarRequest(market="SZ")
        assert req.market == "SZ"

    def test_with_date(self):
        req = CalendarRequest(date=20240101)
        assert req.date == 20240101


class TestSnapshotRequest:
    def test_minimal(self):
        req = SnapshotRequest(codes="000001.SZ")
        assert req.codes == "000001.SZ"
        assert req.date is None
        assert req.time is None

    def test_with_date_time(self):
        req = SnapshotRequest(codes="000001.SZ", date=20240101, time=143000)
        assert req.date == 20240101
        assert req.time == 143000


class TestStockBasicRequest:
    def test_defaults(self):
        req = StockBasicRequest()
        assert req.codes is None
        assert req.summary_only is False

    def test_with_codes(self):
        req = StockBasicRequest(codes="000001.SZ,600000.SH")
        assert req.codes == "000001.SZ,600000.SH"

    def test_summary_only(self):
        req = StockBasicRequest(summary_only=True)
        assert req.summary_only is True
