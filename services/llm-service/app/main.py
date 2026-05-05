import httpx
from fastapi import FastAPI

from shared.schemas.models import GenerateAnswerRequest
from shared.utils.ai import (
    configured_llm_provider,
    fallback_answer,
    get_openai_client,
    has_anthropic_credentials,
    has_openai_credentials,
)
from shared.utils.config import get_settings
from shared.utils.observability import LLM_REQUESTS, instrument_fastapi_app

app = FastAPI(title="RAG LLM Service", version="0.1.0")
instrument_fastapi_app(app, "llm-service")
settings = get_settings()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "llm-service"}


@app.post("/generate")
async def generate_answer(request: GenerateAnswerRequest) -> dict[str, str]:
    provider = configured_llm_provider()

    if provider == "anthropic" and has_anthropic_credentials():
        return await generate_anthropic_answer(request)
    if provider == "openai" and has_openai_credentials():
        return await generate_openai_answer(request)

    LLM_REQUESTS.labels(provider="fallback", status="success").inc()
    return {"answer": fallback_answer(request.question, request.context)}


async def generate_openai_answer(request: GenerateAnswerRequest) -> dict[str, str]:
    try:
        response = await get_openai_client().chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": user_prompt(request)},
            ],
            temperature=0.1,
        )
    except Exception:
        LLM_REQUESTS.labels(provider="openai", status="failure").inc()
        raise

    answer = response.choices[0].message.content or fallback_answer(
        request.question,
        request.context,
    )
    LLM_REQUESTS.labels(provider="openai", status="success").inc()
    return {"answer": answer}


async def generate_anthropic_answer(request: GenerateAnswerRequest) -> dict[str, str]:
    payload = {
        "model": settings.anthropic_chat_model,
        "system": system_prompt(),
        "messages": [
            {
                "role": "user",
                "content": user_prompt(request),
            }
        ],
        "temperature": 0.1,
        "max_tokens": 900,
    }
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": settings.anthropic_api_version,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
    except Exception:
        LLM_REQUESTS.labels(provider="anthropic", status="failure").inc()
        raise

    answer = extract_anthropic_text(response.json()) or fallback_answer(
        request.question,
        request.context,
    )
    LLM_REQUESTS.labels(provider="anthropic", status="success").inc()
    return {"answer": answer}


def system_prompt() -> str:
    return (
        "You answer questions using only the provided context. "
        "If the answer is not supported by the context, say so clearly. "
        "Use concise Markdown formatting when it improves readability. "
        "Prefer short sections and bullets for comparisons; avoid Markdown tables "
        "unless the user explicitly asks for a table."
    )


def user_prompt(request: GenerateAnswerRequest) -> str:
    joined_context = "\n\n".join(
        f"Context {index + 1}:\n{chunk}"
        for index, chunk in enumerate(request.context)
    )
    return f"Question: {request.question}\n\nRetrieved context:\n{joined_context}"


def extract_anthropic_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("content", []):
        if item.get("type") == "text" and item.get("text"):
            parts.append(item["text"])
    return "\n".join(parts).strip()
