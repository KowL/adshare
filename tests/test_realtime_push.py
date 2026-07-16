"""Tests for real-time push (WebSocket + SSE + broadcast service)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import WebSocket

from adshare.services.realtime_broadcast import (
    CHANNEL_QUOTE,
    RealtimeBroadcastService,
    SSEClientQueue,
    get_broadcast_service,
)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def broadcast_service(monkeypatch):
    """Provide a fresh RealtimeBroadcastService with mocked Redis."""
    # Reset singleton
    import adshare.services.realtime_broadcast as _rb_mod

    _rb_mod._broadcast_service = None

    service = get_broadcast_service()

    # Mock Redis pubsub
    service._pubsub = MagicMock()
    service._pubsub.get_message = MagicMock(return_value=None)
    service._pubsub.subscribe = MagicMock()
    service._pubsub.unsubscribe = MagicMock()
    service._pubsub.close = MagicMock()

    # Mock cache manager redis
    mock_redis = MagicMock()
    mock_redis.pubsub = MagicMock(return_value=service._pubsub)

    with patch(
        "adshare.services.realtime_broadcast.get_cache_manager"
    ) as mock_cache:
        mock_cache.return_value.redis = mock_redis
        yield service

    # Cleanup
    _rb_mod._broadcast_service = None


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket that records sent messages."""
    ws = MagicMock(spec=WebSocket)
    ws.sent_messages: list = []

    async def _send_json(data):
        ws.sent_messages.append(data)

    ws.send_json = _send_json
    return ws


# ============================================================
# Broadcast Service Unit Tests
# ============================================================


class TestBroadcastServiceLifecycle:
    """Test RealtimeBroadcastService start/stop."""

    @pytest.mark.asyncio
    async def test_start_subscribes_to_channels(self, broadcast_service):
        """start() should subscribe to quote, index, and kline channels."""
        service = broadcast_service
        with patch(
            "adshare.services.realtime_broadcast.get_cache_manager"
        ) as mock_cache:
            mock_redis = MagicMock()
            mock_pubsub = MagicMock()
            mock_pubsub.get_message = MagicMock(return_value=None)
            mock_pubsub.subscribe = MagicMock()
            mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
            mock_cache.return_value.redis = mock_redis

            await service.start()

            mock_pubsub.subscribe.assert_called_once()
            call_args = mock_pubsub.subscribe.call_args[0]
            assert CHANNEL_QUOTE in call_args
            assert "adshare:realtime:index" in call_args

            # Clean up
            service._listen_task.cancel()
            try:
                await service._listen_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_stop_cancels_listen_task(self, broadcast_service):
        """stop() should cancel the listen task and close pubsub."""
        service = broadcast_service
        service._listen_task = asyncio.create_task(asyncio.sleep(60))
        service._pubsub = MagicMock()

        await service.stop()

        assert service._listen_task.cancelled()
        service._pubsub.unsubscribe.assert_called_once()
        service._pubsub.close.assert_called_once()


class TestBroadcastWs:
    """Test WebSocket broadcast logic."""

    @pytest.mark.asyncio
    async def test_broadcast_ws_sends_to_subscribers(
        self, broadcast_service, mock_websocket
    ):
        """Message should be sent to all subscribers of the code."""
        service = broadcast_service
        client_id = "ws_test01"

        # Register mock WebSocket
        service.ws_manager._connections[client_id] = mock_websocket
        service.ws_manager._subscriptions[client_id] = {"000001.SZ"}
        service.ws_manager._code_subscribers["000001.SZ"] = {client_id}

        payload = {"type": "quote", "code": "000001.SZ", "data": {"price": 10.5}}
        await service._broadcast_ws("000001.SZ", payload)

        assert len(mock_websocket.sent_messages) == 1
        assert mock_websocket.sent_messages[0]["code"] == "000001.SZ"
        service.ws_manager.disconnect(client_id)

    @pytest.mark.asyncio
    async def test_broadcast_ws_skips_unsubscribed_clients(
        self, broadcast_service, mock_websocket
    ):
        """Message should not be sent to clients not subscribed to the code."""
        service = broadcast_service
        client_id = "ws_test02"

        service.ws_manager._connections[client_id] = mock_websocket
        service.ws_manager._subscriptions[client_id] = {"600000.SH"}  # different code
        service.ws_manager._code_subscribers["600000.SH"] = {client_id}

        payload = {"type": "quote", "code": "000001.SZ", "data": {"price": 10.5}}
        await service._broadcast_ws("000001.SZ", payload)

        assert len(mock_websocket.sent_messages) == 0
        service.ws_manager.disconnect(client_id)

    @pytest.mark.asyncio
    async def test_broadcast_ws_disconnects_failed_clients(
        self, broadcast_service
    ):
        """Clients that raise on send_json should be disconnected."""
        service = broadcast_service
        client_id = "ws_test03"

        bad_ws = MagicMock(spec=WebSocket)

        async def _raise(*args, **kwargs):
            raise RuntimeError("Connection closed")

        bad_ws.send_json = _raise

        service.ws_manager._connections[client_id] = bad_ws
        service.ws_manager._subscriptions[client_id] = {"000001.SZ"}
        service.ws_manager._code_subscribers["000001.SZ"] = {client_id}

        payload = {"type": "quote", "code": "000001.SZ"}
        await service._broadcast_ws("000001.SZ", payload)

        assert client_id not in service.ws_manager._connections


