"""End-to-end tests for technical analysis endpoint.

Validates TechnicalResponse schema for both single-indicator and category modes.
"""

import pytest


class TestTechnicalAnalyze:
    def test_analyze_single_indicator(self, client):
        response = client.get("/technical/analyze?code=000001.SZ&indicator=MACD")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "000001.SZ"
        assert "price" in data
        assert "categories" in data
        # Single-indicator mode wraps under key "indicator"
        cat = data["categories"]["indicator"]
        assert cat["name"] == "MACD"
        assert isinstance(cat["indicators"], list)
        assert len(cat["indicators"]) == 1
        assert cat["indicators"][0]["name"] == "MACD"
        assert "values" in cat["indicators"][0]

    def test_analyze_category_mode(self, client):
        response = client.get("/technical/analyze?code=000001.SZ&category=trend")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "000001.SZ"
        cat = data["categories"]["trend"]
        assert cat["name"] == "trend"
        assert isinstance(cat["indicators"], list)
        assert len(cat["indicators"]) > 0
        for ind in cat["indicators"]:
            assert "name" in ind
            assert "values" in ind

    def test_analyze_all_categories(self, client):
        response = client.get("/technical/analyze?code=000001.SZ")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "000001.SZ"
        # Should contain all 7 categories
        expected_categories = {
            "overbought_oversold",
            "trend",
            "energy",
            "volume",
            "ma",
            "path",
            "other",
        }
        assert set(data["categories"].keys()) == expected_categories
        for cat in data["categories"].values():
            assert isinstance(cat["indicators"], list)
            for ind in cat["indicators"]:
                assert "name" in ind
                assert "values" in ind

    def test_analyze_invalid_indicator(self, client):
        response = client.get("/technical/analyze?code=000001.SZ&indicator=INVALID")
        assert response.status_code == 404

    @pytest.mark.parametrize("category", [
        "overbought_oversold",
        "energy",
        "volume",
        "ma",
        "path",
        "other",
    ])
    def test_analyze_each_category(self, client, category):
        response = client.get(f"/technical/analyze?code=000001.SZ&category={category}")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "000001.SZ"
        cat = data["categories"][category]
        assert cat["name"] == category
        assert isinstance(cat["indicators"], list)
        assert len(cat["indicators"]) > 0
        for ind in cat["indicators"]:
            assert "name" in ind
            assert "values" in ind

    def test_analyze_invalid_category(self, client):
        response = client.get("/technical/analyze?code=000001.SZ&category=INVALID")
        assert response.status_code == 400

    def test_analyze_no_data(self, client):
        # Unknown code should result in empty kline => 404
        response = client.get("/technical/analyze?code=UNKNOWN.CODE")
        assert response.status_code == 404


class TestTechnicalIndicatorsList:
    def test_list_indicators(self, client):
        response = client.get("/technical/indicators")
        assert response.status_code == 200
        data = response.json()
        expected_categories = {
            "overbought_oversold",
            "trend",
            "energy",
            "volume",
            "ma",
            "path",
            "other",
        }
        assert set(data.keys()) == expected_categories
        for cat_list in data.values():
            assert isinstance(cat_list, list)
            assert len(cat_list) > 0
