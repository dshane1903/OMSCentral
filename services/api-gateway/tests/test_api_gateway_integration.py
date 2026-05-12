import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app, settings


class ApiGatewayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_rate_limit_enabled = settings.rate_limit_enabled
        self.original_admin_api_key = settings.admin_api_key

    def tearDown(self):
        settings.rate_limit_enabled = self.original_rate_limit_enabled
        settings.admin_api_key = self.original_admin_api_key

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_query_proxies_to_retrieval_service(self, post_json):
        settings.rate_limit_enabled = False
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
        settings.admin_api_key = "test-admin-token"
        post_json.return_value = {
            "documents_processed": 2,
            "chunks_created": 6,
            "errors": [],
        }

        response = self.client.post(
            "/process",
            headers={"x-admin-token": "test-admin-token"},
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

    @patch("app.main.get_redis_client")
    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_query_rate_limit_returns_429_after_minute_budget(
        self,
        post_json,
        get_redis_client,
    ):
        settings.rate_limit_enabled = True
        settings.query_rate_limit_per_minute = 1
        settings.query_rate_limit_per_day = 100
        post_json.return_value = {"answer": "ok", "chunks": []}

        class FakePipeline:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def incr(self, key):
                return self

            def expire(self, key, seconds):
                return self

            def execute(self):
                return [2, True, 2, True]

        class FakeRedis:
            def pipeline(self):
                return FakePipeline()

        get_redis_client.return_value = FakeRedis()

        response = self.client.post(
            "/query",
            json={"question": "How hard is CS 6250?", "top_k": 3},
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        post_json.assert_not_awaited()

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_admin_endpoint_rejects_missing_token(self, post_json):
        settings.admin_api_key = "test-admin-token"

        response = self.client.post(
            "/index/courses",
            json={"course_slugs": ["computer-networks"]},
        )

        self.assertEqual(response.status_code, 401)
        post_json.assert_not_awaited()

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_admin_endpoint_rejects_placeholder_admin_key(self, post_json):
        settings.admin_api_key = "replace-me"

        response = self.client.post(
            "/index/courses",
            headers={"x-admin-token": "replace-me"},
            json={"course_slugs": ["computer-networks"]},
        )

        self.assertEqual(response.status_code, 503)
        post_json.assert_not_awaited()

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_admin_endpoint_accepts_bearer_token(self, post_json):
        settings.admin_api_key = "test-admin-token"
        post_json.return_value = {
            "job_id": "job-1",
            "status": "queued",
            "message": "Indexing started.",
        }

        response = self.client.post(
            "/index/courses",
            headers={"authorization": "Bearer test-admin-token"},
            json={"course_slugs": ["computer-networks"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], "job-1")
        post_json.assert_awaited_once()

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_reddit_index_endpoint_forwards_backfill_options(self, post_json):
        settings.admin_api_key = "test-admin-token"
        post_json.return_value = {
            "job_id": "reddit-job-1",
            "status": "queued",
            "message": "Reddit indexing job queued.",
        }

        response = self.client.post(
            "/index/reddit",
            headers={"x-admin-token": "test-admin-token"},
            json={
                "course_slugs": ["introduction-to-graduate-algorithms"],
                "missing_only": True,
                "posts_per_course": 12,
                "include_aliases": True,
                "search_modes": ["relevance_all", "top_all"],
                "max_search_results_per_query": 30,
                "process_after": True,
                "limit": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], "reddit-job-1")
        url, forwarded_payload = post_json.await_args.args[:2]
        self.assertTrue(url.endswith("/index/reddit"))
        self.assertEqual(
            forwarded_payload,
            {
                "course_slugs": ["introduction-to-graduate-algorithms"],
                "missing_only": True,
                "posts_per_course": 12,
                "include_aliases": True,
                "search_modes": ["relevance_all", "top_all"],
                "max_search_results_per_query": 30,
                "process_after": True,
                "limit": 1,
            },
        )

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_manual_reddit_source_endpoint_forwards_curated_source(self, post_json):
        settings.admin_api_key = "test-admin-token"
        post_json.return_value = {
            "source": "reddit",
            "document_id": "reddit-manual-abc123",
            "source_document_id": "manual:abc123",
            "documents_persisted": 1,
            "processing_documents_processed": 1,
            "processing_chunks_created": 2,
            "status": "processed",
        }

        response = self.client.post(
            "/sources/reddit/manual",
            headers={"x-admin-token": "test-admin-token"},
            json={
                "course_slug": "computer-networks",
                "title": "CS 6250 workload discussion",
                "url": "https://www.reddit.com/r/OMSCS/comments/abc123/example/",
                "content": "Students in this thread describe CS 6250 as manageable but project-heavy.",
                "author": "student1",
                "subreddit": "OMSCS",
                "process_after": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        url, forwarded_payload = post_json.await_args.args[:2]
        self.assertTrue(url.endswith("/sources/reddit/manual"))
        self.assertEqual(
            forwarded_payload,
            {
                "course_slug": "computer-networks",
                "title": "CS 6250 workload discussion",
                "url": "https://www.reddit.com/r/OMSCS/comments/abc123/example/",
                "content": (
                    "Students in this thread describe CS 6250 as manageable "
                    "but project-heavy."
                ),
                "author": "student1",
                "subreddit": "OMSCS",
                "published_at": None,
                "score": 0,
                "num_comments": 0,
                "process_after": True,
                "metadata": {},
            },
        )

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_delete_documents_endpoint_requires_admin_and_forwards_ids(self, post_json):
        settings.admin_api_key = "test-admin-token"
        post_json.return_value = {
            "requested_count": 2,
            "deleted_count": 2,
            "deleted_document_ids": ["doc-1", "doc-2"],
        }

        response = self.client.post(
            "/documents/delete",
            headers={"authorization": "Bearer test-admin-token"},
            json={"document_ids": ["doc-1", "doc-2"], "source": "reddit"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_count"], 2)
        url, forwarded_payload = post_json.await_args.args[:2]
        self.assertTrue(url.endswith("/documents/delete"))
        self.assertEqual(
            forwarded_payload,
            {"document_ids": ["doc-1", "doc-2"], "source": "reddit"},
        )

    @patch("app.main.post_json", new_callable=AsyncMock)
    def test_query_rejects_oversized_question_before_proxying(self, post_json):
        settings.rate_limit_enabled = False

        response = self.client.post(
            "/query",
            json={"question": "x" * 2001, "top_k": 3},
        )

        self.assertEqual(response.status_code, 422)
        post_json.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
