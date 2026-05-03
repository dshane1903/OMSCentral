from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.scrapers.omscentral import OMSCentralClient
from app.scrapers.reddit import RedditClient
from shared.schemas.models import (
    CourseCatalogEntry,
    CourseReview,
    OMSCentralScrapeRequest,
    OMSCentralScrapeResponse,
    RedditDocument,
    RedditScrapeRequest,
    RedditScrapeResponse,
)
from shared.utils.config import get_settings
from shared.utils.db import db_connection, ensure_schema
from shared.utils.messaging import publish_document_ingested

app = FastAPI(title="OMSCS Ingestion Service", version="0.2.0")
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


def upsert_course(course: CourseCatalogEntry) -> None:
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
                ON CONFLICT (course_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    slug = EXCLUDED.slug,
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
                """,
                {
                    **course.model_dump(),
                    "metadata": json.dumps(course.metadata),
                },
            )
        connection.commit()


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
                upsert_course(course)
                if request.include_reviews:
                    persisted_document_count += upsert_reviews(reviews)
                    # Publish events only after the DB write committed.
                    # If the broker is down the reconciliation poller in the
                    # processing service will still pick these documents up.
                    for review in reviews:
                        await publish_document_ingested(review.document_id)
                write_snapshot(course, reviews)

        return OMSCentralScrapeResponse(
            catalog_count=len(catalog),
            scraped_course_count=len(scraped_courses),
            review_count=len(scraped_reviews),
            persisted_document_count=persisted_document_count,
            courses=scraped_courses,
            reviews=scraped_reviews,
        )
    except HTTPException:
        raise
    except Exception as exc:
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
                        updated_at = NOW()
                    """,
                    {
                        "id": doc.document_id,
                        "source": doc.source,
                        "source_document_id": doc.source_document_id,
                        "title": doc.title,
                        "url": doc.url,
                        "course_id": doc.course_id,
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
            # Publish events for the processing pipeline
            for doc in all_docs:
                await publish_document_ingested(doc.document_id)

        courses_matched = sum(1 for doc in all_docs if doc.course_id is not None)

        return RedditScrapeResponse(
            documents_scraped=len(all_docs),
            documents_persisted=persisted_count,
            courses_matched=courses_matched,
            documents=all_docs,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await omscentral_client.aclose()
        await reddit_client.aclose()
