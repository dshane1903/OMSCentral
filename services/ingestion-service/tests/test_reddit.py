"""
Unit tests for the Reddit scraper's parsing and matching logic.
No network calls — these test the pure functions.
"""
from __future__ import annotations

import unittest

from app.scrapers.reddit import (
    build_document_content,
    build_course_search_queries,
    course_code_variants,
    extract_comment_text,
    match_course,
    parse_post_listing,
    parse_post_with_comments,
    post_to_document,
)
from app.main import build_manual_reddit_document
from shared.schemas.models import CourseCatalogEntry
from shared.schemas.models import ManualRedditDocumentRequest


def make_catalog() -> list[CourseCatalogEntry]:
    return [
        CourseCatalogEntry(
            course_id="c1",
            slug="computer-networks",
            name="Computer Networks",
            codes=["CS 6250"],
        ),
        CourseCatalogEntry(
            course_id="c2",
            slug="software-architecture-and-design",
            name="Software Architecture and Design",
            codes=["CS 6310"],
        ),
        CourseCatalogEntry(
            course_id="c3",
            slug="introduction-to-graduate-algorithms",
            name="Introduction to Graduate Algorithms",
            codes=["CS 6515"],
        ),
    ]


LISTING_FIXTURE = {
    "data": {
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "abc123",
                    "title": "CS 6250 is amazing",
                    "selftext": "I really loved this course.",
                    "author": "student1",
                    "score": 42,
                    "num_comments": 5,
                    "created_utc": 1700000000,
                    "permalink": "/r/OMSCS/comments/abc123/cs_6250_is_amazing/",
                    "is_self": True,
                },
            },
            {
                "kind": "t3",
                "data": {
                    "id": "def456",
                    "title": "Check out this link",
                    "selftext": "",
                    "author": "linkposter",
                    "score": 3,
                    "num_comments": 0,
                    "created_utc": 1700000100,
                    "permalink": "/r/OMSCS/comments/def456/check_out_this_link/",
                    "is_self": False,
                },
            },
            {
                "kind": "t3",
                "data": {
                    "id": "ghi789",
                    "title": "[removed]",
                    "selftext": "[removed]",
                    "author": "[deleted]",
                    "score": 0,
                    "num_comments": 0,
                    "created_utc": 1700000200,
                    "permalink": "/r/OMSCS/comments/ghi789/removed/",
                    "is_self": True,
                    "removed_by_category": "moderator",
                },
            },
        ],
    },
}

POST_WITH_COMMENTS_FIXTURE = [
    {
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "id": "abc123",
                        "title": "CS 6250 is amazing",
                        "selftext": "I really loved this course.",
                        "author": "student1",
                        "score": 42,
                        "num_comments": 3,
                        "created_utc": 1700000000,
                        "permalink": "/r/OMSCS/comments/abc123/cs_6250_is_amazing/",
                    },
                }
            ],
        },
    },
    {
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "body": "Great review, I agree!",
                        "author": "commenter1",
                        "score": 10,
                        "replies": "",
                    },
                },
                {
                    "kind": "t1",
                    "data": {
                        "body": "The BGP project was brutal though.",
                        "author": "commenter2",
                        "score": 7,
                        "replies": {
                            "data": {
                                "children": [
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "body": "Agreed, spent 20 hours on it.",
                                            "author": "commenter3",
                                            "score": 3,
                                            "replies": "",
                                        },
                                    }
                                ],
                            },
                        },
                    },
                },
                {
                    "kind": "t1",
                    "data": {
                        "body": "[deleted]",
                        "author": "[deleted]",
                        "score": 0,
                        "replies": "",
                    },
                },
            ],
        },
    },
]


class ParsePostListingTests(unittest.TestCase):
    def test_filters_removed_and_link_only(self):
        posts = parse_post_listing(LISTING_FIXTURE)
        # Only the self-post with content should survive
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "abc123")

    def test_extracts_post_fields(self):
        posts = parse_post_listing(LISTING_FIXTURE)
        post = posts[0]
        self.assertEqual(post["title"], "CS 6250 is amazing")
        self.assertEqual(post["author"], "student1")
        self.assertEqual(post["score"], 42)
        self.assertIn("reddit.com", post["url"])


class ParsePostWithCommentsTests(unittest.TestCase):
    def test_extracts_post_and_comments(self):
        post, comments = parse_post_with_comments(POST_WITH_COMMENTS_FIXTURE)
        self.assertEqual(post["id"], "abc123")
        self.assertEqual(post["title"], "CS 6250 is amazing")
        # Should have 3 comments (2 top-level non-deleted + 1 nested reply)
        # The [deleted] comment is filtered out
        self.assertEqual(len(comments), 3)
        self.assertIn("Great review", comments[0])
        self.assertIn("BGP project", comments[1])
        self.assertIn("20 hours", comments[2])


