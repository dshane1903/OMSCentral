from __future__ import annotations

import json
import uuid
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.scrapers.omscentral import OMSCentralClient
from app.scrapers.reddit import RedditClient
from shared.schemas.models import (
    CourseCatalogEntry,
    CourseReview,
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
    RedditDocument,
    RedditScrapeRequest,
    RedditScrapeResponse,
)
from shared.utils.config import get_settings
from shared.utils.db import db_connection, ensure_schema
from shared.utils.messaging import publish_document_ingested
from shared.utils.observability import (
    DOCUMENTS_PERSISTED,
    SCRAPE_RUNS,
    instrument_fastapi_app,
)
from shared.utils.service_client import post_json

app = FastAPI(title="OMSCS Ingestion Service", version="0.2.0")
instrument_fastapi_app(app, "ingestion-service")
settings = get_settings()


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "ingestion-service"}


def write_snapshot(course: CourseCatalogEntry, reviews: list[CourseReview]) -> None:
    snapshot_root = Path(settings.document_storage_path) / "omscentral"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_root / f"{course.slug}.json"
    payload = {
        "course": course.model_dump(mode="json"),
        "reviews": [review.model_dump(mode="json") for review in reviews],
    }
    snapshot_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def upsert_course(course: CourseCatalogEntry) -> str:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO course_catalog (
                    course_id,
                    source,
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
                    metadata
                )
                VALUES (
                    %(course_id)s,
                    %(source)s,
                    %(slug)s,
                    %(name)s,
                    %(codes)s,
                    %(credit_hours)s,
                    %(description)s,
                    %(rating)s,
                    %(difficulty)s,
                    %(workload)s,
                    %(review_count)s,
                    %(official_url)s,
                    %(syllabus_url)s,
                    %(metadata)s::jsonb
                )
                ON CONFLICT (slug) DO UPDATE SET
                    source = EXCLUDED.source,
                    name = EXCLUDED.name,
                    codes = EXCLUDED.codes,
                    credit_hours = EXCLUDED.credit_hours,
                    description = EXCLUDED.description,
                    rating = EXCLUDED.rating,
                    difficulty = EXCLUDED.difficulty,
                    workload = EXCLUDED.workload,
                    review_count = EXCLUDED.review_count,
                    official_url = EXCLUDED.official_url,
                    syllabus_url = EXCLUDED.syllabus_url,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING course_id
                """,
                {
                    **course.model_dump(),
                    "metadata": json.dumps(course.metadata),
                },
            )
            row = cursor.fetchone()
        connection.commit()
    return row["course_id"]


def apply_course_id_to_reviews(
    reviews: list[CourseReview],
    course_id: str,
) -> list[CourseReview]:
    return [review.model_copy(update={"course_id": course_id}) for review in reviews]


def upsert_reviews(reviews: list[CourseReview]) -> int:
    if not reviews:
        return 0

    with db_connection() as connection:
        with connection.cursor() as cursor:
            for review in reviews:
                cursor.execute(
                    """
                    INSERT INTO documents (
                        id,
                        source,
                        source_document_id,
                        document_type,
                        title,
                        url,
                        course_id,
                        course_slug,
                        course_name,
                        course_codes,
                        published_at,
                        content,
                        content_hash,
                        metadata,
                        chunk_count
                    )
                    VALUES (
                        %(id)s,
                        %(source)s,
                        %(source_document_id)s,
                        'course_review',
                        %(title)s,
                        %(url)s,
                        %(course_id)s,
                        %(course_slug)s,
                        %(course_name)s,
                        %(course_codes)s,
                        %(published_at)s,
                        %(content)s,
                        %(content_hash)s,
                        %(metadata)s::jsonb,
                        0
                    )
                    ON CONFLICT (source, source_document_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        url = EXCLUDED.url,
                        course_id = EXCLUDED.course_id,
                        course_slug = EXCLUDED.course_slug,
                        course_name = EXCLUDED.course_name,
                        course_codes = EXCLUDED.course_codes,
                        published_at = EXCLUDED.published_at,
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        metadata = EXCLUDED.metadata,
                        chunk_count = CASE
                            WHEN documents.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                            THEN 0
                            ELSE documents.chunk_count
                        END,
                        updated_at = NOW()
                    """,
                    {
                        "id": review.document_id,
                        "source": review.source,
                        "source_document_id": review.source_document_id,
                        "title": review.title,
                        "url": review.url,
                        "course_id": review.course_id,
                        "course_slug": review.course_slug,
                        "course_name": review.course_name,
                        "course_codes": review.course_codes,
                        "published_at": review.published_at,
                        "content": review.content,
                        "content_hash": review.content_hash,
                        "metadata": json.dumps(
                            {
                                **review.metadata,
                                "author": review.author,
                                "semester": review.semester,
                                "rating": review.rating,
                                "difficulty": review.difficulty,
                                "workload_hours": review.workload_hours,
                            }
                        ),
                    },
                )
        connection.commit()

    return len(reviews)


def delete_documents(request: DeleteDocumentsRequest) -> DeleteDocumentsResponse:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM documents
                WHERE id = ANY(%s::text[])
                    AND (%s::text IS NULL OR source = %s)
                RETURNING id
                """,
                (request.document_ids, request.source, request.source),
            )
            deleted_ids = [row["id"] for row in cursor.fetchall()]
        connection.commit()

    return DeleteDocumentsResponse(
        requested_count=len(request.document_ids),
        deleted_count=len(deleted_ids),
        deleted_document_ids=deleted_ids,
    )


