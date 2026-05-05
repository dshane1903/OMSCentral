import unittest

from shared.utils.text import semantic_chunk_text, split_sentences


class SemanticChunkingTests(unittest.TestCase):
    def test_split_sentences_preserves_sentence_boundaries(self):
        text = "Projects are hard. Exams are fair! Workload varies? Plan ahead."

        self.assertEqual(
            split_sentences(text),
            [
                "Projects are hard.",
                "Exams are fair!",
                "Workload varies?",
                "Plan ahead.",
            ],
        )

    def test_semantic_chunking_does_not_slice_mid_sentence(self):
        text = (
            "Projects are hard and require careful debugging. "
            "The workload is highest near the final assignment. "
            "Lectures are short and mostly conceptual. "
            "Exams are fair if you review the papers."
        )
        vectors = [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.9, 0.1],
        ]

        chunks = semantic_chunk_text(
            text,
            sentence_vectors=vectors,
            max_chunk_size=140,
            min_chunk_size=40,
        )

        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertRegex(chunk, r"[.!?]$")
            self.assertNotIn(" assignment. Lectures", chunk)


if __name__ == "__main__":
    unittest.main()
