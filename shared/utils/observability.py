from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REQUEST_COUNT = Counter(
    "omscs_http_requests_total",
    "Total HTTP requests handled by OMSCS services.",
    ["service", "method", "path", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "omscs_http_request_duration_seconds",
    "HTTP request latency for OMSCS services.",
    ["service", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

REQUESTS_IN_PROGRESS = Gauge(
    "omscs_http_requests_in_progress",
    "HTTP requests currently in progress for OMSCS services.",
    ["service", "method"],
)

SERVICE_INFO = Gauge(
    "omscs_service_info",
    "Static service metadata for OMSCS services.",
    ["service", "version"],
)

SCRAPE_RUNS = Counter(
    "omscs_scrape_runs_total",
    "Scrape requests by source and outcome.",
    ["source", "status"],
)

DOCUMENTS_PERSISTED = Counter(
    "omscs_documents_persisted_total",
    "Documents persisted by source.",
    ["source"],
)

DOCUMENT_EVENTS_PUBLISHED = Counter(
    "omscs_document_events_published_total",
    "Document ingestion events published to RabbitMQ by source and outcome.",
    ["source", "status"],
)

PROCESSING_DOCUMENTS = Counter(
    "omscs_processing_documents_total",
    "Documents processed by the processing worker.",
    ["status"],
)

PROCESSING_CHUNKS_CREATED = Counter(
    "omscs_processing_chunks_created_total",
    "Chunks created by the processing worker.",
)

EMBEDDING_BATCHES = Counter(
    "omscs_embedding_batches_total",
    "Embedding batches requested by outcome.",
    ["service", "status"],
)

EMBEDDING_TEXTS = Counter(
    "omscs_embedding_texts_total",
    "Texts submitted for embedding by outcome.",
    ["service", "status"],
)

QUERY_REQUESTS = Counter(
    "omscs_query_requests_total",
    "RAG query requests by outcome.",
    ["status"],
)

QUERY_LATENCY = Histogram(
    "omscs_query_duration_seconds",
    "End-to-end RAG query latency.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

RETRIEVAL_CACHE_EVENTS = Counter(
    "omscs_retrieval_cache_events_total",
    "Retrieval cache hit/miss events.",
    ["result"],
)

LLM_REQUESTS = Counter(
    "omscs_llm_requests_total",
    "Answer generation requests by provider and outcome.",
    ["provider", "status"],
)


def instrument_fastapi_app(app: FastAPI, service_name: str) -> None:
    """Expose /metrics and add low-cardinality HTTP metrics to a FastAPI app."""
    if getattr(app.state, "observability_configured", False):
        return

    app.state.observability_configured = True
    SERVICE_INFO.labels(service=service_name, version=app.version).set(1)

    @app.middleware("http")
    async def prometheus_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = _route_path(request)
        start = time.perf_counter()
        REQUESTS_IN_PROGRESS.labels(
            service=service_name,
            method=method,
        ).inc()

        try:
            response = await call_next(request)
        except Exception:
            path = _route_path(request)
            REQUEST_COUNT.labels(
                service=service_name,
                method=method,
                path=path,
                status_code="500",
            ).inc()
            REQUEST_LATENCY.labels(
                service=service_name,
                method=method,
                path=path,
            ).observe(time.perf_counter() - start)
            raise
        else:
            path = _route_path(request)
            REQUEST_COUNT.labels(
                service=service_name,
                method=method,
                path=path,
                status_code=str(response.status_code),
            ).inc()
            REQUEST_LATENCY.labels(
                service=service_name,
                method=method,
                path=path,
            ).observe(time.perf_counter() - start)
            return response
        finally:
            REQUESTS_IN_PROGRESS.labels(
                service=service_name,
                method=method,
            ).dec()

    @app.get("/metrics", include_in_schema=False)
    @app.get("/metrics/", include_in_schema=False)
    def metrics() -> Response:
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return request.url.path