def create_index_job(request: IndexCoursesRequest) -> str:
    job_id = str(uuid.uuid4())
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO index_jobs (
                    id,
                    status,
                    requested_course_slugs,
                    missing_only,
                    include_reviews,
                    process_after,
                    limit_count
                )
                VALUES (%s, 'queued', %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    request.course_slugs,
                    request.missing_only,
                    request.include_reviews,
                    request.process_after,
                    request.limit,
                ),
            )
        connection.commit()
    return job_id


def get_index_job(job_id: str) -> IndexJobStatus | None:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    status,
                    requested_course_slugs,
                    missing_only,
                    include_reviews,
                    process_after,
                    limit_count,
                    total_courses,
                    courses_indexed,
                    documents_persisted,
                    processing_documents_processed,
                    processing_chunks_created,
                    errors,
                    created_at,
                    started_at,
                    finished_at
                FROM index_jobs
                WHERE id = %s
                """,
                (job_id,),
            )
            row = cursor.fetchone()

    if row is None:
        return None

    return _index_job_from_row(row)


def update_index_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return

    assignments = [
        f"{field} = %s::jsonb" if field == "errors" else f"{field} = %s"
        for field in fields
    ]
    values = [
        json.dumps(value) if field == "errors" else value
        for field, value in fields.items()
    ]
    assignments.append("updated_at = NOW()")

    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE index_jobs
                SET {", ".join(assignments)}
                WHERE id = %s
                """,
                (*values, job_id),
            )
        connection.commit()


def get_indexed_course_slugs() -> set[str]:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT documents.course_slug
                FROM documents
                JOIN chunks ON chunks.document_id = documents.id
                WHERE documents.course_slug IS NOT NULL
                    AND documents.course_slug != ''
                """
            )
            return {row["course_slug"] for row in cursor.fetchall()}


def get_reddit_indexed_course_slugs() -> set[str]:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT documents.course_slug
                FROM documents
                JOIN chunks ON chunks.document_id = documents.id
                WHERE documents.source = 'reddit'
                    AND documents.course_slug IS NOT NULL
                    AND documents.course_slug != ''
                """
            )
            return {row["course_slug"] for row in cursor.fetchall()}


