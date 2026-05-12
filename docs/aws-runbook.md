# AWS Deployment Runbook

This is the concrete path for the first real deployment. The goal is not a
perfect platform on day one; it is a small, reproducible production shape that
keeps public traffic away from databases, queues, metrics endpoints, and admin
backfill endpoints.

## Target Shape

- Frontend: S3 + CloudFront, Amplify Hosting, or Vercel
- Public backend: one Application Load Balancer target for `api-gateway`
- Private services: ECS Fargate services for ingestion, processing, embedding,
  retrieval, and LLM
- Database: RDS Postgres with pgvector enabled
- Cache: ElastiCache Redis
- Queue: Amazon MQ RabbitMQ
- Secrets: AWS Secrets Manager or SSM Parameter Store
- Logs: CloudWatch Logs
- Scheduled refresh: EventBridge Scheduler hitting protected admin endpoints

Only the frontend and `api-gateway` should be public.

## Current Production Snapshot

- Frontend: `https://omscslens.com`
- CloudFront fallback: `https://d38rezjkrvukjs.cloudfront.net`
- Backend ALB: `http://omscentral-api-alb-185117391.us-east-1.elb.amazonaws.com`
- ECS cluster: `omscentral-prod`
- RDS instance: `omscentral-postgres`
- Redis cluster: `omscentral-redis`
- Alert topic: `omscentral-production-alerts`
- CloudFront distribution: `E24NTULC7IV4RP`

Route 53 hosts the public zone for `omscslens.com`, with the apex domain pointed
at CloudFront. `www.omscslens.com` is not currently attached to CloudFront.

## Preflight

Run these before building images:

```bash
git grep -n "replace-me" .env.example
docker compose -f infra/docker-compose.yml config --quiet

cd frontend
npm run build
```

Also run a secret scan before every push. The important part is that real API
keys never appear in `.env.example`, docs, services, shared code, or CI files.

Run the Python test commands from `README.md` or CI before pushing deployment
changes.

## Required Runtime Values

Store real values in Secrets Manager, SSM Parameter Store, or the hosting
provider secret store. Do not bake them into images.

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY` when `LLM_PROVIDER=anthropic`
- `ADMIN_API_KEY`
- `POSTGRES_PASSWORD`
- `RABBITMQ_PASSWORD`
- Grafana admin password if Grafana is deployed

Production environment values that are not usually secret:

- `POSTGRES_HOST`: RDS endpoint
- `POSTGRES_PORT`: `5432`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `REDIS_HOST`: ElastiCache endpoint
- `REDIS_PORT`: `6379`
- `RABBITMQ_HOST`: Amazon MQ endpoint
- `RABBITMQ_PORT`: `5672`
- `FRONTEND_CORS_ORIGINS`: frontend HTTPS origin
- `FRONTEND_CORS_ORIGIN_REGEX`: leave empty unless using preview deploy URLs
- `RATE_LIMIT_ENABLED`: `true`
- `QUERY_RATE_LIMIT_PER_MINUTE`: start with `10`
- `QUERY_RATE_LIMIT_PER_DAY`: start with `100`
- `LLM_PROVIDER`: `openai` or `anthropic`

For the production frontend build, `VITE_API_BASE_URL` can be omitted when
CloudFront routes `/courses`, `/query`, and the other API paths to the ALB. The
frontend will call `window.location.origin` in production. Set
`VITE_API_BASE_URL` only when the API lives on a different origin.

## First Deploy

1. Create ECR repositories for each service image.
2. Build and push images for all six FastAPI services.
3. Create RDS Postgres and enable `vector`.
4. Apply `infra/postgres/init.sql` against the RDS database.
5. Create ElastiCache Redis.
6. Create Amazon MQ RabbitMQ.
7. Create ECS task definitions using private subnets for internal services.
8. Put only `api-gateway` behind an internet-facing ALB.
9. Deploy the frontend with `VITE_API_BASE_URL` pointing at the ALB or API
   domain.
10. Verify `GET /health` and `GET /courses` through the public API.

Keep service-level ports, RabbitMQ management, Prometheus, Grafana, Redis, and
Postgres off the public internet.

## Initial Data Seed

The first seed is an admin operation, not a public user flow.

After the production API gateway is healthy, run the helper script from your
admin machine:

```bash
API_BASE_URL="https://api.your-domain.example" \
ADMIN_API_KEY="<admin-token>" \
scripts/seed-production-data.sh
```

The script runs OMSCentral first, then Reddit, and polls each background job
until it finishes.

Run OMSCentral first:

```bash
curl -X POST "$API_BASE_URL/index/courses" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"course_slugs":[],"missing_only":true,"include_reviews":true,"process_after":true,"limit":132}'
```

Reddit API access was denied for automated ingestion, so production Reddit data
is curated from approved/search-discovered links. Do not schedule the Reddit API
scraper unless API access is approved later. Use the approved manual importer
workflow instead:

```bash
API_BASE_URL="https://d38rezjkrvukjs.cloudfront.net" \
ADMIN_API_KEY="<admin-token>" \
scripts/collect-and-import-priority-reddit.sh
```

Poll the job:

```bash
curl -H "Authorization: Bearer $ADMIN_API_KEY" \
  "$API_BASE_URL/index/jobs/<job_id>"
```

## Scheduled Refresh

Use EventBridge Scheduler after the first seed is complete. It should call the
protected API gateway endpoint with the admin token from Secrets Manager.

Recommended starting schedule:

- OMSCentral refresh: weekly
- Reddit refresh: manual curated import only, unless Reddit grants API access
- Deep Reddit refresh: manual only, because it is slower and noisier

If EventBridge cannot attach the admin header directly in the deployment shape,
use a tiny Lambda target that reads `ADMIN_API_KEY` from Secrets Manager and
POSTs to the API gateway.

## Smoke Tests

After seed jobs finish, verify:

```bash
curl "$API_BASE_URL/courses"

curl -X POST "$API_BASE_URL/query" \
  -H "Content-Type: application/json" \
  -d '{"question":"What does Reddit say about CS 6210 workload?","top_k":8}'

curl -X POST "$API_BASE_URL/query" \
  -H "Content-Type: application/json" \
  -d '{"question":"Compare OMSCentral reviews and Reddit discussions for CS 6515 stress and workload.","top_k":8}'
```

The first query should return Reddit citations. The second should separate
structured OMSCentral evidence from anecdotal Reddit discussion when both are
retrieved.

## Cost Controls

- Billing alarm: `omscentral-estimated-charges-50usd`
- API 5XX alarm: `omscentral-api-alb-5xx`
- API target health alarm: `omscentral-api-unhealthy-targets`
- Keep public beta rate limits conservative.
- Prefer `gpt-4.1-mini` or a cost-equivalent Anthropic model for early beta.
- Keep Redis answer caching enabled.
- Watch LLM request count and token usage daily after posting publicly.
- Do not run deep Reddit refreshes on a tight cron.

The production database has 7-day automated backups and deletion protection
enabled. Redis has 7-day snapshots enabled. CloudWatch log groups for ECS
services retain logs for 14 days.
