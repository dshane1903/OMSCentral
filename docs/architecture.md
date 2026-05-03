# OMSCS Course Intelligence Architecture

## Goal

Answer course-planning questions for OMSCS students using real student data.
First slice covers OMSCentral reviews; the platform is designed to grow into
a multi-source retrieval system.

## Service Topology

- **API Gateway** — public HTTP entrypoint
- **Ingestion Service** — scrapes OMSCentral and Reddit r/OMSCS, normalizes
  documents, persists them in Postgres, and publishes `document.ingested` events
- **Processing Service** — consumes `document.ingested` events, chunks,
  embeds, writes vectors. Also runs a reconciliation poller as a backstop
- **Embedding Service** — embedding model wrapper (OpenAI or deterministic
  fallback)
- **Retrieval Service** — vector search + LLM orchestration with Redis cache
- **LLM Service** — grounded answer generation
- **Postgres + pgvector** — primary store: course catalog, source documents,
  embedded chunks
- **Redis** — query result cache
- **RabbitMQ** — event bus between ingestion and processing

## Event-Driven Pipeline

Documents flow through a topic exchange with a retry-queue dead-letter pattern:

- `documents` topic exchange, routing key `document.ingested`
- `processing.document.ingested` durable queue, dead-letters to `documents.dlx`
- `documents.dlx` direct exchange with two bindings:
  - `retry` → `processing.document.retry` (TTL 30s, dead-letters back to
    `documents` with the original routing key — message reappears in the main
    queue after the delay)
  - `failed` → `processing.document.failed` (terminal DLQ)
- The consumer counts dead-letterings via the `x-death` header. After
  `MAX_RETRIES` (default 3) it publishes the message to the terminal DLQ
  and acks it off the main queue.

## Consistency Model

The Postgres write is the source of truth. Events are a fast-path
notification — losing one does not lose the work, because the
reconciliation poller scans for documents with `chunk_count = 0`. This gives
us at-least-once processing without a full transactional outbox table:

- DB commit succeeds, publish succeeds → consumer processes via event
- DB commit succeeds, publish fails → poller picks it up within 30s
- DB commit fails → nothing is published, nothing is processed (correct)
- Consumer processes a duplicate event → idempotent: `process_one_document`
  short-circuits when `chunk_count != 0`

## Data Model

- `course_catalog` — one row per OMSCentral course
- `documents` — one row per review or future source document, with
  `chunk_count` driving processing state (0 = needs chunking, -1 = empty,
  positive = chunked)
- `chunks` — embedding rows; ivfflat cosine index for retrieval

## Failure Modes

- **Broker down at publish time** — publishes are best-effort and logged;
  reconciler catches up
- **Broker down at consume time** — consumer reconnects via `connect_robust`;
  events buffer in the durable queue
- **Embedding service down** — handler returns False, message routes through
  retry queue, reattempted after TTL
- **Bad event payload** — message goes directly to terminal DLQ, no retry
- **Document deleted between publish and consume** — handler returns True
  (no work to do), message acked

## Data Sources

### OMSCentral
- catalog discovery via Next.js server payload
- per-course review scraping and normalization
- course metadata extraction (codes, credit hours, syllabus)

### Reddit r/OMSCS
- course-specific search via Reddit's public JSON API
- recent post scraping for general OMSCS discussion
- automatic course matching via course code pattern detection
- post + top comments combined into single documents
- rate-limited to ~1 req/sec (Reddit's unauthenticated rate limit)

## Near-Term Evolution

- add syllabi and grade distribution ingestion
- Prometheus metrics on queue depth, consumer lag, embedding throughput,
  retrieval latency
- citation rendering on retrieved answers
