from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    document_id: str
    chunk_index: int
    text: str


class DocumentCreateResponse(BaseModel):
    document_id: str
    title: str
    chunk_count: int
    status: str


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1)


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]


class DocumentIngestResponse(BaseModel):
    document_id: str
    title: str
    chunk_count: int
    status: str


class CourseCatalogEntry(BaseModel):
    course_id: str
    slug: str
    name: str
    codes: list[str] = Field(default_factory=list)
    credit_hours: int | None = None
    description: str | None = None
    rating: float | None = None
    difficulty: float | None = None
    workload: float | None = None
    review_count: int = 0
    official_url: str | None = None
    syllabus_url: str | None = None
    source: str = "omscentral"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CourseReview(BaseModel):
    document_id: str
    source_document_id: str
    source: str = "omscentral"
    course_id: str
    course_slug: str
    course_name: str
    course_codes: list[str] = Field(default_factory=list)
    author: str
    semester: str | None = None
    published_at: datetime | None = None
    rating: float | None = None
    difficulty: float | None = None
    workload_hours: float | None = None
    url: str
    title: str
    content: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OMSCentralScrapeRequest(BaseModel):
    course_slugs: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=500)
    persist: bool = True
    include_reviews: bool = True


class OMSCentralScrapeResponse(BaseModel):
    source: str = "omscentral"
    catalog_count: int
    scraped_course_count: int
    review_count: int
    persisted_document_count: int
    courses: list[CourseCatalogEntry] = Field(default_factory=list)
    reviews: list[CourseReview] = Field(default_factory=list)


class IndexCoursesRequest(BaseModel):
    course_slugs: list[str] = Field(default_factory=list)
    missing_only: bool = True
    include_reviews: bool = True
    process_after: bool = True
    limit: int | None = Field(default=None, ge=1, le=500)


class IndexCoursesResponse(BaseModel):
    job_id: str
    status: str
    message: str


class IndexJobStatus(BaseModel):
    job_id: str
    status: str
    requested_course_slugs: list[str] = Field(default_factory=list)
    missing_only: bool = True
    include_reviews: bool = True
    process_after: bool = True
    limit: int | None = None
    total_courses: int = 0
    courses_indexed: int = 0
    documents_persisted: int = 0
    processing_documents_processed: int = 0
    processing_chunks_created: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ProcessDocumentsRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    max_batches: int = Field(default=1, ge=1, le=1000)
    reprocess: bool = False
    course_slugs: list[str] = Field(default_factory=list)


class RedditDocument(BaseModel):
    document_id: str
    source_document_id: str
    source: str = "reddit"
    title: str
    url: str
    author: str
    score: int = 0
    num_comments: int = 0
    published_at: datetime | None = None
    course_id: str | None = None
    course_slug: str | None = None
    course_name: str | None = None
    course_codes: list[str] = Field(default_factory=list)
    content: str
    content_hash: str
    subreddit: str = "OMSCS"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RedditScrapeRequest(BaseModel):
    course_slugs: list[str] = Field(default_factory=list)
    posts_per_course: int = Field(default=10, ge=1, le=50)
    include_recent: bool = True
    recent_limit: int = Field(default=25, ge=1, le=100)
    persist: bool = True


class RedditScrapeResponse(BaseModel):
    source: str = "reddit"
    documents_scraped: int
    documents_persisted: int
    courses_matched: int
    documents: list[RedditDocument] = Field(default_factory=list)


class GenerateAnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    context: list[str]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class RetrievedChunk(BaseModel):
    document_id: str
    chunk_index: int
    score: float
    text: str
    dense_score: float | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    retrieval_method: str = "hybrid_rrf"
    source: str | None = None
    document_type: str | None = None
    title: str | None = None
    url: str | None = None
    course_slug: str | None = None
    course_name: str | None = None
    course_codes: list[str] = Field(default_factory=list)
    published_at: datetime | None = None


class QueryResponse(BaseModel):
    answer: str
    chunks: list[RetrievedChunk]


class CourseListResponse(BaseModel):
    courses: list[CourseCatalogEntry] = Field(default_factory=list)


class CourseDocumentSummary(BaseModel):
    document_id: str
    source_document_id: str
    source: str
    document_type: str
    title: str
    url: str
    course_slug: str | None = None
    course_name: str | None = None
    course_codes: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    chunk_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class CourseDocumentsResponse(BaseModel):
    course_slug: str
    documents: list[CourseDocumentSummary] = Field(default_factory=list)


class DocumentIngestedEvent(BaseModel):
    event: str = "document.ingested"
    document_id: str
