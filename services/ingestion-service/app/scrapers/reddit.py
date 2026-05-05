"""
Reddit scraper for r/OMSCS course discussions.

Uses Reddit's public JSON API (no OAuth required for public subreddits).
Each post + its top comments becomes a single document, linked to a course
when the post title or body mentions a known course code.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from shared.schemas.models import CourseCatalogEntry, RedditDocument
from shared.utils.text import normalize_text

if TYPE_CHECKING:
    from shared.utils.config import Settings

logger = logging.getLogger("reddit-scraper")

SUBREDDIT = "OMSCS"
MAX_COMMENTS_PER_POST = 15
REQUEST_DELAY_SECONDS = 1.2  # Reddit asks for ~1 req/sec unauthenticated


def build_reddit_document_id(post_id: str) -> str:
    return f"reddit-post-{post_id}"


def build_source_document_id(post_id: str) -> str:
    return post_id


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def match_course(
    text: str,
    catalog: list[CourseCatalogEntry],
) -> CourseCatalogEntry | None:
    """
    Match a post to a course by scanning for course codes (e.g. CS 6250,
    CS-6310, CS6515) in the text. Returns the first match found.
    """
    upper = text.upper()
    for course in catalog:
        for code in course.codes:
            # Match variations: "CS 6250", "CS-6250", "CS6250"
            normalized_code = code.upper().replace("-", "").replace(" ", "")
            # Build pattern: CS\s*-?\s*6250
            if len(normalized_code) >= 4:
                dept = normalized_code[:2]
                num = normalized_code[2:]
                pattern = rf"\b{dept}\s*-?\s*{num}\b"
                if re.search(pattern, upper):
                    return course

        # Also try matching on the course name
        if course.name.lower() in text.lower():
            return course

    return None


def extract_comment_text(comment_data: dict[str, Any], depth: int = 0) -> list[str]:
    """
    Recursively extract comment bodies from Reddit's nested comment structure.
    Only goes 2 levels deep and takes top comments by score.
    """
    if depth > 2:
        return []

    texts: list[str] = []
    kind = comment_data.get("kind")

    if kind == "t1":
        data = comment_data.get("data", {})
        body = data.get("body", "")
        if body and body != "[deleted]" and body != "[removed]":
            author = data.get("author", "[deleted]")
            score = data.get("score", 0)
            texts.append(f"[{author}, {score} pts] {body}")

        # Recurse into replies
        replies = data.get("replies")
        if isinstance(replies, dict):
            children = replies.get("data", {}).get("children", [])
            for child in children[:3]:  # Limit nested replies
                texts.extend(extract_comment_text(child, depth + 1))

    elif kind == "Listing":
        children = comment_data.get("data", {}).get("children", [])
        for child in children:
            texts.extend(extract_comment_text(child, depth))

    return texts


def parse_post_listing(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a subreddit listing or search result into post summaries."""
    posts: list[dict[str, Any]] = []
    children = response_json.get("data", {}).get("children", [])

    for child in children:
        if child.get("kind") != "t3":
            continue

        data = child["data"]
        if data.get("is_self") is False and not data.get("selftext"):
            # Link-only posts with no body text aren't useful for RAG
            continue
        if data.get("removed_by_category") or data.get("selftext") == "[removed]":
            continue

        posts.append({
            "id": data["id"],
            "title": data.get("title", ""),
            "selftext": data.get("selftext", ""),
            "author": data.get("author", "[deleted]"),
            "score": data.get("score", 0),
            "num_comments": data.get("num_comments", 0),
            "created_utc": data.get("created_utc", 0),
            "permalink": data.get("permalink", ""),
            "url": f"https://www.reddit.com{data.get('permalink', '')}",
        })

    return posts


def parse_post_with_comments(
    response_json: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """
    Parse a Reddit post page (which returns [post_listing, comments_listing]).
    Returns the post data and a flat list of comment texts.
    """
    post_listing = response_json[0]
    comments_listing = response_json[1]

    # Extract the post
    post_data = post_listing["data"]["children"][0]["data"]
    post = {
        "id": post_data["id"],
        "title": post_data.get("title", ""),
        "selftext": post_data.get("selftext", ""),
        "author": post_data.get("author", "[deleted]"),
        "score": post_data.get("score", 0),
        "num_comments": post_data.get("num_comments", 0),
        "created_utc": post_data.get("created_utc", 0),
        "permalink": post_data.get("permalink", ""),
        "url": f"https://www.reddit.com{post_data.get('permalink', '')}",
    }

    # Extract comments
    comment_texts: list[str] = []
    children = comments_listing.get("data", {}).get("children", [])
    for child in children[:MAX_COMMENTS_PER_POST]:
        comment_texts.extend(extract_comment_text(child))

    return post, comment_texts[:MAX_COMMENTS_PER_POST]


def build_document_content(post: dict[str, Any], comments: list[str]) -> str:
    """Assemble a post + comments into a single document body."""
    parts: list[str] = []

    title = normalize_text(post.get("title", ""))
    selftext = normalize_text(post.get("selftext", ""))

    if title:
        parts.append(f"Title: {title}")
    if selftext:
        parts.append(f"\n{selftext}")

    if comments:
        parts.append("\n--- Comments ---")
        for comment in comments:
            parts.append(normalize_text(comment))

    return "\n\n".join(parts)


def post_to_document(
    post: dict[str, Any],
    comments: list[str],
    matched_course: CourseCatalogEntry | None,
) -> RedditDocument:
    """Convert a Reddit post + comments into a RedditDocument."""
    post_id = post["id"]
    content = build_document_content(post, comments)
    created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)

    return RedditDocument(
        document_id=build_reddit_document_id(post_id),
        source_document_id=build_source_document_id(post_id),
        source="reddit",
        title=normalize_text(post.get("title", f"Reddit post {post_id}")),
        url=post["url"],
        author=post.get("author", "[deleted]"),
        score=post.get("score", 0),
        num_comments=post.get("num_comments", 0),
        published_at=created,
        course_id=matched_course.course_id if matched_course else None,
        course_slug=matched_course.slug if matched_course else None,
        course_name=matched_course.name if matched_course else None,
        course_codes=matched_course.codes if matched_course else [],
        content=content,
        content_hash=content_hash(content),
        subreddit=SUBREDDIT,
        metadata={
            "post_id": post_id,
            "permalink": post.get("permalink", ""),
            "comment_count_scraped": len(comments),
        },
    )


