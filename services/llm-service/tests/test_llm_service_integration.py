import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, extract_anthropic_text, system_prompt, user_prompt
from shared.schemas.models import GenerateAnswerRequest


class LlmServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.has_anthropic_credentials", return_value=True)
    @patch("app.main.configured_llm_provider", return_value="anthropic")
    @patch("app.main.generate_anthropic_answer", new_callable=AsyncMock)
    def test_generate_uses_anthropic_when_configured(
        self,
        generate_anthropic_answer,
        configured_llm_provider,
        has_anthropic_credentials,
    ):
        generate_anthropic_answer.return_value = {"answer": "Claude answer"}

        response = self.client.post(
            "/generate",
            json={"question": "Compare CS 6200 and CS 6250", "context": ["ctx"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "Claude answer"})
        configured_llm_provider.assert_called_once()
        has_anthropic_credentials.assert_called_once()
        generate_anthropic_answer.assert_awaited_once()

    @patch("app.main.has_openai_credentials", return_value=False)
    @patch("app.main.configured_llm_provider", return_value="openai")
    def test_generate_falls_back_without_configured_credentials(
        self,
        configured_llm_provider,
        has_openai_credentials,
    ):
        response = self.client.post(
            "/generate",
            json={
                "question": "How hard is CS 6250?",
                "context": ["CS 6250 is often described as manageable."],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Grounded summary", response.json()["answer"])
        configured_llm_provider.assert_called_once()
        has_openai_credentials.assert_called_once()

    def test_prompt_keeps_context_grounding_and_markdown_guidance(self):
        request = GenerateAnswerRequest(
            question="How hard is CS 6250?",
            context=["Context chunk one", "Context chunk two"],
        )

        self.assertIn("only the provided context", system_prompt())
        self.assertIn("OMSCentral as structured course-review evidence", system_prompt())
        self.assertIn("Reddit as anecdotal discussion evidence", system_prompt())
        self.assertIn("avoid Markdown tables", system_prompt())
        prompt = user_prompt(request)
        self.assertIn("Question: How hard is CS 6250?", prompt)
        self.assertIn("Context 1:\nContext chunk one", prompt)
        self.assertIn("Context 2:\nContext chunk two", prompt)

    def test_extract_anthropic_text_joins_text_blocks_only(self):
        payload = {
            "content": [
                {"type": "text", "text": "First paragraph."},
                {"type": "tool_use", "name": "ignored"},
                {"type": "text", "text": "Second paragraph."},
            ]
        }

        self.assertEqual(
            extract_anthropic_text(payload),
            "First paragraph.\nSecond paragraph.",
        )


if __name__ == "__main__":
    unittest.main()
