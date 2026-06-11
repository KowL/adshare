"""Tests for API Key authentication."""

import asyncio

import pytest
from fastapi import HTTPException

from adshare.core.auth import APIKeyAuth, get_api_key, verify_api_key


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestGetApiKey:
    def test_header_key(self):
        key = _run(get_api_key(header_key="test-key", query_key=None))
        assert key == "test-key"

    def test_query_key(self):
        key = _run(get_api_key(header_key=None, query_key="query-key"))
        assert key == "query-key"

    def test_header_overrides_query(self):
        key = _run(get_api_key(header_key="header-key", query_key="query-key"))
        assert key == "header-key"

    def test_no_key_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _run(get_api_key(header_key=None, query_key=None))
        assert exc_info.value.status_code == 401


class TestAPIKeyAuth:
    def test_disabled_auth_allows_any_key(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        auth = APIKeyAuth(enabled=False)
        result = _run(auth(api_key="any-key"))
        assert result == "any-key"

    def test_enabled_auth_validates_key(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("ADSHARE_API_KEY", "secret-key")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        auth = APIKeyAuth(enabled=True)
        result = _run(auth(api_key="secret-key"))
        assert result == "secret-key"

    def test_invalid_key_raises_403(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("ADSHARE_API_KEY", "secret-key")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        auth = APIKeyAuth(enabled=True)
        with pytest.raises(HTTPException) as exc_info:
            _run(auth(api_key="wrong-key"))
        assert exc_info.value.status_code == 403

    def test_no_server_key_raises_500(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.delenv("ADSHARE_API_KEY", raising=False)
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        auth = APIKeyAuth(enabled=True)
        with pytest.raises(HTTPException) as exc_info:
            _run(auth(api_key="any-key"))
        assert exc_info.value.status_code == 500


class TestVerifyApiKey:
    def test_auth_disabled_passes(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        result = _run(verify_api_key(api_key="any-key"))
        assert result == "any-key"

    def test_auth_enabled_valid_key(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("ADSHARE_API_KEY", "secret")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        result = _run(verify_api_key(api_key="secret"))
        assert result == "secret"

    def test_auth_enabled_invalid_key(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("ADSHARE_API_KEY", "secret")
        from adshare.core.config import get_settings
        get_settings.cache_clear()

        with pytest.raises(HTTPException) as exc_info:
            _run(verify_api_key(api_key="wrong"))
        assert exc_info.value.status_code == 403