class TestBroadcastSse:
    """Test SSE broadcast logic."""

    @pytest.mark.asyncio
    async def test_broadcast_sse_puts_to_queue(self, broadcast_service):
        """Message should be put into the SSE client's queue."""
        service = broadcast_service
        codes = {"000001.SZ"}
        queue = service.register_sse_client(codes)

        payload = {"type": "quote", "code": "000001.SZ", "data": {"price": 10.5}}
        await service._broadcast_sse("000001.SZ", payload)

        assert not queue.empty()
        msg = queue.get_nowait()
        assert msg["code"] == "000001.SZ"

        service.unregister_sse_client(queue.client_id)

    @pytest.mark.asyncio
    async def test_broadcast_sse_skips_unsubscribed_codes(self, broadcast_service):
        """Message should not be put into queues for unsubscribed codes."""
        service = broadcast_service
        codes = {"600000.SH"}
        queue = service.register_sse_client(codes)

        payload = {"type": "quote", "code": "000001.SZ"}
        await service._broadcast_sse("000001.SZ", payload)

        assert queue.empty()
        service.unregister_sse_client(queue.client_id)

    @pytest.mark.asyncio
    async def test_broadcast_sse_full_queue_warns(self, broadcast_service, caplog):
        """Full queue should log a warning and not block."""
        service = broadcast_service
        codes = {"000001.SZ"}
        queue = service.register_sse_client(codes)

        # Fill the queue
        for _ in range(1000):
            queue.put_nowait({"dummy": True})

        payload = {"type": "quote", "code": "000001.SZ"}
        await service._broadcast_sse("000001.SZ", payload)

        assert "SSE queue full" in caplog.text
        service.unregister_sse_client(queue.client_id)


class TestSseClientLifecycle:
    """Test SSE client registration and unregistration."""

    def test_register_sse_client_returns_queue(self, broadcast_service):
        """register_sse_client should return a queue with subscribed codes."""
        service = broadcast_service
        codes = {"000001.SZ", "600000.SH"}
        queue = service.register_sse_client(codes)

        assert isinstance(queue, SSEClientQueue)
        assert queue.subscribed_codes == codes
        assert queue.client_id.startswith("sse_")
        assert service.stats["sse_connections"] == 1

    def test_unregister_sse_client_removes_queue(self, broadcast_service):
        """unregister_sse_client should remove the client queue."""
        service = broadcast_service
        codes = {"000001.SZ"}
        queue = service.register_sse_client(codes)
        client_id = queue.client_id

        service.unregister_sse_client(client_id)
        assert client_id not in service._sse_queues
        assert service.stats["sse_connections"] == 0


class TestBroadcastServiceStats:
    """Test statistics collection."""

    def test_get_stats_returns_combined(self, broadcast_service):
        """get_stats should combine service stats with ws_manager stats."""
        service = broadcast_service
        stats = service.get_stats()

        assert "ws_active_connections" in stats
        assert "ws_subscribed_codes" in stats
        assert "ws_total_subscriptions" in stats
        assert "ws_broadcasts" in stats
        assert "sse_connections" in stats
        assert "sse_broadcasts" in stats
        assert "redis_messages" in stats
        assert "start_time" in stats


# ============================================================
# WebSocket Integration Tests
# ============================================================


