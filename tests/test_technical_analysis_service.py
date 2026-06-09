"""Contract tests for the technical analysis application service."""

import pandas as pd
import pytest

from adshare.services.market_data import KlineQueryResult
from adshare.services.technical_analysis import TechnicalAnalysisError, TechnicalAnalysisService


class FakeMarketDataService:
    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.calls = []

    def get_kline(
        self,
        codes: str,
        begin_date: int,
        end_date: int,
        period: str = "day",
        limit=None,
        offset: int = 0,
        source: str = "auto",
    ) -> KlineQueryResult:
        self.calls.append(
            {
                "codes": codes,
                "begin_date": begin_date,
                "end_date": end_date,
                "period": period,
                "source": source,
            }
        )
        return KlineQueryResult(df=self.df, source="sdk", synced=False)


def sample_kline(rows: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=rows, freq="B")
    return pd.DataFrame(
        {
            "code": ["000001.SZ"] * rows,
            "kline_time": dates,
            "open": [10.0 + i * 0.1 for i in range(rows)],
            "high": [10.5 + i * 0.1 for i in range(rows)],
            "low": [9.8 + i * 0.1 for i in range(rows)],
            "close": [10.2 + i * 0.1 for i in range(rows)],
            "volume": [100000 + i * 1000 for i in range(rows)],
            "amount": [1000000.0 + i * 10000 for i in range(rows)],
        }
    )


def test_analyze_single_indicator_returns_existing_response_shape():
    market_service = FakeMarketDataService(sample_kline())
    service = TechnicalAnalysisService(market_data_service=market_service)

    result = service.analyze("000001.SZ", begin_date=20240101, end_date=20241231, indicator="MACD")

    assert result.code == "000001.SZ"
    assert result.count == 1
    assert result.categories["indicator"].name == "MACD"
    assert result.categories["indicator"].indicators[0].name == "MACD"
    assert market_service.calls[0]["period"] == "day"
    assert market_service.calls[0]["source"] == "auto"


def test_analyze_category_returns_only_requested_category():
    service = TechnicalAnalysisService(market_data_service=FakeMarketDataService(sample_kline()))

    result = service.analyze("000001.SZ", begin_date=20240101, end_date=20241231, category="trend")

    assert list(result.categories.keys()) == ["trend"]
    assert len(result.categories["trend"].indicators) > 0


def test_analyze_invalid_indicator_raises_404():
    service = TechnicalAnalysisService(market_data_service=FakeMarketDataService(sample_kline()))

    with pytest.raises(TechnicalAnalysisError) as exc:
        service.analyze("000001.SZ", indicator="INVALID")

    assert exc.value.status_code == 404


def test_analyze_invalid_category_raises_400():
    service = TechnicalAnalysisService(market_data_service=FakeMarketDataService(sample_kline()))

    with pytest.raises(TechnicalAnalysisError) as exc:
        service.analyze("000001.SZ", category="INVALID")

    assert exc.value.status_code == 400


def test_analyze_empty_kline_raises_404():
    service = TechnicalAnalysisService(market_data_service=FakeMarketDataService(pd.DataFrame()))

    with pytest.raises(TechnicalAnalysisError) as exc:
        service.analyze("UNKNOWN.CODE")

    assert exc.value.status_code == 404


def test_analyze_uses_default_begin_date_when_omitted():
    market_service = FakeMarketDataService(sample_kline())
    service = TechnicalAnalysisService(market_data_service=market_service)

    service.analyze("000001.SZ", end_date=20241231, indicator="MACD")

    assert market_service.calls[0]["begin_date"] == 20240101
    assert market_service.calls[0]["end_date"] == 20241231
