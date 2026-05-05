import re
import time
from typing import Any

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

RRF_K = 60
RETRIEVAL_CANDIDATE_MULTIPLIER = 5
COURSE_CODE_PATTERN = re.compile(r"\b([A-Z]{2,4})[-\s]?(\d{4})\b", re.IGNORECASE)


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
    course_scopes = resolve_course_scopes(request.question)
    indexed_course_slugs = [
        scope["slug"] for scope in course_scopes if scope["chunk_count"] > 0
    ]
    scope_slug = (
        ",".join(sorted(scope["slug"] for scope in course_scopes))
        if course_scopes
        else "all"
    )
    scope_chunks = (
        ",".join(
            f"{scope['slug']}:{scope['chunk_count']}"
            for scope in sorted(course_scopes, key=lambda item: item["slug"])
        )
        if course_scopes
        else "any"
    )
    cache_key = f"query:v5:hybrid_rrf:{scope_slug}:{scope_chunks}:{request.question}:{request.top_k}"
    cached = get_cached_json(cache_key)
    if cached:
        RETRIEVAL_CACHE_EVENTS.labels(result="hit").inc()
        QUERY_REQUESTS.labels(status="success").inc()
        QUERY_LATENCY.observe(time.perf_counter() - start)
        return QueryResponse.model_validate(cached)

    RETRIEVAL_CACHE_EVENTS.labels(result="miss").inc()

    if course_scopes and not indexed_course_slugs:
        chunks: list[RetrievedChunk] = []
    else:
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
        chunks = retrieve_hybrid(
            request.question,
            query_vector,
            request.top_k,
            course_slugs=indexed_course_slugs or None,
        )

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
    if chunks:
        set_cached_json(cache_key, response.model_dump(mode="json"))
    QUERY_REQUESTS.labels(status="success").inc()
    QUERY_LATENCY.observe(time.perf_counter() - start)
    return response


def retrieve_hybrid(
    question: str,
    query_vector: list[float],
    top_k: int,
    course_slugs: list[str] | None = None,
) -> list[RetrievedChunk]:
    candidate_limit = max(top_k * RETRIEVAL_CANDIDATE_MULTIPLIER, 20)
    vector = serialize_vector(query_vector)

    with db_connection() as connection:
        with connection.cursor() as cursor:
            if course_slugs and len(course_slugs) > 1:
                dense_rows = _interleave_ranked_batches(
                    [
                        _fetch_dense_candidates(
                            cursor,
                            vector,
                            candidate_limit,
                            course_slugs=[course_slug],
                        )
                        for course_slug in course_slugs
                    ]
                )
                sparse_rows = _interleave_ranked_batches(
                    [
                        _fetch_sparse_candidates(
                            cursor,
                            question,
                            candidate_limit,
                            course_slugs=[course_slug],
                        )
                        for course_slug in course_slugs
                    ]
                )
            else:
                dense_rows = _fetch_dense_candidates(
                    cursor,
                    vector,
                    candidate_limit,
                    course_slugs=course_slugs,
                )
                sparse_rows = _fetch_sparse_candidates(
                    cursor,
                    question,
                    candidate_limit,
                    course_slugs=course_slugs,
                )

    return _fuse_candidates(dense_rows, sparse_rows, top_k)


def retrieve_dense_only(query_vector: list[float], top_k: int) -> list[RetrievedChunk]:
    vector = serialize_vector(query_vector)
    with db_connection() as connection:
        with connection.cursor() as cursor:
            rows = _fetch_dense_candidates(cursor, vector, top_k)
    chunks: list[RetrievedChunk] = []
    for rank, row in enumerate(rows, start=1):
        row["dense_rank"] = rank
        chunks.append(_chunk_from_candidate(row, float(row["dense_score"]), "dense"))
    return chunks


def _fetch_dense_candidates(
    cursor: Any,
    vector: str,
    limit: int,
    course_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    if course_slugs:
        # pgvector ivfflat can return zero rows for selective metadata filters
        # because the filter is applied after approximate candidate selection.
        # Course-scoped queries are small enough for an exact scan, and exact
        # filtering is much safer for compare/course-specific retrieval.
        cursor.execute("SET LOCAL enable_indexscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")

    cursor.execute(
        """
        SELECT
            chunks.document_id,
            chunks.chunk_index,
            chunks.text,
            1 - (chunks.embedding <=> %s::vector) AS dense_score,
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
        WHERE (%s::text[] IS NULL OR documents.course_slug = ANY(%s::text[]))
        ORDER BY chunks.embedding <=> %s::vector
        LIMIT %s
        """,
        (vector, course_slugs, course_slugs, vector, limit),
    )
    rows = list(cursor.fetchall())

    if course_slugs:
        cursor.execute("SET LOCAL enable_indexscan = on")
        cursor.execute("SET LOCAL enable_bitmapscan = on")

    return rows


def _interleave_ranked_batches(
    batches: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    max_length = max((len(batch) for batch in batches), default=0)
    for index in range(max_length):
        for batch in batches:
            if index < len(batch):
                rows.append(batch[index])
    return rows


def _fetch_sparse_candidates(
    cursor: Any,
    question: str,
    limit: int,
    course_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    cursor.execute(
        """
        WITH query AS (
            SELECT websearch_to_tsquery('english', %s) AS tsq
        )
        SELECT
            chunks.document_id,
            chunks.chunk_index,
            chunks.text,
            ts_rank_cd(to_tsvector('english', chunks.text), query.tsq) AS sparse_score,
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
        CROSS JOIN query
        WHERE query.tsq @@ to_tsvector('english', chunks.text)
            AND (%s::text[] IS NULL OR documents.course_slug = ANY(%s::text[]))
        ORDER BY sparse_score DESC
        LIMIT %s
        """,
        (question, course_slugs, course_slugs, limit),
    )
    return list(cursor.fetchall())


def resolve_course_scopes(question: str) -> list[dict[str, Any]]:
    normalized_codes = {
        _normalize_course_code(f"{subject}{number}")
        for subject, number in COURSE_CODE_PATTERN.findall(question)
    }
    normalized_question = _normalize_text(question)

    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    course_catalog.slug,
                    course_catalog.name,
                    course_catalog.codes,
                    COUNT(chunks.id)::integer AS chunk_count
                FROM course_catalog
                LEFT JOIN documents ON documents.course_slug = course_catalog.slug
                LEFT JOIN chunks ON chunks.document_id = documents.id
                GROUP BY course_catalog.slug, course_catalog.name, course_catalog.codes
                ORDER BY LENGTH(course_catalog.name) DESC
                """
            )
            courses = list(cursor.fetchall())

    matched: dict[str, dict[str, Any]] = {}

    for course in courses:
        course_codes = {
            _normalize_course_code(code)
            for code in (course["codes"] or [])
        }
        if normalized_codes and normalized_codes.intersection(course_codes):
            matched[course["slug"]] = course

    for course in courses:
        slug_phrase = _normalize_text(course["slug"].replace("-", " "))
        name_phrase = _normalize_text(course["name"])
        if _phrase_matches(normalized_question, slug_phrase):
            matched[course["slug"]] = course
        if _phrase_matches(normalized_question, name_phrase):
            matched[course["slug"]] = course

    return list(matched.values())


def _normalize_course_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _normalize_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _phrase_matches(normalized_question: str, normalized_phrase: str) -> bool:
    return len(normalized_phrase) >= 6 and normalized_phrase in normalized_question


def _fuse_candidates(
    dense_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    top_k: int,
) -> list[RetrievedChunk]:
    candidates: dict[tuple[str, int], dict[str, Any]] = {}

    for rank, row in enumerate(dense_rows, start=1):
        key = (row["document_id"], row["chunk_index"])
        entry = candidates.setdefault(key, dict(row))
        entry["dense_score"] = float(row["dense_score"])
        entry["dense_rank"] = rank

    for rank, row in enumerate(sparse_rows, start=1):
        key = (row["document_id"], row["chunk_index"])
        entry = candidates.setdefault(key, dict(row))
        entry["sparse_score"] = float(row["sparse_score"])
        entry["sparse_rank"] = rank

    ranked = sorted(
        candidates.values(),
        key=lambda row: _rrf_score(row.get("dense_rank"), row.get("sparse_rank")),
        reverse=True,
    )

    return [
        _chunk_from_candidate(
            row,
            _rrf_score(row.get("dense_rank"), row.get("sparse_rank")),
            "hybrid_rrf",
        )
        for row in ranked[:top_k]
    ]


def _rrf_score(dense_rank: int | None, sparse_rank: int | None) -> float:
    score = 0.0
    if dense_rank is not None:
        score += 1.0 / (RRF_K + dense_rank)
    if sparse_rank is not None:
        score += 1.0 / (RRF_K + sparse_rank)
    return score


def _chunk_from_candidate(
    row: dict[str, Any],
    score: float,
    method: str,
) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=row["document_id"],
        chunk_index=row["chunk_index"],
        score=score,
        text=row["text"],
        dense_score=row.get("dense_score"),
        sparse_score=row.get("sparse_score"),
        dense_rank=row.get("dense_rank"),
        sparse_rank=row.get("sparse_rank"),
        retrieval_method=method,
        source=row["source"],
        document_type=row["document_type"],
        title=row["title"],
        url=row["url"],
        course_slug=row["course_slug"],
        course_name=row["course_name"],
        course_codes=row["course_codes"] or [],
        published_at=row["published_at"],
    )


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