class RedditClient:
    """
    Async client for Reddit's public JSON API.

    No OAuth required — uses unauthenticated endpoints with a polite
    User-Agent and rate limiting.
    """

    def __init__(self, settings: "Settings") -> None:
        import httpx

        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url="https://www.reddit.com",
            timeout=settings.reddit_request_timeout_seconds,
            headers={
                "User-Agent": settings.reddit_user_agent,
            },
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch a JSON endpoint with rate-limit delay."""
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        url = path
        if params:
            url = f"{path}?{urlencode(params)}"
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()

    async def search_subreddit(
        self,
        query: str,
        *,
        sort: str = "relevance",
        time_filter: str = "all",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Search r/OMSCS for posts matching a query."""
        data = await self._get_json(
            f"/r/{SUBREDDIT}/search.json",
            {
                "q": query,
                "restrict_sr": "on",
                "sort": sort,
                "t": time_filter,
                "limit": min(limit, 100),
            },
        )
        return parse_post_listing(data)

    async def fetch_post_comments(self, post_id: str) -> tuple[dict[str, Any], list[str]]:
        """Fetch a post and its top comments."""
        data = await self._get_json(
            f"/r/{SUBREDDIT}/comments/{post_id}.json",
            {"sort": "top", "limit": MAX_COMMENTS_PER_POST},
        )
        return parse_post_with_comments(data)

    async def fetch_subreddit_posts(
        self,
        sort: str = "new",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Fetch posts from r/OMSCS by sort order (new, hot, top)."""
        data = await self._get_json(
            f"/r/{SUBREDDIT}/{sort}.json",
            {"limit": min(limit, 100)},
        )
        return parse_post_listing(data)

    async def scrape_course_discussions(
        self,
        catalog: list[CourseCatalogEntry],
        course_slugs: list[str] | None = None,
        posts_per_course: int = 10,
    ) -> list[RedditDocument]:
        """
        Search Reddit for discussions about specific courses.

        For each course, searches r/OMSCS by course code, fetches the full
        thread for top results, and builds documents linked to the course.
        """
        if course_slugs:
            courses = [c for c in catalog if c.slug in course_slugs]
        else:
            courses = catalog

        all_docs: list[RedditDocument] = []
        seen_ids: set[str] = set()

        for course in courses:
            # Search by each course code (e.g. "CS 6250")
            queries = list(course.codes) + [course.name]
            for query in queries[:2]:  # Limit queries per course
                try:
                    posts = await self.search_subreddit(
                        query,
                        limit=posts_per_course,
                    )
                except Exception as exc:
                    logger.error("Search failed for %r: %s", query, exc)
                    continue

                for post_summary in posts:
                    post_id = post_summary["id"]
                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)

                    try:
                        post, comments = await self.fetch_post_comments(post_id)
                    except Exception as exc:
                        logger.error("Failed to fetch post %s: %s", post_id, exc)
                        continue

                    # Try to match to a course (might match a different one
                    # than we searched for)
                    full_text = f"{post.get('title', '')} {post.get('selftext', '')}"
                    matched = match_course(full_text, catalog) or course
                    doc = post_to_document(post, comments, matched)
                    all_docs.append(doc)

        return all_docs

    async def scrape_recent_posts(
        self,
        catalog: list[CourseCatalogEntry],
        limit: int = 25,
    ) -> list[RedditDocument]:
        """
        Scrape the most recent r/OMSCS posts (not course-specific).
        Attempts to match each post to a course via text analysis.
        """
        posts = await self.fetch_subreddit_posts(sort="new", limit=limit)
        docs: list[RedditDocument] = []

        for post_summary in posts:
            try:
                post, comments = await self.fetch_post_comments(post_summary["id"])
            except Exception as exc:
                logger.error("Failed to fetch post %s: %s", post_summary["id"], exc)
                continue

            full_text = f"{post.get('title', '')} {post.get('selftext', '')}"
            matched = match_course(full_text, catalog)
            doc = post_to_document(post, comments, matched)
            docs.append(doc)

        return docs