# OMSCS Course Intelligence Platform

This repo is the start of a course intelligence product for OMSCS students.

The first shipped slice is an `OMSCentral` ingestion path:

- discover the full OMSCentral catalog from the homepage payload
- scrape individual course review pages
- normalize course metadata and review text
- persist source-backed review documents in Postgres
- save per-course JSON snapshots for downstream processing

The existing retrieval, embedding, and LLM services are still here so we can grow this into the full cited Q&A experience next.

## Current Services

- `api-gateway` proxies public requests into the platform
- `ingestion-service` scrapes OMSCentral and stores normalized source documents
- `processing-service` chunks ingested documents, embeds them via the embedding service, and writes retrieval-ready chunks to pgvector (runs as a background poller + manual trigger)
- `embedding-service` exposes embeddings for the later pipeline stages
- `retrieval-service` exposes vector retrieval over stored chunks
- `llm-service` produces grounded answers from retrieved context

## Local Run

```bash
docker compose -f infra/docker-compose.yml up --build
```

Then trigger the first scrape through the gateway:

```bash
curl -X POST http://localhost:8000/sources/omscentral/scrape \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":["software-architecture-and-design"],"persist":true}'
```

You can also run the scraper directly from the ingestion service:

```bash
PYTHONPATH=services/ingestion-service:. python3 services/ingestion-service/app/scrape_omscentral.py \
  --course-slug software-architecture-and-design \
  --persist
```

Snapshots are written under `DOCUMENT_STORAGE_PATH/omscentral/`.

After ingesting, trigger the processing worker to chunk and embed:

```bash
curl -X POST http://localhost:8000/process
```

The processing service also polls automatically every 30 seconds for unchunked documents.

Once documents are chunked and embedded, query the platform:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How hard is CS 6250 if I work full-time?"}'
```

## Next Build Targets

- wire ingestion → processing onto RabbitMQ (event-driven pipeline)
- add Reddit, syllabi, and grade distribution ingestion
- add Prometheus metrics and Grafana dashboards
- deploy to a public host and get real users
- return cited answers for semester planning questions
