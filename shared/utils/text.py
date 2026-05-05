import math
import re


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text.strip():
        return []

    chunks: list[str] = []
    start = 0
    step = max(chunk_size - overlap, 1)

    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        start += step

    return [chunk for chunk in chunks if chunk]


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())


def split_sentences(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|(?<=;)\s+(?=[A-Z0-9])", normalized)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def semantic_chunk_text(
    text: str,
    sentence_vectors: list[list[float]] | None = None,
    max_chunk_size: int = 900,
    min_chunk_size: int = 300,
    fallback_overlap: int = 80,
) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []

    if len(sentences) == 1:
        return chunk_text(
            sentences[0],
            chunk_size=max_chunk_size,
            overlap=fallback_overlap,
        )

    if sentence_vectors is None or len(sentence_vectors) != len(sentences):
        return _sentence_chunk_text(sentences, max_chunk_size=max_chunk_size)

    similarities = [
        cosine_similarity(left, right)
        for left, right in zip(sentence_vectors, sentence_vectors[1:])
    ]
    threshold = _adaptive_similarity_threshold(similarities)
    chunks: list[str] = []
    current: list[str] = []

    for index, sentence in enumerate(sentences):
        candidate = " ".join([*current, sentence]).strip()
        if current and len(candidate) > max_chunk_size:
            chunks.append(" ".join(current).strip())
            current = [sentence]
        else:
            current.append(sentence)

        current_text = " ".join(current).strip()
        next_similarity = similarities[index] if index < len(similarities) else None
        should_break = (
            next_similarity is not None
            and next_similarity <= threshold
            and len(current_text) >= min_chunk_size
        )
        if should_break:
            chunks.append(current_text)
            current = []

    if current:
        chunks.append(" ".join(current).strip())

    return _merge_small_chunks(chunks, max_chunk_size=max_chunk_size)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _sentence_chunk_text(sentences: list[str], max_chunk_size: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*current, sentence]).strip()
        if current and len(candidate) > max_chunk_size:
            chunks.append(" ".join(current).strip())
            current = [sentence]
        else:
            current.append(sentence)

    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def _adaptive_similarity_threshold(similarities: list[float]) -> float:
    if not similarities:
        return 0.0

    sorted_scores = sorted(similarities)
    quartile_index = max(0, int(len(sorted_scores) * 0.25) - 1)
    return sorted_scores[quartile_index]


def _merge_small_chunks(chunks: list[str], max_chunk_size: int) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if merged and len(merged[-1]) + len(chunk) + 1 <= max_chunk_size:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged
