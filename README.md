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
- `retrieval-service` — hybrid dense-sparse retrieval over chunks, calls the
  LLM service with retrieved context, caches answers in Redis
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

Run the frontend in another shell:

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on http://localhost:5173 and talks to the API gateway at
http://localhost:8000 by default. Override `VITE_API_BASE_URL` for other API
targets.

Trigger a scrape:

```bash
curl -X POST http://localhost:8000/sources/omscentral/scrape \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":["software-architecture-and-design"],"persist":true}'
```

Each persisted review will produce a `document.ingested` event that the
processing service picks up automatically.

Backfill or refresh course data without returning every review in the HTTP
response:

```bash
# Index every course that does not already have chunks
curl -X POST http://localhost:8000/index/courses \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":[],"missing_only":true,"include_reviews":true,"process_after":false}'

# Check job status
curl http://localhost:8000/index/jobs/<job_id>
```

Use `course_slugs` to index a specific course, or set `missing_only` to `false`
for a full refresh. The processing worker and reconciliation poller chunk and
embed persisted documents in the background.

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
# Process every unchunked document now, one batch
curl -X POST http://localhost:8005/process \
  -H "Content-Type: application/json" \
  -d '{"limit":50,"max_batches":1}'

# Process a specific document by id
curl -X POST http://localhost:8005/process/<document_id>
```

Once chunks are embedded, ask a question:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How hard is CS 6250 if I work full-time?"}'
```

## Retrieval Evaluation

The retrieval service uses hybrid dense-sparse retrieval:

- dense search: pgvector cosine similarity over chunk embeddings
- sparse search: Postgres full-text search over chunk text
- fusion: Reciprocal Rank Fusion (RRF)

Run a small retrieval eval against a running local stack:

```bash
PYTHONPATH=services/retrieval-service:. \
  python3 eval/run_retrieval_eval.py \
  --questions eval/questions.example.jsonl \
  --mode hybrid \
  --top-k 5
```

Use `--mode dense` to compare against dense-only retrieval. Add labeled
questions to the JSONL file with `relevant_course_slugs` and/or
`relevant_document_ids`.

The RabbitMQ management UI is exposed on http://localhost:15672
(user: `rag`, password: `rag`) — useful for inspecting queue depth, the
DLQ, and message rates while developing.

## Observability

Every FastAPI service exposes Prometheus metrics at `/metrics`. The local
compose stack also starts Prometheus and Grafana:

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (user: `admin`, password: `admin`)
- RabbitMQ metrics: http://localhost:15692/metrics

Grafana is provisioned with the Prometheus datasource and an `OMSCS Service
Overview` dashboard covering request rate, 5xx rate, p95 latency, in-flight
requests, RabbitMQ queue depth, and scrape health.

Application-level metrics include scrape runs, persisted documents, RabbitMQ
publish outcomes, processed documents, chunks created, embedding batches/texts,
query latency, retrieval cache hits/misses, and LLM generation outcomes.

## Deployment

Deployment planning lives in [docs/deployment.md](docs/deployment.md). The
recommended production path is AWS ECS Fargate with RDS Postgres, ElastiCache
Redis, Amazon MQ, private Prometheus/Grafana, Terraform, and GitHub Actions.

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

- deploy to a public host and put it in front of OMSCS students
- citation rendering on retrieved answers
