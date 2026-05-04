import time

import httpx
from fastapi import FastAPI, HTTPException

from shared.schemas.models import (
    CourseCatalogEntry,
    CourseDocumentSummary,
    CourseDocumentsResponse,
    CourseListResponse,
    QueryRequest,
    QueryResponse,
    RetrievedChunk,
)
from shared.utils.cache import get_cached_json, set_cached_json
from shared.utils.config import get_settings
from shared.utils.db import db_connection, ensure_schema, serialize_vector
from shared.utils.observability import (
    QUERY_LATENCY,
    QUERY_REQUESTS,
    RETRIEVAL_CACHE_EVENTS,
    instrument_fastapi_app,
)
from shared.utils.service_client import post_json

app = FastAPI(title="RAG Retrieval Service", version="0.1.0")
instrument_fastapi_app(app, "retrieval-service")
settings = get_settings()


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "retrieval-service"}


@app.get("/courses", response_model=CourseListResponse)
def list_courses() -> CourseListResponse:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    course_id,
                    slug,
                    name,
                    codes,
                    credit_hours,
                    description,
                    rating,
                    difficulty,
                    workload,
                    review_count,
                    official_url,
                    syllabus_url,
                    source,
                    metadata
                FROM course_catalog
                ORDER BY name
                """
            )
            rows = list(cursor.fetchall())

    return CourseListResponse(courses=[_course_from_row(row) for row in rows])


@app.get("/courses/{slug}", response_model=CourseCatalogEntry)
def get_course(slug: str) -> CourseCatalogEntry:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    course_id,
                    slug,
                    name,
                    codes,
                    credit_hours,
                    description,
                    rating,
                    difficulty,
                    workload,
                    review_count,
                    official_url,
                    syllabus_url,
                    source,
                    metadata
                FROM course_catalog
                WHERE slug = %s
                """,
                (slug,),
            )
            row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown course slug: {slug}")

    return _course_from_row(row)


@app.get("/courses/{slug}/documents", response_model=CourseDocumentsResponse)
def list_course_documents(slug: str) -> CourseDocumentsResponse:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM course_catalog WHERE slug = %s", (slug,))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail=f"Unknown course slug: {slug}")

            cursor.execute(
                """
                SELECT
                    id,
                    source,
                    source_document_id,
                    document_type,
                    title,
                    url,
                    course_slug,
                    course_name,
                    course_codes,
                    published_at,
                    chunk_count,
                    metadata
                FROM documents
                WHERE course_slug = %s
                ORDER BY published_at DESC NULLS LAST, updated_at DESC
                """,
                (slug,),
            )
            rows = list(cursor.fetchall())

    return CourseDocumentsResponse(
        course_slug=slug,
        documents=[_document_from_row(row) for row in rows],
    )


@app.post("/retrieve", response_model=QueryResponse)
async def retrieve_context(request: QueryRequest) -> QueryResponse:
    start = time.perf_counter()
    cache_key = f"query:{request.question}:{request.top_k}"
    cached = get_cached_json(cache_key)
    if cached:
        RETRIEVAL_CACHE_EVENTS.labels(result="hit").inc()
        QUERY_REQUESTS.labels(status="success").inc()
        QUERY_LATENCY.observe(time.perf_counter() - start)
        return QueryResponse.model_validate(cached)

    RETRIEVAL_CACHE_EVENTS.labels(result="miss").inc()

    try:
        embedding_payload = await post_json(
            f"{settings.embedding_service_url}/embed",
            {"texts": [request.question]},
        )
    except httpx.HTTPError as exc:
        QUERY_REQUESTS.labels(status="failure").inc()
        QUERY_LATENCY.observe(time.perf_counter() - start)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    query_vector = embedding_payload["vectors"][0]

    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    chunks.document_id,
                    chunks.chunk_index,
                    chunks.text,
                    1 - (chunks.embedding <=> %s::vector) AS score,
                    documents.source,
                    documents.document_type,
                    documents.title,
                    documents.url,
                    documents.course_slug,
                    documents.course_name,
                    documents.course_codes,
                    documents.published_at
                FROM chunks
                JOIN documents ON documents.id = chunks.document_id
                ORDER BY chunks.embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    serialize_vector(query_vector),
                    serialize_vector(query_vector),
                    request.top_k,
                ),
            )
            rows = list(cursor.fetchall())

    chunks = [
        RetrievedChunk(
            document_id=row["document_id"],
            chunk_index=row["chunk_index"],
            score=float(row["score"]),
            text=row["text"],
            source=row["source"],
            document_type=row["document_type"],
            title=row["title"],
            url=row["url"],
            course_slug=row["course_slug"],
            course_name=row["course_name"],
            course_codes=row["course_codes"] or [],
            published_at=row["published_at"],
        )
        for row in rows
    ]

    try:
        answer_payload = await post_json(
            f"{settings.llm_service_url}/generate",
            {
                "question": request.question,
                "context": [chunk.text for chunk in chunks],
            },
        )
    except httpx.HTTPError as exc:
        QUERY_REQUESTS.labels(status="failure").inc()
        QUERY_LATENCY.observe(time.perf_counter() - start)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    response = QueryResponse(
        answer=answer_payload["answer"],
        chunks=chunks,
    )
    set_cached_json(cache_key, response.model_dump(mode="json"))
    QUERY_REQUESTS.labels(status="success").inc()
    QUERY_LATENCY.observe(time.perf_counter() - start)
    return response


def _course_from_row(row: dict) -> CourseCatalogEntry:
    return CourseCatalogEntry(
        course_id=row["course_id"],
        slug=row["slug"],
        name=row["name"],
        codes=row["codes"] or [],
        credit_hours=row["credit_hours"],
        description=row["description"],
        rating=row["rating"],
        difficulty=row["difficulty"],
        workload=row["workload"],
        review_count=row["review_count"],
        official_url=row["official_url"],
        syllabus_url=row["syllabus_url"],
        source=row["source"],
        metadata=row["metadata"] or {},
    )


def _document_from_row(row: dict) -> CourseDocumentSummary:
    return CourseDocumentSummary(
        document_id=row["id"],
        source_document_id=row["source_document_id"],
        source=row["source"],
        document_type=row["document_type"],
        title=row["title"],
        url=row["url"],
        course_slug=row["course_slug"],
        course_name=row["course_name"],
        course_codes=row["course_codes"] or [],
        published_at=row["published_at"],
        chunk_count=row["chunk_count"],
        metadata=row["metadata"] or {},
    )
