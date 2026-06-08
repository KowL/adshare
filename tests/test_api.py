"""Tests for API endpoints."""


class TestHealth:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "redis_connected" in data

    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "adshare"
        assert "docs" in data


class TestTechnical:
    def test_indicators_list(self, client):
        response = client.get("/technical/indicators")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        total = sum(len(v) for v in data.values())
        assert total == 56


class TestFundamental:
    def test_factors_list(self, client):
        response = client.get("/fundamental/factors")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        total = sum(v["count"] for v in data.values())
        assert total == 90


class TestFactor:
    def test_capabilities(self, client):
        response = client.get("/factor/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "preprocessing" in data
        assert "analysis" in data


class TestMetrics:
    def test_metrics_endpoint(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "adshare_info" in response.text
