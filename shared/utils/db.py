from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from shared.utils.config import get_settings


SCHEMA_SQL_TEMPLATE = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS course_catalog (
    course_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    codes TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    credit_hours INTEGER,
    description TEXT,
    rating DOUBLE PRECISION,
    difficulty DOUBLE PRECISION,
    workload DOUBLE PRECISION,
    review_count INTEGER NOT NULL DEFAULT 0,
    official_url TEXT,
    syllabus_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_document_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    course_id TEXT REFERENCES course_catalog(course_id) ON DELETE SET NULL,
    course_slug TEXT,
    course_name TEXT,
    course_codes TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    published_at TIMESTAMPTZ,
    content TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_document_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR({embedding_dimensions}) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_course_catalog_source_slug ON course_catalog (source, slug);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_documents_course_slug ON documents (course_slug);
CREATE INDEX IF NOT EXISTS idx_documents_source_document ON documents (source, source_document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ivfflat
ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""


def get_connection_string() -> str:
    settings = get_settings()
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


@contextmanager
def db_connection(autocommit: bool = False) -> Iterator[psycopg.Connection[Any]]:
    connection = psycopg.connect(
        get_connection_string(),
        autocommit=autocommit,
        row_factory=dict_row,
    )
    try:
        yield connection
    finally:
        connection.close()


def ensure_schema() -> None:
    settings = get_settings()
    schema_sql = SCHEMA_SQL_TEMPLATE.format(
        embedding_dimensions=settings.embedding_dimensions,
    )
    with db_connection(autocommit=True) as connection:
        connection.execute(schema_sql)


def serialize_vector(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.10f}" for value in vector) + "]"
