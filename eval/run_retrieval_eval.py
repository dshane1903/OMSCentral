from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.main import retrieve_dense_only, retrieve_hybrid
from shared.schemas.models import RetrievedChunk
from shared.utils.config import get_settings
from shared.utils.service_client import post_json


@dataclass
class EvalQuestion:
    id: str
    question: str
    relevant_document_ids: set[str]
    relevant_course_slugs: set[str]


def load_questions(path: Path) -> list[EvalQuestion]:
    questions: list[EvalQuestion] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            question = payload.get("question")
            if not question:
                raise ValueError(f"{path}:{line_number} is missing question")
            questions.append(
                EvalQuestion(
                    id=payload.get("id") or f"q{line_number}",
                    question=question,
                    relevant_document_ids=set(payload.get("relevant_document_ids", [])),
                    relevant_course_slugs=set(payload.get("relevant_course_slugs", [])),
                )
            )
    return questions


async def embed_question(question: str) -> list[float]:
    settings = get_settings()
    payload = await post_json(
        f"{settings.embedding_service_url}/embed",
        {"texts": [question]},
    )
    return payload["vectors"][0]


async def retrieve(question: EvalQuestion, mode: str, top_k: int) -> list[RetrievedChunk]:
    vector = await embed_question(question.question)
    if mode == "dense":
        return retrieve_dense_only(vector, top_k)
    if mode == "hybrid":
        return retrieve_hybrid(question.question, vector, top_k)
    raise ValueError(f"Unknown mode: {mode}")


def is_relevant(chunk: RetrievedChunk, question: EvalQuestion) -> bool:
    if chunk.document_id in question.relevant_document_ids:
        return True
    if chunk.course_slug and chunk.course_slug in question.relevant_course_slugs:
        return True
    return False


def score_question(chunks: list[RetrievedChunk], question: EvalQuestion) -> dict[str, float]:
    hits = [index for index, chunk in enumerate(chunks, start=1) if is_relevant(chunk, question)]
    relevant_count = max(
        len(question.relevant_document_ids) + len(question.relevant_course_slugs),
        1,
    )
    hit_count = len(hits)
    return {
        "hit": 1.0 if hits else 0.0,
        "precision": hit_count / len(chunks) if chunks else 0.0,
        "recall": min(hit_count / relevant_count, 1.0),
        "mrr": 1.0 / hits[0] if hits else 0.0,
    }


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


async def run_eval(path: Path, mode: str, top_k: int) -> None:
    questions = load_questions(path)
    rows: list[dict[str, Any]] = []

    for question in questions:
        chunks = await retrieve(question, mode, top_k)
        scores = score_question(chunks, question)
        rows.append(scores)
        top_sources = [
            {
                "document_id": chunk.document_id,
                "course_slug": chunk.course_slug,
                "score": chunk.score,
                "dense_rank": chunk.dense_rank,
                "sparse_rank": chunk.sparse_rank,
            }
            for chunk in chunks[:3]
        ]
        print(
            json.dumps(
                {
                    "id": question.id,
                    "mode": mode,
                    "top_k": top_k,
                    **scores,
                    "top_sources": top_sources,
                },
                sort_keys=True,
            )
        )

    print(
        json.dumps(
            {
                "mode": mode,
                "top_k": top_k,
                "questions": len(questions),
                "hit_at_k": mean(row["hit"] for row in rows),
                "precision_at_k": mean(row["precision"] for row in rows),
                "recall_at_k": mean(row["recall"] for row in rows),
                "mrr": mean(row["mrr"] for row in rows),
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality.")
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("eval/questions.example.jsonl"),
        help="JSONL file with labeled retrieval questions.",
    )
    parser.add_argument(
        "--mode",
        choices=("dense", "hybrid"),
        default="hybrid",
        help="Retrieval mode to evaluate.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_eval(args.questions, args.mode, args.top_k))
