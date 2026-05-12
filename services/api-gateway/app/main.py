import hashlib
import secrets
from datetime import UTC, datetime

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from shared.schemas.models import (
    CourseCatalogEntry,
    CourseDocumentsResponse,
    CourseListResponse,
    DeleteDocumentsRequest,
    DeleteDocumentsResponse,
    IndexCoursesRequest,
    IndexCoursesResponse,
    IndexJobStatus,
    IndexRedditRequest,
    ManualRedditDocumentRequest,
    ManualRedditDocumentResponse,
    OMSCentralScrapeRequest,
    OMSCentralScrapeResponse,
    ProcessDocumentsRequest,
    QueryRequest,
    QueryResponse,
    RedditScrapeRequest,
    RedditScrapeResponse,
)
from shared.utils.config import get_settings
from shared.utils.cache import get_redis_client
from shared.utils.observability import instrument_fastapi_app
from shared.utils.service_client import get_json, post_json

app = FastAPI(title="OMSCS Course Intelligence API Gateway", version="0.2.0")
instrument_fastapi_app(app, "api-gateway")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.frontend_cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_origin_regex=settings.frontend_cors_origin_regex or None,
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "api-gateway"}


def client_identity(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def hashed_identity(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def admin_token_from_request(request: Request) -> str | None:
    token = request.headers.get("x-admin-token")
    if token:
        return token.strip()

    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()

    return None


def require_admin(request: Request) -> None:
    expected = settings.admin_api_key.strip()
    provided = admin_token_from_request(request)
    if not expected or expected == "replace-me":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key is not configured.",
        )
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token required.",
        )


def enforce_query_rate_limit(request: Request) -> None:
    if not settings.rate_limit_enabled:
        return

    identity = hashed_identity(client_identity(request))
    now = datetime.now(UTC)
    minute_key = f"ratelimit:query:minute:{identity}:{int(now.timestamp() // 60)}"
    day_key = f"ratelimit:query:day:{identity}:{now.strftime('%Y%m%d')}"

    try:
        client = get_redis_client()
        with client.pipeline() as pipe:
            pipe.incr(minute_key)
            pipe.expire(minute_key, 90)
            pipe.incr(day_key)
            pipe.expire(day_key, 60 * 60 * 26)
            minute_count, _, day_count, _ = pipe.execute()
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter is unavailable.",
        ) from exc

    if minute_count > settings.query_rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Query rate limit exceeded. Please wait a minute and try again.",
            headers={"Retry-After": "60"},
        )
    if day_count > settings.query_rate_limit_per_day:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily query limit exceeded. Please try again tomorrow.",
            headers={"Retry-After": "86400"},
        )


@app.post(
    "/sources/omscentral/scrape",
    response_model=OMSCentralScrapeResponse,
    dependencies=[Depends(require_admin)],
)
async def scrape_omscentral(
    request: OMSCentralScrapeRequest,
) -> OMSCentralScrapeResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/sources/omscentral/scrape",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return OMSCentralScrapeResponse.model_validate(payload)


@app.post(
    "/sources/reddit/scrape",
    response_model=RedditScrapeResponse,
    dependencies=[Depends(require_admin)],
)
async def scrape_reddit(request: RedditScrapeRequest) -> RedditScrapeResponse:
    try:
        # Reddit scraping is slow due to rate limits — give it more time
        payload = await post_json(
            f"{settings.ingestion_service_url}/sources/reddit/scrape",
            request.model_dump(),
            timeout=300.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RedditScrapeResponse.model_validate(payload)


@app.post(
    "/sources/reddit/manual",
    response_model=ManualRedditDocumentResponse,
    dependencies=[Depends(require_admin)],
)
async def ingest_manual_reddit_source(
    request: ManualRedditDocumentRequest,
) -> ManualRedditDocumentResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/sources/reddit/manual",
            request.model_dump(mode="json"),
            timeout=300.0,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 422}:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.json().get("detail"),
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ManualRedditDocumentResponse.model_validate(payload)


@app.post(
    "/documents/delete",
    response_model=DeleteDocumentsResponse,
    dependencies=[Depends(require_admin)],
)
async def delete_documents(
    request: DeleteDocumentsRequest,
) -> DeleteDocumentsResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/documents/delete",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return DeleteDocumentsResponse.model_validate(payload)


@app.post(
    "/index/courses",
    response_model=IndexCoursesResponse,
    dependencies=[Depends(require_admin)],
)
async def index_courses(request: IndexCoursesRequest) -> IndexCoursesResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/index/courses",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexCoursesResponse.model_validate(payload)


@app.post(
    "/index/reddit",
    response_model=IndexCoursesResponse,
    dependencies=[Depends(require_admin)],
)
async def index_reddit(request: IndexRedditRequest) -> IndexCoursesResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/index/reddit",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexCoursesResponse.model_validate(payload)


@app.get(
    "/index/jobs/{job_id}",
    response_model=IndexJobStatus,
    dependencies=[Depends(require_admin)],
)
async def get_index_job(job_id: str) -> IndexJobStatus:
    try:
        payload = await get_json(
            f"{settings.ingestion_service_url}/index/jobs/{job_id}"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail=exc.response.json().get("detail")) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexJobStatus.model_validate(payload)


@app.get("/courses", response_model=CourseListResponse)
async def list_courses() -> CourseListResponse:
    try:
        payload = await get_json(f"{settings.retrieval_service_url}/courses")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CourseListResponse.model_validate(payload)


@app.get("/courses/{slug}", response_model=CourseCatalogEntry)
async def get_course(slug: str) -> CourseCatalogEntry:
    try:
        payload = await get_json(f"{settings.retrieval_service_url}/courses/{slug}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail=exc.response.json().get("detail")) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CourseCatalogEntry.model_validate(payload)


@app.get("/courses/{slug}/documents", response_model=CourseDocumentsResponse)
async def list_course_documents(slug: str) -> CourseDocumentsResponse:
    try:
        payload = await get_json(
            f"{settings.retrieval_service_url}/courses/{slug}/documents"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail=exc.response.json().get("detail")) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CourseDocumentsResponse.model_validate(payload)


@app.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    http_request: Request,
) -> QueryResponse:
    enforce_query_rate_limit(http_request)
    try:
        payload = await post_json(
            f"{settings.retrieval_service_url}/retrieve",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return QueryResponse.model_validate(payload)


@app.post("/process", dependencies=[Depends(require_admin)])
async def trigger_processing(
    request: ProcessDocumentsRequest = ProcessDocumentsRequest(),
) -> dict:
    """Trigger the processing worker to chunk and embed unchunked documents."""
    try:
        payload = await post_json(
            f"{settings.processing_service_url}/process",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return payload