class MatchCourseTests(unittest.TestCase):
    def setUp(self):
        self.catalog = make_catalog()

    def test_matches_course_code_with_space(self):
        course = match_course("I just finished CS 6250", self.catalog)
        self.assertIsNotNone(course)
        self.assertEqual(course.slug, "computer-networks")

    def test_matches_course_code_with_dash(self):
        course = match_course("CS-6310 was tough", self.catalog)
        self.assertIsNotNone(course)
        self.assertEqual(course.slug, "software-architecture-and-design")

    def test_matches_course_code_no_separator(self):
        course = match_course("Anyone taking CS6515?", self.catalog)
        self.assertIsNotNone(course)
        self.assertEqual(course.slug, "introduction-to-graduate-algorithms")

    def test_matches_course_name(self):
        course = match_course("Computer Networks was my favorite", self.catalog)
        self.assertIsNotNone(course)
        self.assertEqual(course.slug, "computer-networks")


    def test_matches_known_alias(self):
        course = match_course("Is GA really as stressful as people say?", self.catalog)
        self.assertIsNotNone(course)
        self.assertEqual(course.slug, "introduction-to-graduate-algorithms")

    def test_returns_none_for_no_match(self):
        course = match_course("I love pizza", self.catalog)
        self.assertIsNone(course)


class ManualRedditDocumentTests(unittest.TestCase):
    def test_builds_deterministic_curated_reddit_document(self):
        course = make_catalog()[0]
        request = ManualRedditDocumentRequest(
            course_slug="computer-networks",
            title="CS 6250 workload discussion",
            url="https://www.reddit.com/r/OMSCS/comments/abc123/example/",
            content="Students describe Computer Networks as manageable with steady projects.",
            author="student1",
            subreddit="r/OMSCS",
        )

        doc = build_manual_reddit_document(request, course)
        second_doc = build_manual_reddit_document(request, course)

        self.assertEqual(doc.document_id, second_doc.document_id)
        self.assertEqual(doc.source, "reddit")
        self.assertEqual(doc.source_document_id.split(":", 1)[0], "manual")
        self.assertEqual(doc.course_slug, "computer-networks")
        self.assertEqual(doc.course_name, "Computer Networks")
        self.assertEqual(doc.subreddit, "OMSCS")
        self.assertEqual(doc.metadata["ingestion_mode"], "manual")


class CourseSearchQueryTests(unittest.TestCase):
    def test_course_code_variants_include_common_reddit_spellings(self):
        self.assertEqual(
            course_code_variants("CS-6210"),
            ["CS 6210", "CS-6210", "CS6210"],
        )

    def test_build_course_search_queries_includes_aliases_and_omscs_variants(self):
        course = CourseCatalogEntry(
            course_id="c4",
            slug="advanced-operating-systems",
            name="Advanced Operating Systems",
            codes=["CS-6210"],
        )

        queries = build_course_search_queries(course)

        self.assertIn("CS 6210", queries)
        self.assertIn("CS-6210 OMSCS", queries)
        self.assertIn("Advanced Operating Systems", queries)
        self.assertIn("AOS", queries)
        self.assertIn("AOS OMSCS", queries)


class ExtractCommentTextTests(unittest.TestCase):
    def test_extracts_simple_comment(self):
        comment = {
            "kind": "t1",
            "data": {
                "body": "Great post!",
                "author": "user1",
                "score": 5,
                "replies": "",
            },
        }
        texts = extract_comment_text(comment)
        self.assertEqual(len(texts), 1)
        self.assertIn("Great post!", texts[0])
        self.assertIn("user1", texts[0])

    def test_skips_deleted_comments(self):
        comment = {
            "kind": "t1",
            "data": {
                "body": "[deleted]",
                "author": "[deleted]",
                "score": 0,
                "replies": "",
            },
        }
        texts = extract_comment_text(comment)
        self.assertEqual(len(texts), 0)


class BuildDocumentContentTests(unittest.TestCase):
    def test_includes_title_body_and_comments(self):
        post = {"title": "CS 6250 review", "selftext": "Great course overall."}
        comments = ["[user1, 5 pts] Agreed!", "[user2, 3 pts] The exams are hard."]
        content = build_document_content(post, comments)
        self.assertIn("CS 6250 review", content)
        self.assertIn("Great course overall", content)
        self.assertIn("Agreed!", content)
        self.assertIn("exams are hard", content)
        self.assertIn("--- Comments ---", content)


class PostToDocumentTests(unittest.TestCase):
    def test_creates_document_with_course(self):
        catalog = make_catalog()
        post = {
            "id": "abc123",
            "title": "CS 6250 tips",
            "selftext": "Start early on the projects.",
            "author": "student1",
            "score": 15,
            "num_comments": 3,
            "created_utc": 1700000000,
            "permalink": "/r/OMSCS/comments/abc123/",
            "url": "https://www.reddit.com/r/OMSCS/comments/abc123/",
        }
        comments = ["[user1, 5 pts] Good advice!"]

        doc = post_to_document(post, comments, catalog[0])
        self.assertEqual(doc.document_id, "reddit-post-abc123")
        self.assertEqual(doc.source, "reddit")
        self.assertEqual(doc.course_id, "c1")
        self.assertEqual(doc.course_slug, "computer-networks")
        self.assertIn("CS 6250 tips", doc.content)
        self.assertIn("Good advice!", doc.content)

    def test_creates_document_without_course(self):
        post = {
            "id": "xyz999",
            "title": "General OMSCS question",
            "selftext": "How many courses per semester?",
            "author": "newbie",
            "score": 5,
            "num_comments": 1,
            "created_utc": 1700000000,
            "permalink": "/r/OMSCS/comments/xyz999/",
            "url": "https://www.reddit.com/r/OMSCS/comments/xyz999/",
        }
        doc = post_to_document(post, [], None)
        self.assertIsNone(doc.course_id)
        self.assertIsNone(doc.course_slug)


if __name__ == "__main__":
    unittest.main()
