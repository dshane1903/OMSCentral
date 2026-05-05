import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app


class ApiGatewayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_query_proxies_to_retrieval_service(self, post_json):
        post_json.return_value = {
            "answer": "Computer Networks is usually manageable.",
            "chunks": [
                {
                    "document_id": "doc-1",
                    "chunk_index": 0,
                    "score": 0.42,
                    "text": "Course: Computer Networks\n\nManageable workload.",
                    "course_slug": "computer-networks",
                    "course_name": "Computer Networks",
                    "course_codes": ["CS-6250"],
                }
            ],
        }

        response = self.client.post(
            "/query",
            json={"question": "How hard is CS 6250?", "top_k": 3},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["answer"], "Computer Networks is usually manageable.")
        self.assertEqual(payload["chunks"][0]["course_slug"], "computer-networks")
        post_json.assert_awaited_once()
        url, forwarded_payload = post_json.await_args.args[:2]
        self.assertTrue(url.endswith("/retrieve"))
        self.assertEqual(
            forwarded_payload,
            {"question": "How hard is CS 6250?", "top_k": 3},
        )

    @patch("app.main.get_json", new_callable=AsyncMock)
    def test_course_404_is_preserved_from_retrieval_service(self, get_json):
        request = httpx.Request("GET", "http://retrieval-service:8003/courses/missing")
        response = httpx.Response(
            404,
            request=request,
            json={"detail": "Unknown course slug: missing"},
        )
        get_json.side_effect = httpx.HTTPStatusError(
            "not found",
            request=request,
            response=response,
        )

        result = self.client.get("/courses/missing")

        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.json()["detail"], "Unknown course slug: missing")

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_process_forwards_reprocess_options(self, post_json):
        post_json.return_value = {
            "documents_processed": 2,
            "chunks_created": 6,
            "errors": [],
        }

        response = self.client.post(
            "/process",
            json={
                "limit": 25,
                "max_batches": 4,
                "reprocess": True,
                "course_slugs": ["computer-networks"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chunks_created"], 6)
        _, forwarded_payload = post_json.await_args.args[:2]
        self.assertEqual(
            forwarded_payload,
            {
                "limit": 25,
                "max_batches": 4,
                "reprocess": True,
                "course_slugs": ["computer-networks"],
            },
        )


if __name__ == "__main__":
    unittest.main()