class TestRealtimeWebSocket:
    """Test WebSocket endpoint via TestClient."""

    def test_websocket_connect_and_subscribe(self, client):
        """Client should connect, receive connected message, subscribe successfully."""
        with client.websocket_connect("/realtime/ws") as ws:
            # Receive connected message
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert "client_id" in msg

            # Subscribe
            ws.send_json({"action": "subscribe", "codes": ["000001.SZ"]})
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert msg["codes"] == ["000001.SZ"]
            assert msg["count"] == 1

    def test_websocket_ping_pong(self, client):
        """Ping should receive pong response."""
        with client.websocket_connect("/realtime/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"

    def test_websocket_unsubscribe(self, client):
        """Unsubscribe should clear all subscriptions."""
        with client.websocket_connect("/realtime/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json({"action": "subscribe", "codes": ["000001.SZ"]})
            ws.receive_json()  # subscribed

            ws.send_json({"action": "unsubscribe"})
            msg = ws.receive_json()
            assert msg["type"] == "unsubscribed"

    def test_websocket_invalid_json(self, client):
        """Invalid JSON should receive error message."""
        with client.websocket_connect("/realtime/ws") as ws:
            ws.receive_json()  # connected

            ws.send_text("not json")
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Invalid JSON" in msg["message"]

    def test_websocket_empty_codes(self, client):
        """Empty codes list should receive error."""
        with client.websocket_connect("/realtime/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json({"action": "subscribe", "codes": []})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "non-empty list" in msg["message"]

    def test_websocket_unknown_action(self, client):
        """Unknown action should receive error."""
        with client.websocket_connect("/realtime/ws") as ws:
            ws.receive_json()  # connected

            ws.send_json({"action": "foobar"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Unknown action" in msg["message"]


# ============================================================
# SSE Integration Tests
# ============================================================


class TestRealtimeSse:
    """Test SSE endpoint via TestClient."""

    def test_sse_endpoint_requires_codes(self, client):
        """SSE endpoint without codes should fail validation."""
        response = client.get("/realtime/sse")
        assert response.status_code == 422


# ============================================================
# Redis Pub/Sub Listen Loop Tests
# ============================================================


class TestListenLoop:
    """Test the Redis Pub/Sub listen loop."""

    @pytest.mark.asyncio
    async def test_listen_loop_processes_valid_message(
        self, broadcast_service, mock_websocket
    ):
        """A valid Pub/Sub message should be broadcast to subscribers."""
        service = broadcast_service
        client_id = "ws_loop01"

        service.ws_manager._connections[client_id] = mock_websocket
        service.ws_manager._subscriptions[client_id] = {"000001.SZ"}
        service.ws_manager._code_subscribers["000001.SZ"] = {client_id}

        # Simulate a Pub/Sub message
        payload = {"type": "quote", "code": "000001.SZ", "data": {"price": 10.5}}
        service._pubsub.get_message = MagicMock(
            side_effect=[
                {"type": "message", "data": json.dumps(payload)},
                None,  # timeout on next iteration → break loop
            ]
        )

        # Run one iteration of the listen loop
        try:
            await asyncio.wait_for(service._listen_loop(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert len(mock_websocket.sent_messages) == 1
        assert mock_websocket.sent_messages[0]["code"] == "000001.SZ"
        service.ws_manager.disconnect(client_id)

    @pytest.mark.asyncio
    async def test_listen_loop_skips_non_message(self, broadcast_service):
        """Non-message Pub/Sub events (subscribe, unsubscribe) should be skipped."""
        service = broadcast_service
        service._pubsub.get_message = MagicMock(
            side_effect=[
                {"type": "subscribe", "channel": "adshare:realtime:quote"},
                None,
            ]
        )

        try:
            await asyncio.wait_for(service._listen_loop(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert service.stats["redis_messages"] == 0

    @pytest.mark.asyncio
    async def test_listen_loop_handles_invalid_json(self, broadcast_service, caplog):
        """Invalid JSON in Pub/Sub message should be logged and skipped."""
        service = broadcast_service
        service._pubsub.get_message = MagicMock(
            side_effect=[
                {"type": "message", "data": "not json"},
                None,
            ]
        )

        try:
            await asyncio.wait_for(service._listen_loop(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert "Invalid JSON" in caplog.text
        assert service.stats["redis_messages"] == 1

    @pytest.mark.asyncio
    async def test_listen_loop_handles_cancelled(self, broadcast_service):
        """CancelledError should break the loop cleanly."""
        service = broadcast_service
        service._pubsub.get_message = MagicMock(
            side_effect=asyncio.CancelledError()
        )

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(service._listen_loop(), timeout=1.0)
