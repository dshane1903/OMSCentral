from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from shared.utils.config import get_settings
from shared.utils.observability import DOCUMENT_EVENTS_PUBLISHED

logger = logging.getLogger("messaging")

DOCUMENTS_EXCHANGE = "documents"
DOCUMENTS_DLX = "documents.dlx"

DOCUMENT_INGESTED_ROUTING_KEY = "document.ingested"

PROCESSING_QUEUE = "processing.document.ingested"
PROCESSING_RETRY_QUEUE = "processing.document.retry"
PROCESSING_DLQ = "processing.document.failed"

DLX_RETRY_ROUTING_KEY = "retry"
DLX_FAILED_ROUTING_KEY = "failed"

MAX_RETRIES = 3


def amqp_url() -> str:
    settings = get_settings()
    return (
        f"{settings.rabbitmq_scheme}://{settings.rabbitmq_user}:{settings.rabbitmq_password}"
        f"@{settings.rabbitmq_host}:{settings.rabbitmq_port}/"
    )


async def connect() -> AbstractRobustConnection:
    """Open a robust (auto-reconnecting) connection to RabbitMQ."""
    return await aio_pika.connect_robust(amqp_url())


async def declare_topology(channel: aio_pika.abc.AbstractChannel) -> None:
    """
    Declare exchanges and queues used by the document processing pipeline.

    Topology:

        documents (topic exchange)
          |  (document.ingested)
          v
        processing.document.ingested  -- failure (nack) -->  documents.dlx (direct)
                                                                 |
                                              retry  ----------->|<------- failed
                                                |                                |
                                                v                                v
                                  processing.document.retry            processing.document.failed
                                  (TTL=30s, dead-letters back
                                   to documents exchange with
                                   document.ingested key)

    The function is idempotent and safe to call from every service that needs
    to interact with the queues.
    """
    settings = get_settings()
    retry_ms = settings.rabbitmq_retry_delay_ms

    documents_exchange = await channel.declare_exchange(
        DOCUMENTS_EXCHANGE,
        ExchangeType.TOPIC,
        durable=True,
    )
    dlx = await channel.declare_exchange(
        DOCUMENTS_DLX,
        ExchangeType.DIRECT,
        durable=True,
    )

    main_queue = await channel.declare_queue(
        PROCESSING_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": DOCUMENTS_DLX,
            "x-dead-letter-routing-key": DLX_RETRY_ROUTING_KEY,
        },
    )
    await main_queue.bind(documents_exchange, DOCUMENT_INGESTED_ROUTING_KEY)

    retry_queue = await channel.declare_queue(
        PROCESSING_RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": retry_ms,
            "x-dead-letter-exchange": DOCUMENTS_EXCHANGE,
            "x-dead-letter-routing-key": DOCUMENT_INGESTED_ROUTING_KEY,
        },
    )
    await retry_queue.bind(dlx, DLX_RETRY_ROUTING_KEY)

    dlq = await channel.declare_queue(
        PROCESSING_DLQ,
        durable=True,
    )
    await dlq.bind(dlx, DLX_FAILED_ROUTING_KEY)


def death_count(message: AbstractIncomingMessage) -> int:
    """
    Count the number of times a message has been dead-lettered.

    RabbitMQ adds an x-death header to dead-lettered messages. Each entry in
    the array represents a queue the message has been dead-lettered from. The
    count field on each entry is the number of dead-letterings from that
    queue. Summing those counts gives a stable retry counter that survives
    queue cycling.
    """
    death_header = message.headers.get("x-death") if message.headers else None
    if not death_header:
        return 0

    total = 0
    for entry in death_header:
        if isinstance(entry, dict):
            count = entry.get("count", 0)
            if isinstance(count, int):
                total += count
    return total


@asynccontextmanager
async def publisher_channel() -> AsyncIterator[aio_pika.abc.AbstractChannel]:
    """Open a publisher channel with topology declared."""
    connection = await connect()
    try:
        channel = await connection.channel()
        await declare_topology(channel)
        yield channel
    finally:
        await connection.close()


