import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import (
    app,
    _fuse_candidates,
    format_llm_context,
    resolve_source_filter,
    retrieve_hybrid,
)
from shared.schemas.models import RetrievedChunk


def candidate(
    document_id: str,
    chunk_index: int,
    text: str,
    course_slug: str = "computer-networks",
    dense_score: float | None = None,
    sparse_score: float | None = None,
) -> dict:
    row = {
        "document_id": document_id,
        "chunk_index": chunk_index,
        "text": text,
        "source": "omscentral",
        "document_type": "review",
        "title": f"{course_slug} review",
        "url": "https://example.test/review",
        "course_slug": course_slug,
        "course_name": course_slug.replace("-", " ").title(),
        "course_codes": ["CS-6250"],
        "published_at": None,
    }
    if dense_score is not None:
        row["dense_score"] = dense_score
    if sparse_score is not None:
        row["sparse_score"] = sparse_score
    return row


class RetrievalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.set_cached_json")
    @patch("app.main.get_cached_json", return_value=None)
    @patch("app.main.retrieve_hybrid")
    @patch("app.main.post_json", new_callable=AsyncMock)
    @patch(
        "app.main.resolve_course_scopes",
        return_value=[{"slug": "computer-networks", "chunk_count": 12}],
    )
    def test_retrieve_route_embeds_filters_and_generates_answer(
        self,
        resolve_course_scopes,
        post_json,
        retrieve_hybrid_mock,
        get_cached_json,
        set_cached_json,
    ):
        chunk = RetrievedChunk(
            document_id="doc-1",
            chunk_index=0,
            score=0.5,
            text="Course: Computer Networks\n\nWorkload is manageable.",
            course_slug="computer-networks",
            course_name="Computer Networks",
            course_codes=["CS-6250"],
        )
        post_json.side_effect = [
            {"vectors": [[0.1, 0.2, 0.3]]},
            {"answer": "CS 6250 is usually manageable."},
        ]
        retrieve_hybrid_mock.return_value = [chunk]

        response = self.client.post(
            "/retrieve",
            json={"question": "How hard is CS 6250?", "top_k": 4},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], "CS 6250 is usually manageable.")
        self.assertEqual(payload["chunks"][0]["course_slug"], "computer-networks")
        llm_payload = post_json.await_args_list[1].args[1]
        self.assertIn("Source: unknown source", llm_payload["context"][0])
        self.assertIn("Evidence:\nCourse: Computer Networks", llm_payload["context"][0])
        resolve_course_scopes.assert_called_once_with("How hard is CS 6250?")
        retrieve_hybrid_mock.assert_called_once_with(
            "How hard is CS 6250?",
            [0.1, 0.2, 0.3],
            4,
            course_slugs=["computer-networks"],
            sources=None,
        )
        self.assertEqual(post_json.await_count, 2)
        set_cached_json.assert_called_once()

    @patch("app.main.set_cached_json")
    @patch("app.main.get_cached_json", return_value=None)
    @patch("app.main.retrieve_hybrid")
    @patch("app.main.post_json", new_callable=AsyncMock)
    @patch(
        "app.main.resolve_course_scopes",
        return_value=[{"slug": "advanced-malware", "chunk_count": 0}],
    )
    def test_retrieve_route_does_not_embed_when_scoped_course_has_no_chunks(
        self,
        resolve_course_scopes,
        post_json,
        retrieve_hybrid_mock,
        get_cached_json,
        set_cached_json,
    ):
        post_json.return_value = {
            "answer": "I could not find relevant context in the uploaded documents."
        }

        response = self.client.post(
            "/retrieve",
            json={"question": "Tell me about CS 6747", "top_k": 5},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["chunks"], [])
        retrieve_hybrid_mock.assert_not_called()
        post_json.assert_awaited_once()
        llm_payload = post_json.await_args.args[1]
        self.assertEqual(llm_payload["context"], [])
        set_cached_json.assert_not_called()

    @patch("app.main._fetch_sparse_candidates")
    @patch("app.main._fetch_dense_candidates")
    @patch("app.main.db_connection")
    def test_multi_course_hybrid_retrieval_interleaves_course_batches(
        self,
        db_connection,
        fetch_dense_candidates,
        fetch_sparse_candidates,
    ):
        cursor = MagicMock()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        db_connection.return_value = connection

        def dense_side_effect(cursor_arg, vector, limit, course_slugs=None, sources=None):
            slug = course_slugs[0]
            return [
                candidate(f"{slug}-dense-1", 0, f"{slug} dense one", slug, dense_score=0.9),
                candidate(f"{slug}-dense-2", 0, f"{slug} dense two", slug, dense_score=0.8),
            ]

        def sparse_side_effect(cursor_arg, question, limit, course_slugs=None, sources=None):
            slug = course_slugs[0]
            return [
                candidate(f"{slug}-sparse-1", 0, f"{slug} sparse one", slug, sparse_score=0.7)
            ]

        fetch_dense_candidates.side_effect = dense_side_effect
        fetch_sparse_candidates.side_effect = sparse_side_effect

        chunks = retrieve_hybrid(
            "Compare CS 6250 and CS 6200",
            [0.1, 0.2],
            top_k=4,
            course_slugs=["computer-networks", "operating-systems"],
        )

        dense_scopes = [
            call.kwargs["course_slugs"][0]
            for call in fetch_dense_candidates.call_args_list
        ]
        sparse_scopes = [
            call.kwargs["course_slugs"][0]
            for call in fetch_sparse_candidates.call_args_list
        ]
        self.assertEqual(dense_scopes, ["computer-networks", "operating-systems"])
        self.assertEqual(sparse_scopes, ["computer-networks", "operating-systems"])
        self.assertTrue(
            all(call.kwargs["sources"] is None for call in fetch_dense_candidates.call_args_list)
        )
        self.assertEqual(
            {chunk.course_slug for chunk in chunks},
            {"computer-networks", "operating-systems"},
        )
        self.assertTrue(all(chunk.retrieval_method == "hybrid_rrf" for chunk in chunks))

    @patch("app.main.set_cached_json")
    @patch("app.main.get_cached_json", return_value=None)
    @patch("app.main.retrieve_hybrid")
    @patch("app.main.post_json", new_callable=AsyncMock)
    @patch(
        "app.main.resolve_course_scopes",
        return_value=[{"slug": "computer-networks", "chunk_count": 12}],
    )
    def test_retrieve_route_filters_to_reddit_when_question_asks_for_reddit(
        self,
        resolve_course_scopes,
        post_json,
        retrieve_hybrid_mock,
        get_cached_json,
        set_cached_json,
    ):
        post_json.side_effect = [
            {"vectors": [[0.1, 0.2, 0.3]]},
            {"answer": "Reddit answer."},
        ]
        retrieve_hybrid_mock.return_value = [
            RetrievedChunk(
                document_id="reddit-doc-1",
                chunk_index=0,
                score=0.5,
                text="Course: Computer Networks\n\nA Reddit thread says readings were optional.",
                source="reddit",
                course_slug="computer-networks",
                course_name="Computer Networks",
                course_codes=["CS-6250"],
            )
        ]

        response = self.client.post(
            "/retrieve",
            json={
                "question": "What do Reddit discussions say about CS 6250?",
                "top_k": 4,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chunks"][0]["source"], "reddit")
        retrieve_hybrid_mock.assert_called_once_with(
            "What do Reddit discussions say about CS 6250?",
            [0.1, 0.2, 0.3],
            4,
            course_slugs=["computer-networks"],
            sources=["reddit"],
        )

    def test_source_filter_detects_explicit_source_requests(self):
        self.assertEqual(
            resolve_source_filter("What do Reddit discussions say about CS 6250?"),
            ["reddit"],
        )
        self.assertEqual(
            resolve_source_filter("What does OMSCentral say about CS 6250?"),
            ["omscentral"],
        )
        self.assertIsNone(resolve_source_filter("How hard is CS 6250?"))

    def test_format_llm_context_preserves_source_metadata(self):
        chunk = RetrievedChunk(
            document_id="reddit-doc-1",
            chunk_index=0,
            score=0.5,
            text="Students say the exams are stressful.",
            source="reddit",
            title="CS 6515 exam thread",
            course_slug="introduction-to-graduate-algorithms",
            course_name="Introduction to Graduate Algorithms",
            course_codes=["CS-6515"],
        )

        context = format_llm_context(chunk)

        self.assertIn("Source: Reddit discussion", context)
        self.assertIn("Title: CS 6515 exam thread", context)
        self.assertIn("Course: Introduction to Graduate Algorithms (CS-6515)", context)
        self.assertIn("Evidence:\nStudents say the exams are stressful.", context)

    def test_rrf_fuses_dense_and_sparse_hits_without_duplicates(self):
        dense_rows = [
            candidate("doc-1", 0, "dense and sparse", dense_score=0.8),
            candidate("doc-2", 0, "dense only", dense_score=0.7),
        ]
        sparse_rows = [
            candidate("doc-1", 0, "dense and sparse", sparse_score=0.6),
            candidate("doc-3", 0, "sparse only", sparse_score=0.5),
        ]

        chunks = _fuse_candidates(dense_rows, sparse_rows, top_k=5)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].document_id, "doc-1")
        self.assertEqual(chunks[0].dense_rank, 1)
        self.assertEqual(chunks[0].sparse_rank, 1)
        self.assertEqual(chunks[0].retrieval_method, "hybrid_rrf")


if __name__ == "__main__":
    unittest.main()
