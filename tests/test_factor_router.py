"""Tests for factor analysis router."""

class TestFactorCapabilities:
    def test_capabilities(self, client):
        response = client.get("/factor/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "preprocessing" in data
        assert "analysis" in data
        assert "composite" in data
        assert "sample_factors" in data


class TestFactorAnalyze:
    def test_analyze_returns_503(self, client):
        """Factor analysis requires pre-computed factor data (not yet available)."""
        response = client.get(
            "/factor/analyze?factor_name=ma5&stock_list=000001.SZ&begin_date=20240101"
        )
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data

    def test_analyze_multi_stocks(self, client):
        response = client.get(
            "/factor/analyze?factor_name=momentum&stock_list=000001.SZ,600000.SH&begin_date=20240101&end_date=20241231&benchmark=000300.SH&group_num=5&ic_decay=20"
        )
        assert response.status_code == 503


class TestFactorComposite:
    def test_composite_returns_503(self, client):
        response = client.post(
            "/factor/composite?stock_list=000001.SZ,600000.SH",
            json=["ma5", "ma10"],
        )
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
