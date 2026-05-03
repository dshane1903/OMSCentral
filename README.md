# OMSCS Course Intelligence Platform

A retrieval-augmented Q&A platform for OMSCS students. Ingests course
reviews, embeds them, and answers natural-language questions like
"How hard is CS 6250 if I work full-time?" with citations.

## Current Services

- `api-gateway` — public HTTP entrypoint, proxies to internal services
- `ingestion-service` — scrapes OMSCentral and Reddit r/OMSCS, persists
  normalized review documents in Postgres, and publishes `document.ingested`
  events to RabbitMQ
- `processing-service` — consumes `document.ingested` events, chunks the
  document content, calls the embedding service, and writes retrieval-ready
  chunks to pgvector. Also runs a reconciliation poller that picks up any
  documents whose events were dropped (broker outage, etc.)
- `embedding-service` — wraps OpenAI embeddings (with a deterministic
  fallback for local dev without an API key)
- `retrieval-service` — vector search over chunks, calls the LLM service
  with retrieved context, caches answers in Redis
- `llm-service` — grounded answer generation against retrieved context

## Event-Driven Pipeline

Ingestion and processing are wired through RabbitMQ:

```
ingestion ──publish──▶ documents (topic exchange)
                          │  routing key: document.ingested
                          ▼
                processing.document.ingested  ◀────────┐
                          │                            │
              consumer fails (nack, no requeue)        │ TTL=30s, then dead-letter
                          ▼                            │ back to documents exchange
                  documents.dlx (direct)               │
                   │                                   │
        ┌──────────┴──────────┐                        │
        ▼ retry               ▼ failed                 │
  processing.document.retry   processing.document.failed
        │                                              │
        └──────────────────────────────────────────────┘
```

- The Postgres write is the source of truth. The event is a fast-path
  notification to the consumer.
- Failed deliveries are nacked without requeue, which routes them through
  the DLX into the retry queue for a delayed retry. After `MAX_RETRIES`
  cycles the message is moved to the terminal DLQ instead of looping.
- The reconciliation poller in `processing-service` scans Postgres for
  unchunked documents every 30 seconds, so missing events never cause
  permanent data loss.

## Local Run

```bash
docker compose -f infra/docker-compose.yml up --build
```

Trigger a scrape:

```bash
curl -X POST http://localhost:8000/sources/omscentral/scrape \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":["software-architecture-and-design"],"persist":true}'
```

Each persisted review will produce a `document.ingested` event that the
processing service picks up automatically.

Scrape Reddit r/OMSCS discussions:

```bash
# Scrape recent posts + course-specific discussions
curl -X POST http://localhost:8000/sources/reddit/scrape \
  -H "Content-Type: application/json" \
  -d '{"include_recent":true,"recent_limit":25,"persist":true}'

# Scrape posts about specific courses
curl -X POST http://localhost:8000/sources/reddit/scrape \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":["computer-networks"],"posts_per_course":10,"persist":true}'
```

Reddit posts flow through the same event-driven pipeline — each persisted
post publishes a `document.ingested` event, gets chunked and embedded
automatically. You can also force processing synchronously:

```bash
# Process every unchunked document now
curl -X POST http://localhost:8005/process

# Process a specific document by id
curl -X POST http://localhost:8005/process/<document_id>
```

Once chunks are embedded, ask a question:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How hard is CS 6250 if I work full-time?"}'
```

The RabbitMQ management UI is exposed on http://localhost:15672
(user: `rag`, password: `rag`) — useful for inspecting queue depth, the
DLQ, and message rates while developing.

## Tests

```bash
PYTHONPATH=services/ingestion-service:. \
  python3 -m unittest services.ingestion-service.tests.test_omscentral

PYTHONPATH=services/ingestion-service:. \
  python3 -m unittest services.ingestion-service.tests.test_reddit

PYTHONPATH=. \
  python3 -m unittest services.processing-service.tests.test_messaging
```

## Next Build Targets

- add Prometheus metrics on every service and Grafana dashboards
- deploy to a public host and put it in front of OMSCS students
- citation rendering on retrieved answers
