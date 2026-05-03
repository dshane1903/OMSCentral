import httpx
from fastapi import FastAPI, HTTPException

from shared.schemas.models import (
    OMSCentralScrapeRequest,
    OMSCentralScrapeResponse,
    QueryRequest,
    QueryResponse,
    RedditScrapeRequest,
    RedditScrapeResponse,
)
from shared.utils.config import get_settings
from shared.utils.service_client import post_json

app = FastAPI(title="OMSCS Course Intelligence API Gateway", version="0.2.0")
settings = get_settings()


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
async def trigger_processing() -> dict:
    """Trigger the processing worker to chunk and embed unchunked documents."""
    try:
        payload = await post_json(
            f"{settings.processing_service_url}/process",
            {},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return payload