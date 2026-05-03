
from __future__ import annotations

import json
import logging
from typing import Any

from shared.utils.config import get_settings
from shared.utils.db import db_connection, serialize_vector
from shared.utils.service_client import post_json
from shared.utils.text import chunk_text, normalize_text

logger = logging.getLogger("processing-service")

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
EMBEDDING_BATCH_SIZE = 32


def fetch_unchunked_documents(limit: int = 50) -> list[dict[str, Any]]:
    """Find documents that haven't been chunked yet."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    source,
                    document_type,
                    title,
                    course_slug,
                    course_name,
                    course_codes,
                    content,
                    metadata
                FROM documents
                WHERE chunk_count = 0
                  AND content != ''
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())


def fetch_document_by_id(document_id: str) -> dict[str, Any] | None:
    """Load a single document row, regardless of chunk state."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    source,
                    document_type,
                    title,
                    course_slug,
                    course_name,
                    course_codes,
                    content,
                    metadata,
                    chunk_count
                FROM documents
                WHERE id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def build_chunk_text(doc: dict[str, Any], raw_chunk: str) -> str:
    """Prepend course context to each chunk so retrieval has metadata."""
    meta = doc.get("metadata")
    if isinstance(meta, str):
        meta = json.loads(meta)
    elif meta is None:
        meta = {}

    header_parts: list[str] = []

    if doc.get("course_name"):
        codes = doc.get("course_codes") or []
        if codes:
            code_str = ", ".join(codes)
            header_parts.append(f"Course: {doc['course_name']} ({code_str})")
        else:
            header_parts.append(f"Course: {doc['course_name']}")

    semester = meta.get("semester")
    if semester:
        header_parts.append(f"Semester: {semester}")

    rating = meta.get("rating")
    difficulty = meta.get("difficulty")
    workload = meta.get("workload_hours")
    stats: list[str] = []
    if rating is not None:
        stats.append(f"Rating: {rating}/5")
    if difficulty is not None:
        stats.append(f"Difficulty: {difficulty}/5")
    if workload is not None:
        stats.append(f"Workload: {workload} hrs/week")
    if stats:
        header_parts.append(" | ".join(stats))

    if header_parts:
        header = " — ".join(header_parts)
        return f"{header}\n\n{raw_chunk}"

    return raw_chunk


def chunk_document(doc: dict[str, Any]) -> list[str]:
    """Split a document into retrieval-ready chunks with context headers."""
    content = normalize_text(doc.get("content") or "")
    if not content:
        return []

    raw_chunks = chunk_text(content, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    return [build_chunk_text(doc, chunk) for chunk in raw_chunks]


async def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """Call the embedding service in batches."""
    settings = get_settings()
    all_vectors: list[list[float]] = []

    for i in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[i : i + EMBEDDING_BATCH_SIZE]
        response = await post_json(
            f"{settings.embedding_service_url}/embed",
            {"texts": batch},
        )
        all_vectors.extend(response["vectors"])

    return all_vectors


def write_chunks(
    document_id: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> int:
    """Write chunks + embeddings to DB and update document chunk_count."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            # Delete any existing chunks for this document (idempotent re-processing)
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))

            for index, (text, vector) in enumerate(zip(chunks, vectors)):
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, chunk_index, text, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (document_id, index, text, serialize_vector(vector)),
                )

            cur.execute(
                "UPDATE documents SET chunk_count = %s, updated_at = NOW() WHERE id = %s",
                (len(chunks), document_id),
            )
        conn.commit()

    return len(chunks)


async def process_unchunked_documents(limit: int = 50) -> dict[str, Any]:
    """Main processing loop: find unchunked docs, chunk, embed, store."""
    documents = fetch_unchunked_documents(limit=limit)

    if not documents:
        return {
            "documents_processed": 0,
            "chunks_created": 0,
            "errors": [],
        }

    total_chunks = 0
    errors: list[dict[str, str]] = []

    for doc in documents:
        doc_id = doc["id"]
        try:
            written = await _chunk_and_embed(doc)
            total_chunks += written
        except Exception as exc:
            logger.error("Failed to process document %s: %s", doc_id, exc)
            errors.append({"document_id": doc_id, "error": str(exc)})

    return {
        "documents_processed": len(documents) - len(errors),
        "chunks_created": total_chunks,
        "errors": errors,
    }


async def _chunk_and_embed(doc: dict[str, Any]) -> int:
    """Chunk one document, embed it, and write the chunks. Returns chunk count."""
    doc_id = doc["id"]
    chunks = chunk_document(doc)
    if not chunks:
        # Mark as processed even if no chunks (empty content) so we don't
        # re-scan it forever on the next poll.
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET chunk_count = -1, updated_at = NOW() WHERE id = %s",
                    (doc_id,),
                )
            conn.commit()
        return 0

    vectors = await embed_chunks(chunks)
    written = write_chunks(doc_id, chunks, vectors)
    logger.info(
        "Chunked document %s (%s): %d chunks",
        doc_id,
        doc.get("course_slug", "unknown"),
        written,
    )
    return written


async def process_one_document(document_id: str) -> bool:
    """
    Process a single document by id. Used by the RabbitMQ consumer.

    Returns True if the message can be acked (success or terminal "no content"
    state), False if the broker should retry. Raises only on programmer errors;
    expected runtime failures (embedding service down, etc.) return False so
    the message goes through the retry pipeline.
    """
    doc = fetch_document_by_id(document_id)
    if doc is None:
        # Document was deleted between publish and consume. Nothing to retry.
        logger.warning("Document %s not found; acking event", document_id)
        return True

    if doc.get("chunk_count", 0) and doc["chunk_count"] != 0:
        # Already processed (positive count) or skipped as empty (-1). Idempotent.
        logger.debug("Document %s already processed (chunk_count=%s)",
                     document_id, doc["chunk_count"])
        return True

    try:
        await _chunk_and_embed(doc)
        return True
    except Exception as exc:
        logger.error("Processing failed for %s: %s", document_id, exc)
        return False