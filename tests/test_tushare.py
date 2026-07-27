"""Tests for the unified Tushare Pro compatible entry point."""

import pytest


def _post(client, api_name, params=None, fields=None, **extra):
    """Helper: POST /tushare with the standard envelope."""
    body = {"api_name": api_name, "params": params or {}, "token": ""}
    if fields is not None:
        body["fields"] = fields
    body.update(extra)
    return client.post("/tushare", json=body)


class TestTushareStockDaily:
    def test_daily_returns_fields_and_items(self, client):
        response = _post(
            client,
            "daily",
            params={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data
        assert "fields" in data["data"]
        assert "items" in data["data"]
        assert "ts_code" in data["data"]["fields"]
        assert "trade_date" in data["data"]["fields"]

    def test_daily_top_level_params_accepted(self, client):
        """Tushare clients also pass params at the top level — should work."""
        response = client.post(
            "/tushare",
            json={
                "api_name": "daily",
                "ts_code": "000001.SZ",
                "start_date": "20240101",
                "end_date": "20240110",
                "token": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["items"]) > 0

    def test_daily_missing_ts_code(self, client):
        response = _post(client, "daily", params={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == -400
        assert "ts_code" in data["msg"]

    def test_daily_invalid_date(self, client):
        response = _post(
            client,
            "daily",
            params={"ts_code": "000001.SZ", "start_date": "not-a-date", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == -400
        assert "date" in data["msg"].lower()

    def test_daily_fields_filter(self, client):
        response = _post(
            client,
            "daily",
            params={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
            fields="ts_code,trade_date,close",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["fields"] == ["ts_code", "trade_date", "close"]

    def test_daily_single_date_includes_previous_close(self, client):
        response = _post(
            client,
            "daily",
            params={"ts_code": "000001.SZ", "start_date": "20240105", "end_date": "20240105"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["msg"] == "Success"
        payload = data["data"]
        assert len(payload["items"]) >= 1
        row = dict(zip(payload["fields"], payload["items"][0]))
        # trade_date must be a YYYYMMDD string per the Tushare Pro spec
        assert row["trade_date"] == "20240105"
        assert row["pre_close"] == pytest.approx(10.4)
        assert row["change"] == pytest.approx(0.1)
        # vol is hands (float), amount is 千元 (float)
        assert isinstance(row["vol"], float)
        assert isinstance(row["amount"], float)


class TestTushareStockPeriods:
    def test_weekly_uses_last_trading_date(self, client):
        response = _post(
            client,
            "weekly",
            params={"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240111"},
        )
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        rows = [dict(zip(payload["fields"], item)) for item in payload["items"]]
        # Tushare Pro spec: trade_date as YYYYMMDD string, ascending order
        assert [row["trade_date"] for row in rows] == ["20240105", "20240111"]

    def test_monthly_uses_last_trading_date(self, client):
        response = _post(
            client,
            "monthly",
            params={"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240111"},
        )
        data = response.json()
        assert data["code"] == 0
        payload = data["data"]
        assert len(payload["items"]) == 1
        row = dict(zip(payload["fields"], payload["items"][0]))
        assert row["trade_date"] == "20240111"


class TestTushareStockBasic:
    def test_stock_basic(self, client):
        response = _post(client, "stock_basic", params={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "ts_code" in data["data"]["fields"]
        assert len(data["data"]["items"]) > 0

    def test_stock_basic_filter_by_ts_code(self, client):
        response = _post(client, "stock_basic", params={"ts_code": "000001.SZ"})
        assert response.status_code == 200
        data = response.json()
        items = data["data"]["items"]
        assert len(items) == 1


class TestTushareTradeCal:
    def test_trade_cal(self, client):
        response = _post(client, "trade_cal", params={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "cal_date" in data["data"]["fields"]


class TestTushareAdjFactor:
    def test_adj_factor(self, client):
        response = _post(
            client,
            "adj_factor",
            params={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareSuspendD:
    def test_suspend_d(self, client):
        response = _post(
            client,
            "suspend_d",
            params={"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240110"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareLimitList:
    def test_limit_list(self, client):
        response = _post(client, "limit_list", params={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestTushareLimitListD:
    """Tests for ``limit_list_d`` (https://tushare.pro/document/2?doc_id=298)."""

    def test_limit_list_d_registered_and_returns_envelope(self, client):
        response = _post(
            client,
            "limit_list_d",
            params={"trade_date": "20240105"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["msg"] == "Success"
        # Schema is enforced by the unit test against ``build_limit_list_d``
        assert data["data"]["fields"] == []
        assert data["data"]["items"] == []

    def test_limit_list_d_invalid_date(self, client):
        response = _post(
            client,
            "limit_list_d",
            params={"trade_date": "not-a-date"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == -400

    def test_limit_list_d_limit_type_z_returns_empty(self, client):
        """``Z`` (炸板) is reserved — currently out of scope, must return empty."""
        response = _post(
            client,
            "limit_list_d",
            params={"trade_date": "20240105", "limit_type": "Z"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []

    def test_build_limit_list_d_maps_fields(self):
        """Direct unit test of the row builder so filter behaviour is covered."""
        from adshare.services.derived_metrics import build_limit_list_d
        from adshare.models.schemas import LimitUpItem, LimitDownItem

        up_item = LimitUpItem(
            code="600000", name="浦发银行", limitUpDate="2024-01-05",
            changePct=0.1001, board="主板", limitUpDays=2,
            price=11.00, preClose=10.00, industry="银行",
        )
        down_item = LimitDownItem(
            code="300750", name="宁德时代", limitDownDate="2024-01-05",
            changePct=-0.1998, board="创业板", limitDownDays=1,
            price=160.00, preClose=200.00, industry="电池",
        )

        df = build_limit_list_d([up_item], [down_item], trade_date=20240105)
        assert len(df) == 2
        assert df.iloc[0]["trade_date"] == 20240105
        assert df.iloc[0]["ts_code"] == "600000.SH"
        assert df.iloc[0]["limit"] == "U"
        assert df.iloc[0]["limit_times"] == 2
        assert df.iloc[0]["up_stat"] == "1/2"
        assert df.iloc[0]["industry"] == "银行"

        assert df.iloc[1]["ts_code"] == "300750.SZ"
        assert df.iloc[1]["limit"] == "D"
        assert df.iloc[1]["up_stat"] == ""
        assert df.iloc[1]["industry"] == "电池"

    def test_build_limit_list_d_empty(self):
        from adshare.services.derived_metrics import build_limit_list_d

        df = build_limit_list_d([], [], trade_date=20240105)
        assert len(df) == 0
        # Schema is preserved even when empty so clients can iterate headers
        for col in (
            "trade_date", "ts_code", "industry", "name", "close", "pct_chg",
            "amount", "limit_amount", "float_mv", "total_mv", "turnover_ratio",
            "fd_amount", "first_time", "last_time", "open_times",
            "up_stat", "limit_times", "limit",
        ):
            assert col in df.columns


class TestTushareUnifiedUnsupported:
    def test_unsupported_api(self, client):
        response = _post(client, "not_real", params={})
        # Per the Tushare Pro protocol: HTTP stays 200, error reported via ``code``
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == -501
        assert "not supported" in data["msg"]


class TestTushareIndexReserved:
    def test_index_basic_reserved(self, client):
        response = _post(client, "index_basic", params={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == -501
