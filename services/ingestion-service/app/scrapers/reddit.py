"""
Reddit scraper for r/OMSCS course discussions.

Uses Reddit's OAuth API when credentials are configured, otherwise falls back
to Reddit's public JSON API for local development.
Each post + its top comments becomes a single document, linked to a course
when the post title or body mentions a known course code.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
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
DEFAULT_SEARCH_MODES = ("relevance_all", "top_all", "top_year", "new_year")
SEARCH_MODE_PARAMS = {
    "relevance_all": {"sort": "relevance", "time_filter": "all"},
    "top_all": {"sort": "top", "time_filter": "all"},
    "top_year": {"sort": "top", "time_filter": "year"},
    "new_year": {"sort": "new", "time_filter": "year"},
    "comments_all": {"sort": "comments", "time_filter": "all"},
}

COURSE_ALIASES: dict[str, tuple[str, ...]] = {
    "advanced-operating-systems": ("AOS",),
    "ai-ethics-and-society": ("AI Ethics", "AIES"),
    "artificial-intelligence": ("AI",),
    "artificial-intelligence-for-robotics": ("AI4R", "RAIT"),
    "bayesian-statistics": ("Bayes",),
    "big-data-for-health-informatics": ("BD4H",),
    "computer-networks": ("CN",),
    "computer-vision": ("CV",),
    "database-system-concepts-and-design": ("DBSD", "DBS"),
    "deep-learning": ("DL",),
    "deterministic-optimization": ("DO",),
    "distributed-computing": ("DC",),
    "educational-technology": ("EdTech",),
    "graduate-introduction-to-operating-systems": ("GIOS",),
    "gpu-hardware-and-software": ("GPU",),
    "high-performance-computer-architecture": ("HPCA",),
    "high-performance-computing": ("HPC", "IHPC"),
    "human-computer-interaction": ("HCI",),
    "introduction-to-analytics-modeling": ("IAM", "IYSE 6501"),
    "introduction-to-cognitive-science": ("CogSci",),
    "introduction-to-graduate-algorithms": ("GA",),
    "introduction-to-information-security": ("IIS",),
    "knowledge-based-ai": ("KBAI",),
    "machine-learning": ("ML",),
    "machine-learning-for-trading": ("ML4T",),
    "natural-language-processing": ("NLP",),
    "network-science": ("NetSci",),
    "network-security": ("NetSec", "NS"),
    "reinforcement-learning": ("RL",),
    "simulation-and-modeling-for-engineering-and-science": ("Sim", "Simulation"),
    "software-analysis-and-test": ("SAT",),
    "software-architecture-and-design": ("SAD",),
    "software-development-process": ("SDP",),
    "system-design-for-cloud-computing": ("SDCC",),
}


def build_reddit_document_id(post_id: str) -> str:
    return f"reddit-post-{post_id}"


def build_source_document_id(post_id: str) -> str:
    return post_id


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_course_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def course_code_variants(code: str) -> list[str]:
    normalized = normalize_course_code(code)
    match = re.match(r"^([A-Z]{2,4})(\d{4}[A-Z]*)$", normalized)
    if not match:
        return [code]
    subject, number = match.groups()
    return [
        f"{subject} {number}",
        f"{subject}-{number}",
        f"{subject}{number}",
    ]


def course_aliases(course: CourseCatalogEntry) -> list[str]:
    aliases = list(COURSE_ALIASES.get(course.slug, ()))
    metadata_aliases = course.metadata.get("aliases") if course.metadata else None
    if isinstance(metadata_aliases, list):
        aliases.extend(str(alias) for alias in metadata_aliases if alias)
    return unique_preserving_order(aliases)


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(value)
    return unique


def build_course_search_queries(
    course: CourseCatalogEntry,
    include_aliases: bool = True,
) -> list[str]:
    queries: list[str] = []
    for code in course.codes:
        queries.extend(course_code_variants(code))
        for variant in course_code_variants(code):
            queries.append(f"{variant} OMSCS")

    queries.append(course.name)
    queries.append(f"{course.name} OMSCS")

    if include_aliases:
        for alias in course_aliases(course):
            queries.append(alias)
            queries.append(f"{alias} OMSCS")
            if course.codes:
                queries.append(f"{alias} {course.codes[0]}")

    return unique_preserving_order(queries)


def search_modes_to_params(
    search_modes: list[str] | tuple[str, ...] | None,
) -> list[dict[str, str]]:
    modes = search_modes or DEFAULT_SEARCH_MODES
    params: list[dict[str, str]] = []
    for mode in modes:
        if mode not in SEARCH_MODE_PARAMS:
            logger.warning("Unknown Reddit search mode %r; skipping", mode)
            continue
        params.append(SEARCH_MODE_PARAMS[mode])
    return params or [SEARCH_MODE_PARAMS["relevance_all"]]


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

        for alias in course_aliases(course):
            if len(alias) < 2:
                continue
            if re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE):
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
    """Async client for Reddit API endpoints with optional OAuth."""

    def __init__(self, settings: "Settings") -> None:
        import httpx

        self._settings = settings
        self._access_token: str | None = None
        self._access_token_expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._has_oauth_credentials = bool(
            settings.reddit_client_id and settings.reddit_client_secret
        )
        self._client = httpx.AsyncClient(
            base_url=(
                "https://oauth.reddit.com"
                if self._has_oauth_credentials
                else "https://www.reddit.com"
            ),
            timeout=settings.reddit_request_timeout_seconds,
            headers={
                "User-Agent": settings.reddit_user_agent,
            },
            follow_redirects=True,
        )
        self._auth_client = httpx.AsyncClient(
            base_url="https://www.reddit.com",
            timeout=settings.reddit_request_timeout_seconds,
            headers={"User-Agent": settings.reddit_user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._auth_client.aclose()

    async def _ensure_access_token(self) -> None:
        if not self._has_oauth_credentials:
            return

        now = datetime.now(timezone.utc)
        if self._access_token and now < self._access_token_expires_at:
            return

        response = await self._auth_client.post(
            "/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(
                self._settings.reddit_client_id,
                self._settings.reddit_client_secret,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._access_token_expires_at = now + timedelta(
            seconds=max(expires_in - 60, 60),
        )
        self._client.headers["Authorization"] = f"Bearer {self._access_token}"

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch a JSON endpoint with rate-limit delay."""
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        await self._ensure_access_token()
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
        include_aliases: bool = True,
        search_modes: list[str] | None = None,
        max_search_results_per_query: int = 25,
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
        search_params = search_modes_to_params(search_modes)

        for course in courses:
            queries = build_course_search_queries(course, include_aliases=include_aliases)
            candidates: dict[str, dict[str, Any]] = {}

            for query in queries:
                for params in search_params:
                    try:
                        posts = await self.search_subreddit(
                            query,
                            sort=params["sort"],
                            time_filter=params["time_filter"],
                            limit=max_search_results_per_query,
                        )
                    except Exception as exc:
                        logger.error(
                            "Search failed for %r (%s/%s): %s",
                            query,
                            params["sort"],
                            params["time_filter"],
                            exc,
                        )
                        continue

                    for post_summary in posts:
                        post_id = post_summary["id"]
                        candidate = candidates.setdefault(post_id, dict(post_summary))
                        matched_queries = candidate.setdefault("matched_queries", [])
                        matched_queries.append(query)

                    if len(candidates) >= posts_per_course * 4:
                        break
                if len(candidates) >= posts_per_course * 4:
                    break

            ranked_candidates = sorted(
                candidates.values(),
                key=lambda post: (
                    len(post.get("matched_queries", [])),
                    post.get("score", 0),
                    post.get("num_comments", 0),
                ),
                reverse=True,
            )

            course_docs_created = 0
            for post_summary in ranked_candidates:
                if course_docs_created >= posts_per_course:
                    break

                post_id = post_summary["id"]
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                try:
                    post, comments = await self.fetch_post_comments(post_id)
                except Exception as exc:
                    logger.error("Failed to fetch post %s: %s", post_id, exc)
                    continue

                full_text = f"{post.get('title', '')} {post.get('selftext', '')}"
                matched = match_course(full_text, catalog) or course
                doc = post_to_document(post, comments, matched)
                doc.metadata.update(
                    {
                        "search_course_slug": course.slug,
                        "matched_queries": post_summary.get("matched_queries", []),
                    }
                )
                all_docs.append(doc)
                course_docs_created += 1

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
