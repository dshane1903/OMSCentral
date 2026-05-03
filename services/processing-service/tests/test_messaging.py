"""
Unit tests for the messaging dispatch loop. These do not touch a real broker;
they exercise the retry/DLQ decision logic by feeding fake messages through
the private _dispatch helper.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from shared.utils import messaging


def make_message(payload: dict | bytes, deaths: int = 0):
    """Build a stand-in for AbstractIncomingMessage."""
    if isinstance(payload, dict):
        body = json.dumps(payload).encode("utf-8")
    else:
        body = payload

    headers = {}
    if deaths:
        headers["x-death"] = [{"queue": messaging.PROCESSING_QUEUE, "count": deaths}]

    message = MagicMock()
    message.body = body
    message.headers = headers
    message.content_type = "application/json"
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    return message


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def _run_dispatch(self, message, handler, channel=None):
        if channel is None:
            channel = MagicMock()
            # publish_to_dlq calls declare_exchange().publish(...) on the channel.
            exchange = MagicMock()
            exchange.publish = AsyncMock()
            channel.declare_exchange = AsyncMock(return_value=exchange)
        await messaging._dispatch(channel, message, handler)
        return channel

    async def test_successful_handler_acks_message(self):
        message = make_message({"event": "document.ingested", "document_id": "doc-1"})
        handler = AsyncMock(return_value=True)

        await self._run_dispatch(message, handler)

        handler.assert_awaited_once()
        message.ack.assert_awaited_once()
        message.nack.assert_not_called()

    async def test_failed_handler_nacks_for_retry(self):
        message = make_message({"event": "document.ingested", "document_id": "doc-2"})
        handler = AsyncMock(return_value=False)

        await self._run_dispatch(message, handler)

        message.nack.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_called()

    async def test_handler_raising_is_treated_as_failure(self):
        message = make_message({"event": "document.ingested", "document_id": "doc-3"})
        handler = AsyncMock(side_effect=RuntimeError("embedding service down"))

        await self._run_dispatch(message, handler)

        message.nack.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_called()

    async def test_max_retries_routes_to_dlq_and_acks(self):
        message = make_message(
            {"event": "document.ingested", "document_id": "doc-4"},
            deaths=messaging.MAX_RETRIES,
        )
        handler = AsyncMock(return_value=True)
        channel = await self._run_dispatch(message, handler)

        # Handler must NOT be invoked once we hit the retry budget.
        handler.assert_not_called()
        # Message is acked off the main queue.
        message.ack.assert_awaited_once()
        # And forwarded to the DLX.
        channel.declare_exchange.assert_awaited()
        published_exchange = channel.declare_exchange.return_value
        published_exchange.publish.assert_awaited_once()
        _, kwargs = published_exchange.publish.call_args
        self.assertEqual(kwargs.get("routing_key"), messaging.DLX_FAILED_ROUTING_KEY)

    async def test_malformed_json_goes_directly_to_dlq(self):
        message = make_message(b"not-json{")
        handler = AsyncMock()
        channel = await self._run_dispatch(message, handler)

        handler.assert_not_called()
        message.ack.assert_awaited_once()
        published_exchange = channel.declare_exchange.return_value
        published_exchange.publish.assert_awaited_once()


class DeathCountTests(unittest.TestCase):
    def test_no_header_means_zero(self):
        message = make_message({"document_id": "x"})
        self.assertEqual(messaging.death_count(message), 0)

    def test_sums_counts_across_entries(self):
        message = MagicMock()
        message.headers = {
            "x-death": [
                {"queue": "main", "count": 2},
                {"queue": "retry", "count": 1},
            ]
        }
        self.assertEqual(messaging.death_count(message), 3)

    def test_ignores_malformed_entries(self):
        message = MagicMock()
        message.headers = {"x-death": ["bad", {"count": "not-an-int"}, {"count": 4}]}
        self.assertEqual(messaging.death_count(message), 4)


if __name__ == "__main__":
    unittest.main()
