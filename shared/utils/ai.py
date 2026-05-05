import hashlib
import math
from typing import Iterable

from openai import AsyncOpenAI

from shared.utils.config import get_settings


def configured_llm_provider() -> str:
    return get_settings().llm_provider.strip().lower()


def has_openai_credentials() -> bool:
    settings = get_settings()
    return bool(settings.openai_api_key and settings.openai_api_key != "replace-me")


def has_anthropic_credentials() -> bool:
    settings = get_settings()
    return bool(
        settings.anthropic_api_key
        and settings.anthropic_api_key != "replace-me"
    )


def get_openai_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(api_key=settings.openai_api_key)


def deterministic_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    tokens = text.lower().split() or [text.lower() or "empty"]

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for offset in range(0, min(len(digest), dimensions), 2):
            index = digest[offset] % dimensions
            sign = 1.0 if digest[offset + 1] % 2 == 0 else -1.0
            vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


async def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    settings = get_settings()
    values = list(texts)

    if has_openai_credentials():
        response = await get_openai_client().embeddings.create(
            model=settings.openai_embedding_model,
            input=values,
        )
        return [item.embedding for item in response.data]

    return [
        deterministic_embedding(text, settings.embedding_dimensions)
        for text in values
    ]


def fallback_answer(question: str, context: list[str]) -> str:
    if not context:
        return "I could not find relevant context in the uploaded documents."

    joined_context = " ".join(context[:3]).strip()
    preview = joined_context[:600]
    return (
        f"Question: {question}\n"
        f"Grounded summary: {preview}"
    )