def get_course_catalog_entry(slug: str) -> CourseCatalogEntry | None:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    course_id,
                    source,
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
                    metadata
                FROM course_catalog
                WHERE slug = %s
                """,
                (slug,),
            )
            row = cursor.fetchone()

    if row is None:
        return None

    return CourseCatalogEntry(
        course_id=row["course_id"],
        source=row["source"],
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
        metadata=row["metadata"] or {},
    )


def _index_job_from_row(row: dict[str, Any]) -> IndexJobStatus:
    return IndexJobStatus(
        job_id=row["id"],
        status=row["status"],
        requested_course_slugs=row["requested_course_slugs"] or [],
        missing_only=row["missing_only"],
        include_reviews=row["include_reviews"],
        process_after=row["process_after"],
        limit=row["limit_count"],
        total_courses=row["total_courses"],
        courses_indexed=row["courses_indexed"],
        documents_persisted=row["documents_persisted"],
        processing_documents_processed=row["processing_documents_processed"],
        processing_chunks_created=row["processing_chunks_created"],
        errors=row["errors"] or [],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def create_reddit_index_job(request: IndexRedditRequest) -> str:
    return create_index_job(
        IndexCoursesRequest(
            course_slugs=request.course_slugs,
            missing_only=request.missing_only,
            include_reviews=True,
            process_after=request.process_after,
            limit=request.limit,
        )
    )


@app.post("/index/courses", response_model=IndexCoursesResponse)
async def start_course_index(
    request: IndexCoursesRequest,
    background_tasks: BackgroundTasks,
) -> IndexCoursesResponse:
    job_id = create_index_job(request)
    background_tasks.add_task(run_course_index_job, job_id, request)
    return IndexCoursesResponse(
        job_id=job_id,
        status="queued",
        message="Course indexing job queued.",
    )


@app.post("/index/reddit", response_model=IndexCoursesResponse)
async def start_reddit_index(
    request: IndexRedditRequest,
    background_tasks: BackgroundTasks,
) -> IndexCoursesResponse:
    job_id = create_reddit_index_job(request)
    background_tasks.add_task(run_reddit_index_job, job_id, request)
    return IndexCoursesResponse(
        job_id=job_id,
        status="queued",
        message="Reddit indexing job queued.",
    )


@app.get("/index/jobs/{job_id}", response_model=IndexJobStatus)
async def get_course_index_job(job_id: str) -> IndexJobStatus:
    job = get_index_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown index job: {job_id}")
    return job


async def run_course_index_job(job_id: str, request: IndexCoursesRequest) -> None:
    errors: list[dict[str, str]] = []
    documents_persisted = 0
    client = OMSCentralClient(settings)

    update_index_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    try:
        catalog = await client.fetch_catalog()
        catalog_by_slug = {course.slug: course for course in catalog}

        if request.course_slugs:
            missing = sorted(
                slug for slug in request.course_slugs if slug not in catalog_by_slug
            )
            if missing:
                raise ValueError(f"Unknown course slugs: {', '.join(missing)}")
            selected_courses = [catalog_by_slug[slug] for slug in request.course_slugs]
        else:
            selected_courses = catalog

        if request.missing_only:
            indexed_slugs = get_indexed_course_slugs()
            selected_courses = [
                course for course in selected_courses if course.slug not in indexed_slugs
            ]

        if request.limit is not None:
            selected_courses = selected_courses[: request.limit]

        update_index_job(job_id, total_courses=len(selected_courses))

        for index, catalog_entry in enumerate(selected_courses, start=1):
            try:
                course, reviews = await client.fetch_course_reviews(catalog_entry)
                course_id = upsert_course(course)
                if request.include_reviews:
                    reviews = apply_course_id_to_reviews(reviews, course_id)
                    persisted_reviews = upsert_reviews(reviews)
                    documents_persisted += persisted_reviews
                    DOCUMENTS_PERSISTED.labels(source="omscentral").inc(
                        persisted_reviews
                    )
                    for review in reviews:
                        await publish_document_ingested(review.document_id)
                write_snapshot(course, reviews)
            except Exception as exc:
                errors.append(
                    {
                        "course_slug": catalog_entry.slug,
                        "error": str(exc),
                    }
                )

            update_index_job(
                job_id,
                courses_indexed=index,
                documents_persisted=documents_persisted,
                errors=errors,
            )

        if request.process_after:
            processing_result = await _process_indexed_documents()
            errors.extend(processing_result["errors"])
            update_index_job(
                job_id,
                processing_documents_processed=processing_result[
                    "documents_processed"
                ],
                processing_chunks_created=processing_result["chunks_created"],
                errors=errors,
            )

        update_index_job(
            job_id,
            status="completed_with_errors" if errors else "completed",
            finished_at=datetime.now(timezone.utc),
            errors=errors,
        )
    except Exception as exc:
        errors.append({"course_slug": "", "error": str(exc)})
        update_index_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            errors=errors,
        )
    finally:
        await client.aclose()


async def run_reddit_index_job(job_id: str, request: IndexRedditRequest) -> None:
    errors: list[dict[str, str]] = []
    documents_persisted = 0
    omscentral_client = OMSCentralClient(settings)
    reddit_client = RedditClient(settings)

    update_index_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    try:
        catalog = await omscentral_client.fetch_catalog()
        catalog_by_slug = {course.slug: course for course in catalog}

        if request.course_slugs:
            missing = sorted(
                slug for slug in request.course_slugs if slug not in catalog_by_slug
            )
            if missing:
                raise ValueError(f"Unknown course slugs: {', '.join(missing)}")
            selected_courses = [catalog_by_slug[slug] for slug in request.course_slugs]
        else:
            selected_courses = catalog

        if request.missing_only:
            indexed_slugs = get_reddit_indexed_course_slugs()
            selected_courses = [
                course for course in selected_courses if course.slug not in indexed_slugs
            ]

        if request.limit is not None:
            selected_courses = selected_courses[: request.limit]

        update_index_job(job_id, total_courses=len(selected_courses))

        for index, course in enumerate(selected_courses, start=1):
            try:
                docs = await reddit_client.scrape_course_discussions(
                    catalog,
                    course_slugs=[course.slug],
                    posts_per_course=request.posts_per_course,
                    include_aliases=request.include_aliases,
                    search_modes=request.search_modes,
                    max_search_results_per_query=request.max_search_results_per_query,
                )
                persisted_docs = upsert_reddit_documents(docs)
                documents_persisted += persisted_docs
                DOCUMENTS_PERSISTED.labels(source="reddit").inc(persisted_docs)
                for doc in docs:
                    await publish_document_ingested(doc.document_id)
            except Exception as exc:
                errors.append(
                    {
                        "course_slug": course.slug,
                        "error": str(exc),
                    }
                )

            update_index_job(
                job_id,
                courses_indexed=index,
                documents_persisted=documents_persisted,
                errors=errors,
            )

        if request.process_after:
            processing_result = await _process_indexed_documents()
            errors.extend(processing_result["errors"])
            update_index_job(
                job_id,
                processing_documents_processed=processing_result[
                    "documents_processed"
                ],
                processing_chunks_created=processing_result["chunks_created"],
                errors=errors,
            )

        update_index_job(
            job_id,
            status="completed_with_errors" if errors else "completed",
            finished_at=datetime.now(timezone.utc),
            errors=errors,
        )
    except Exception as exc:
        errors.append({"course_slug": "", "error": str(exc)})
        update_index_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            errors=errors,
        )
    finally:
        await omscentral_client.aclose()
        await reddit_client.aclose()


async def _process_indexed_documents() -> dict[str, Any]:
    try:
        return await post_json(
            f"{settings.processing_service_url}/process",
            {"limit": 50, "max_batches": 1000},
            timeout=3600.0,
        )
    except httpx.HTTPError as exc:
        return {
            "documents_processed": 0,
            "chunks_created": 0,
            "errors": [{"document_id": "", "error": str(exc)}],
        }


@app.post("/sources/omscentral/scrape", response_model=OMSCentralScrapeResponse)
async def scrape_omscentral(
    request: OMSCentralScrapeRequest,
) -> OMSCentralScrapeResponse:
    client = OMSCentralClient(settings)
    try:
        catalog = await client.fetch_catalog()
        catalog_by_slug = {course.slug: course for course in catalog}

        if request.course_slugs:
            missing = sorted(
                slug for slug in request.course_slugs if slug not in catalog_by_slug
            )
            if missing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown course slugs: {', '.join(missing)}",
                )
            selected_courses = [catalog_by_slug[slug] for slug in request.course_slugs]
        else:
            selected_courses = catalog

        if request.limit is not None:
            selected_courses = selected_courses[: request.limit]

        scraped_courses: list[CourseCatalogEntry] = []
        scraped_reviews: list[CourseReview] = []
        persisted_document_count = 0

        for catalog_entry in selected_courses:
            course, reviews = await client.fetch_course_reviews(catalog_entry)
            scraped_courses.append(course)
            if request.include_reviews:
                scraped_reviews.extend(reviews)
            if request.persist:
                course_id = upsert_course(course)
                if request.include_reviews:
                    reviews = apply_course_id_to_reviews(reviews, course_id)
                    persisted_reviews = upsert_reviews(reviews)
                    persisted_document_count += persisted_reviews
                    DOCUMENTS_PERSISTED.labels(source="omscentral").inc(
                        persisted_reviews
                    )
                    # Publish events only after the DB write committed.
                    # If the broker is down the reconciliation poller in the
                    # processing service will still pick these documents up.
                    for review in reviews:
                        await publish_document_ingested(review.document_id)
                write_snapshot(course, reviews)

        SCRAPE_RUNS.labels(source="omscentral", status="success").inc()
        return OMSCentralScrapeResponse(
            catalog_count=len(catalog),
            scraped_course_count=len(scraped_courses),
            review_count=len(scraped_reviews),
            persisted_document_count=persisted_document_count,
            courses=scraped_courses,
            reviews=scraped_reviews,
        )
    except HTTPException:
        SCRAPE_RUNS.labels(source="omscentral", status="failure").inc()
        raise
    except Exception as exc:
        SCRAPE_RUNS.labels(source="omscentral", status="failure").inc()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await client.aclose()


def upsert_reddit_documents(documents: list[RedditDocument]) -> int:
    """Persist Reddit documents using the same documents table."""
    if not documents:
        return 0

    persisted = 0
    with db_connection() as connection:
        with connection.cursor() as cursor:
            for doc in documents:
                cursor.execute(
                    """
                    INSERT INTO documents (
                        id,
                        source,
                        source_document_id,
                        document_type,
                        title,
                        url,
                        course_id,
                        course_slug,
                        course_name,
                        course_codes,
                        published_at,
                        content,
                        content_hash,
                        metadata,
                        chunk_count
                    )
                    VALUES (
                        %(id)s,
                        %(source)s,
                        %(source_document_id)s,
                        'reddit_post',
                        %(title)s,
                        %(url)s,
                        %(course_id)s,
                        %(course_slug)s,
                        %(course_name)s,
                        %(course_codes)s,
                        %(published_at)s,
                        %(content)s,
                        %(content_hash)s,
                        %(metadata)s::jsonb,
                        0
                    )
                    ON CONFLICT (source, source_document_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        url = EXCLUDED.url,
                        course_id = EXCLUDED.course_id,
                        course_slug = EXCLUDED.course_slug,
                        course_name = EXCLUDED.course_name,
                        course_codes = EXCLUDED.course_codes,
                        published_at = EXCLUDED.published_at,
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        metadata = EXCLUDED.metadata,
                        chunk_count = CASE
                            WHEN documents.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                            THEN 0
                            ELSE documents.chunk_count
                        END,
                        updated_at = NOW()
                    """,
                    {
                        "id": doc.document_id,
                        "source": doc.source,
                        "source_document_id": doc.source_document_id,
                        "title": doc.title,
                        "url": doc.url,
                        "course_id": None,  # Don't FK-link; course context lives in slug/name/codes
                        "course_slug": doc.course_slug,
                        "course_name": doc.course_name,
                        "course_codes": doc.course_codes,
                        "published_at": doc.published_at,
                        "content": doc.content,
                        "content_hash": doc.content_hash,
                        "metadata": json.dumps({
                            **doc.metadata,
                            "author": doc.author,
                            "score": doc.score,
                            "num_comments": doc.num_comments,
                            "subreddit": doc.subreddit,
                        }),
                    },
                )
                persisted += 1
        connection.commit()

    return persisted


def build_manual_reddit_document(
    request: ManualRedditDocumentRequest,
    course: CourseCatalogEntry,
) -> RedditDocument:
    parsed_url = urlparse(request.url)
    host = parsed_url.netloc.lower()
    if "reddit.com" not in host:
        raise HTTPException(
            status_code=422,
            detail="Manual Reddit sources must link to a reddit.com permalink.",
        )

    normalized_url = request.url.strip()
    source_hash = sha256(
        f"{request.course_slug}:{normalized_url}".encode("utf-8")
    ).hexdigest()[:24]
    content = request.content.strip()
    content_hash = sha256(content.encode("utf-8")).hexdigest()

    return RedditDocument(
        document_id=f"reddit-manual-{source_hash}",
        source_document_id=f"manual:{source_hash}",
        title=request.title.strip(),
        url=normalized_url,
        author=request.author.strip() or "unknown",
        score=request.score,
        num_comments=request.num_comments,
        published_at=request.published_at,
        course_id=course.course_id,
        course_slug=course.slug,
        course_name=course.name,
        course_codes=course.codes,
        content=content,
        content_hash=content_hash,
        subreddit=request.subreddit.strip().removeprefix("r/") or "OMSCS",
        metadata={
            **request.metadata,
            "ingestion_mode": "manual",
            "curation_note": "Human-curated Reddit excerpt with permalink.",
        },
    )


@app.post("/sources/reddit/manual", response_model=ManualRedditDocumentResponse)
async def ingest_manual_reddit_source(
    request: ManualRedditDocumentRequest,
) -> ManualRedditDocumentResponse:
    course = get_course_catalog_entry(request.course_slug)
    if course is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown course slug: {request.course_slug}",
        )

    doc = build_manual_reddit_document(request, course)
    persisted_count = upsert_reddit_documents([doc])
    DOCUMENTS_PERSISTED.labels(source="reddit").inc(persisted_count)
    await publish_document_ingested(doc.document_id)

    processing_result = {
        "documents_processed": 0,
        "chunks_created": 0,
        "errors": [],
    }
    if request.process_after:
        processing_result = await _process_indexed_documents()

    status = "persisted"
    if processing_result["errors"]:
        status = "persisted_with_processing_errors"
    elif request.process_after:
        status = "processed"

    return ManualRedditDocumentResponse(
        document_id=doc.document_id,
        source_document_id=doc.source_document_id,
        documents_persisted=persisted_count,
        processing_documents_processed=processing_result["documents_processed"],
        processing_chunks_created=processing_result["chunks_created"],
        status=status,
    )


@app.post("/documents/delete", response_model=DeleteDocumentsResponse)
async def delete_documents_route(
    request: DeleteDocumentsRequest,
) -> DeleteDocumentsResponse:
    return delete_documents(request)


@app.post("/sources/reddit/scrape", response_model=RedditScrapeResponse)
async def scrape_reddit(request: RedditScrapeRequest) -> RedditScrapeResponse:
    # We need the course catalog for matching posts to courses.
    # Fetch it from OMSCentral (or from DB if already cached).
    omscentral_client = OMSCentralClient(settings)
    reddit_client = RedditClient(settings)

    try:
        catalog = await omscentral_client.fetch_catalog()

        all_docs: list[RedditDocument] = []

        # Search for course-specific discussions
        if request.course_slugs or not request.include_recent:
            course_docs = await reddit_client.scrape_course_discussions(
                catalog,
                course_slugs=request.course_slugs or None,
                posts_per_course=request.posts_per_course,
                include_aliases=request.include_aliases,
                search_modes=request.search_modes,
                max_search_results_per_query=request.max_search_results_per_query,
            )
            all_docs.extend(course_docs)

        # Also grab recent posts if requested
        if request.include_recent:
            recent_docs = await reddit_client.scrape_recent_posts(
                catalog,
                limit=request.recent_limit,
            )
            # Deduplicate against course-specific results
            seen_ids = {doc.document_id for doc in all_docs}
            for doc in recent_docs:
                if doc.document_id not in seen_ids:
                    all_docs.append(doc)
                    seen_ids.add(doc.document_id)

        persisted_count = 0
        if request.persist:
            persisted_count = upsert_reddit_documents(all_docs)
            DOCUMENTS_PERSISTED.labels(source="reddit").inc(persisted_count)
            # Publish events for the processing pipeline
            for doc in all_docs:
                await publish_document_ingested(doc.document_id)

        courses_matched = sum(1 for doc in all_docs if doc.course_id is not None)

        SCRAPE_RUNS.labels(source="reddit", status="success").inc()
        return RedditScrapeResponse(
            documents_scraped=len(all_docs),
            documents_persisted=persisted_count,
            courses_matched=courses_matched,
            documents=all_docs,
        )
    except HTTPException:
        SCRAPE_RUNS.labels(source="reddit", status="failure").inc()
        raise
    except Exception as exc:
        SCRAPE_RUNS.labels(source="reddit", status="failure").inc()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await omscentral_client.aclose()
        await reddit_client.aclose()
