# Deployment Prep

This project is currently local-first with Docker Compose. The production
target should keep the same service boundaries while replacing local stateful
containers with managed infrastructure.

## Recommended AWS Shape

- Frontend: S3 + CloudFront, Amplify Hosting, or Vercel while backend is on AWS
- Public API: Application Load Balancer in front of `api-gateway`
- Services: ECS Fargate tasks for `api-gateway`, `ingestion-service`,
  `retrieval-service`, `processing-service`, `embedding-service`, and
  `llm-service`
- Database: RDS Postgres with pgvector enabled
- Cache: ElastiCache Redis
- Queue: Amazon MQ for RabbitMQ, or migrate to SQS/SNS later
- Secrets: AWS Secrets Manager or SSM Parameter Store
- Logs: CloudWatch Logs
- Metrics: Prometheus/Grafana private to the deployment, or Amazon Managed
  Prometheus + Amazon Managed Grafana
- Images: ECR repositories for each service image
- Infrastructure: Terraform
- CI/CD: GitHub Actions

The concrete first-deploy checklist is in [aws-runbook.md](aws-runbook.md).

## Public vs Private

Expose only the frontend and API gateway publicly.

Keep these private:

- Postgres
- Redis
- RabbitMQ
- Prometheus
- service-level `/metrics` endpoints

Grafana can be exposed on an admin-only subdomain, but should be protected with
strong auth and preferably an IP allowlist, VPN, or SSO.

## Required Secrets

Do not commit real values. Store them in Secrets Manager, SSM Parameter Store,
or the deployment platform's secret store.

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY` if `LLM_PROVIDER=anthropic`
- `ADMIN_API_KEY`
- `POSTGRES_PASSWORD`
- `RABBITMQ_PASSWORD`
- Grafana admin password
- CI/CD cloud credentials

If the previous `.env.example` OpenAI key was real, rotate it before any public
deployment.

## Production Environment Checklist

- Use non-default database and RabbitMQ passwords.
- Set `OPENAI_API_KEY` through the secret store only.
- Set `ADMIN_API_KEY` to a long random value. The API gateway rejects admin
  endpoints when this is still `replace-me`.
- Set service URLs to internal service discovery names.
- Set Postgres, Redis, and RabbitMQ hosts to managed service endpoints.
- Keep API gateway rate limiting enabled for public deployments:
  `RATE_LIMIT_ENABLED=true`, `QUERY_RATE_LIMIT_PER_MINUTE=10`, and
  `QUERY_RATE_LIMIT_PER_DAY=100` are conservative beta defaults.
- Send `X-Admin-Token: <ADMIN_API_KEY>` or
  `Authorization: Bearer <ADMIN_API_KEY>` only from trusted admin tooling for
  scrape, index, and processing endpoints.
- Backfill Reddit separately from OMSCentral with `POST /index/reddit`. V2
  Reddit backfills search course code variants, full course names, known aliases,
  and multiple Reddit search modes. A small beta-friendly request is:
  `{"missing_only": true, "posts_per_course": 25, "include_aliases": true, "search_modes": ["relevance_all", "top_all", "top_year", "new_year"], "max_search_results_per_query": 25, "process_after": true, "limit": 10}`.
  Use explicit `course_slugs` for targeted gaps such as
  `["introduction-to-graduate-algorithms"]`, or use
  `{"missing_only": false, "posts_per_course": 50, "limit": 132}` for a deeper
  source refresh across the catalog.
- Keep `PROMETHEUS` and service metrics endpoints off the public internet.
- Use HTTPS for the frontend and API gateway.
- Add health checks for every ECS service.
- Send container logs to CloudWatch.
- Configure retention for logs and Prometheus metrics.
- Add billing alerts before deploying persistent infrastructure.

## First AWS Milestone

The first production milestone should be deliberately boring:

1. Build and push Docker images to ECR.
2. Create RDS Postgres, ElastiCache Redis, and Amazon MQ.
3. Deploy services to ECS Fargate.
4. Put only `api-gateway` behind an ALB.
5. Verify `/health`, `/query`, `/courses`, and `/metrics` internally.
6. Add CloudWatch logs and Grafana dashboards.

Terraform can be added once this shape is settled; avoid jumping straight to
EKS unless the goal is specifically Kubernetes practice.

## Scheduled Backfills

Do the first OMSCentral and Reddit backfills manually after the production
database is live. After that, use EventBridge Scheduler for refreshes rather
than leaving a public user path responsible for data loading.

Recommended cadence:

- OMSCentral: weekly `POST /index/courses` with `missing_only=true`
- Reddit: daily or every-other-day `POST /index/reddit` with
  `missing_only=true`
- Deep Reddit refresh: manual, `missing_only=false`, because it is slower and
  can create more duplicate/noisy evidence to evaluate

All scheduled calls must include `X-Admin-Token: <ADMIN_API_KEY>` or
`Authorization: Bearer <ADMIN_API_KEY>`. If EventBridge cannot attach that
header in the chosen setup, route the schedule through a small Lambda that reads
the token from Secrets Manager and calls the API gateway.
