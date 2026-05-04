from fastapi import FastAPI

from shared.schemas.models import GenerateAnswerRequest
from shared.utils.ai import fallback_answer, get_openai_client, has_openai_credentials
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
    if not has_openai_credentials():
        LLM_REQUESTS.labels(provider="fallback", status="success").inc()
        return {"answer": fallback_answer(request.question, request.context)}

    system_prompt = (
        "You answer questions using only the provided context. "
        "If the answer is not supported by the context, say so clearly."
    )
    joined_context = "\n\n".join(
        f"Context {index + 1}:\n{chunk}"
        for index, chunk in enumerate(request.context)
    )
    user_prompt = f"Question: {request.question}\n\nRetrieved context:\n{joined_context}"

    try:
        response = await get_openai_client().chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
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