async def publish_document_ingested(document_id: str) -> None:
    """
    Publish a document.ingested event.

    Best-effort: if the broker is unavailable the failure is logged and the
    reconciliation poller will pick the document up by scanning the database.
    The database write is the source of truth, the event is a fast-path
    notification.
    """
    payload = {
        "event": "document.ingested",
        "document_id": document_id,
    }
    body = json.dumps(payload).encode("utf-8")
    message = Message(
        body=body,
        delivery_mode=DeliveryMode.PERSISTENT,
        content_type="application/json",
    )

    try:
        async with publisher_channel() as channel:
            exchange = await channel.declare_exchange(
                DOCUMENTS_EXCHANGE,
                ExchangeType.TOPIC,
                durable=True,
            )
            await exchange.publish(
                message,
                routing_key=DOCUMENT_INGESTED_ROUTING_KEY,
            )
        DOCUMENT_EVENTS_PUBLISHED.labels(
            source=_source_from_document_id(document_id),
            status="success",
        ).inc()
        logger.debug("Published document.ingested for %s", document_id)
    except Exception:
        DOCUMENT_EVENTS_PUBLISHED.labels(
            source=_source_from_document_id(document_id),
            status="failure",
        ).inc()
        logger.exception("Failed to publish document.ingested for %s", document_id)


async def publish_to_dlq(
    channel: aio_pika.abc.AbstractChannel,
    original: AbstractIncomingMessage,
    reason: str,
) -> None:
    """Forward a poison message to the failed queue with a reason header."""
    dlx = await channel.declare_exchange(
        DOCUMENTS_DLX,
        ExchangeType.DIRECT,
        durable=True,
    )
    headers: dict[str, Any] = dict(original.headers or {})
    headers["x-failure-reason"] = reason

    message = Message(
        body=original.body,
        delivery_mode=DeliveryMode.PERSISTENT,
        content_type=original.content_type or "application/json",
        headers=headers,
    )
    await dlx.publish(message, routing_key=DLX_FAILED_ROUTING_KEY)


HandlerResult = bool  # True = ack, False = nack/retry


async def consume_documents(
    handler: Callable[[dict[str, Any]], Awaitable[HandlerResult]],
    *,
    prefetch: int = 4,
    stop_event: asyncio.Event | None = None,
) -> None:
    """
    Consume document.ingested events and dispatch them to the handler.

    The handler is called with the parsed JSON payload. It must return True on
    success (the message is acked) and False (or raise) on failure.

    Failure handling:
      * On the first MAX_RETRIES failures the message is nacked without
        requeueing, which routes it through the DLX into the retry queue.
        After the TTL, the retry queue dead-letters it back to the main queue
        for another attempt.
      * On the (MAX_RETRIES + 1)-th delivery the message is published to the
        terminal DLQ instead and acked off the main queue so it does not loop
        forever.
    """
    connection = await connect()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=prefetch)
    await declare_topology(channel)

    queue = await channel.declare_queue(
        PROCESSING_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": DOCUMENTS_DLX,
            "x-dead-letter-routing-key": DLX_RETRY_ROUTING_KEY,
        },
    )

    logger.info("Consumer started on %s (prefetch=%d)", PROCESSING_QUEUE, prefetch)

    async with queue.iterator() as iterator:
        async for message in iterator:
            if stop_event is not None and stop_event.is_set():
                break
            await _dispatch(channel, message, handler)

    await connection.close()


async def _dispatch(
    channel: aio_pika.abc.AbstractChannel,
    message: AbstractIncomingMessage,
    handler: Callable[[dict[str, Any]], Awaitable[HandlerResult]],
) -> None:
    deaths = death_count(message)

    try:
        payload = json.loads(message.body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception("Malformed message; sending directly to DLQ")
        await publish_to_dlq(channel, message, "malformed-json")
        await message.ack()
        return

    if deaths >= MAX_RETRIES:
        logger.error(
            "Message exceeded retry budget (deaths=%d), sending to DLQ: %s",
            deaths,
            payload,
        )
        await publish_to_dlq(channel, message, f"max-retries-exceeded:{deaths}")
        await message.ack()
        return

    try:
        ok = await handler(payload)
    except Exception as exc:
        logger.exception("Handler raised for %s: %s", payload, exc)
        ok = False

    if ok:
        await message.ack()
    else:
        # Reject without requeue so it flows through the DLX into the retry queue.
        await message.nack(requeue=False)


def _source_from_document_id(document_id: str) -> str:
    if document_id.startswith("omscentral-"):
        return "omscentral"
    if document_id.startswith("reddit-"):
        return "reddit"
    return "unknown"
