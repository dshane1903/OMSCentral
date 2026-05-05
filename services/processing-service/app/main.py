from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.worker import process_one_document, process_unchunked_documents
from shared.schemas.models import DocumentIngestedEvent, ProcessDocumentsRequest
from shared.utils.config import get_settings
from shared.utils.messaging import consume_documents
from shared.utils.observability import instrument_fastapi_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("processing-service")

POLL_INTERVAL_SECONDS = 30

_consumer_task: asyncio.Task | None = None
_poller_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _handle_event(payload: dict[str, Any]) -> bool:
    """RabbitMQ consumer callback. Returns True to ack, False to retry."""
    try:
        event = DocumentIngestedEvent.model_validate(payload)
    except Exception:
        logger.exception("Invalid event payload, dropping: %s", payload)
        # Bad payload: ack so it doesn't loop. The DLQ wrapper handles
        # malformed JSON; this catches valid JSON with the wrong shape.
        return True

    return await process_one_document(event.document_id)


async def _consumer_loop(stop_event: asyncio.Event) -> None:
    """Run the RabbitMQ consumer with restart-on-error backoff."""
    settings = get_settings()
    backoff = 1.0
    while not stop_event.is_set():
        try:
            await consume_documents(
                _handle_event,
                prefetch=settings.rabbitmq_consumer_prefetch,
                stop_event=stop_event,
            )
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Consumer crashed; restarting in %.1fs", backoff)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def _reconciliation_loop(stop_event: asyncio.Event) -> None:
    """
    Backstop poller for documents that never got an event delivered (broker
    outage, dropped publish, etc). The DB is the source of truth, so this
    loop guarantees eventual processing even if RabbitMQ misbehaves.
    """
    while not stop_event.is_set():
        try:
            result = await process_unchunked_documents()
            if result["documents_processed"] > 0:
                logger.info(
                    "Reconciler picked up %d documents, %d chunks",
                    result["documents_processed"],
                    result["chunks_created"],
                )
        except Exception:
            logger.exception("Reconciler error")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _consumer_task, _poller_task, _stop_event
    from shared.utils.db import ensure_schema

    ensure_schema()

    _stop_event = asyncio.Event()
    _consumer_task = asyncio.create_task(
        _consumer_loop(_stop_event), name="rabbitmq-consumer"
    )
    _poller_task = asyncio.create_task(
        _reconciliation_loop(_stop_event), name="reconciler"
    )
    logger.info(
        "Processing service started: rabbitmq consumer + reconciler (every %ds)",
        POLL_INTERVAL_SECONDS,
    )

    try:
        yield
    finally:
        if _stop_event is not None:
            _stop_event.set()
        for task in (_consumer_task, _poller_task):
            if task is not None:
                task.cancel()
        for task in (_consumer_task, _poller_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


app = FastAPI(
    title="OMSCS Processing Service",
    version="0.2.0",
    lifespan=lifespan,
)
instrument_fastapi_app(app, "processing-service")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "processing-service"}


@app.post("/process")
async def trigger_processing(
    request: ProcessDocumentsRequest = ProcessDocumentsRequest(),
) -> dict:
    """Manual trigger: scan the DB for unchunked docs and process them now."""
    total_processed = 0
    total_chunks = 0
    errors = []

    for _ in range(request.max_batches):
        result = await process_unchunked_documents(
            limit=request.limit,
            reprocess=request.reprocess,
            course_slugs=request.course_slugs,
        )
        total_processed += result["documents_processed"]
        total_chunks += result["chunks_created"]
        errors.extend(result["errors"])
        if result["documents_processed"] == 0:
            break

    return {
        "documents_processed": total_processed,
        "chunks_created": total_chunks,
        "errors": errors,
    }


@app.post("/process/{document_id}")
async def trigger_one(document_id: str) -> dict:
    """Manual trigger for a single document, bypassing the queue."""
    ok = await process_one_document(document_id)
    return {"document_id": document_id, "processed": ok}
