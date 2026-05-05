import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from shared.schemas.models import (
    CourseCatalogEntry,
    CourseDocumentsResponse,
    CourseListResponse,
    IndexCoursesRequest,
    IndexCoursesResponse,
    IndexJobStatus,
    OMSCentralScrapeRequest,
    OMSCentralScrapeResponse,
    ProcessDocumentsRequest,
    QueryRequest,
    QueryResponse,
    RedditScrapeRequest,
    RedditScrapeResponse,
)
from shared.utils.config import get_settings
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


@app.post("/sources/omscentral/scrape", response_model=OMSCentralScrapeResponse)
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


@app.post("/sources/reddit/scrape", response_model=RedditScrapeResponse)
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


@app.post("/index/courses", response_model=IndexCoursesResponse)
async def index_courses(request: IndexCoursesRequest) -> IndexCoursesResponse:
    try:
        payload = await post_json(
            f"{settings.ingestion_service_url}/index/courses",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexCoursesResponse.model_validate(payload)


@app.get("/index/jobs/{job_id}", response_model=IndexJobStatus)
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
async def query_documents(request: QueryRequest) -> QueryResponse:
    try:
        payload = await post_json(
            f"{settings.retrieval_service_url}/retrieve",
            request.model_dump(),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return QueryResponse.model_validate(payload)


@app.post("/process")
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
