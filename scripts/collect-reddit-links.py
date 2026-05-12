#!/usr/bin/env python3
"""
Collect candidate r/OMSCS Reddit thread links for prioritized courses.

This does link discovery only. It writes a CSV for human review before anything
is imported into the evidence database.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


POST_RE = re.compile(
    r"https?://(?:(?:www|old|new|sh)\.)?reddit\.com/r/OMSCS/comments/([a-z0-9]+)(?:/[^/?#\s]*)?/?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CourseTarget:
    slug: str
    label: str
    codes: tuple[str, ...]
    names: tuple[str, ...]
    aliases: tuple[str, ...] = ()


COURSES: tuple[CourseTarget, ...] = (
    CourseTarget(
        slug="introduction-to-graduate-algorithms",
        label="GA",
        codes=("CS 6515", "CS-6515", "CS6515"),
        names=("Graduate Algorithms",),
        aliases=("GA",),
    ),
    CourseTarget(
        slug="machine-learning",
        label="ML",
        codes=("CS 7641", "CS-7641", "CS7641"),
        names=("Machine Learning",),
        aliases=("ML",),
    ),
    CourseTarget(
        slug="artificial-intelligence",
        label="AI",
        codes=("CS 6601", "CS-6601", "CS6601"),
        names=("Artificial Intelligence",),
        aliases=("AI",),
    ),
    CourseTarget(
        slug="computer-networks",
        label="CN",
        codes=("CS 6250", "CS-6250", "CS6250"),
        names=("Computer Networks",),
        aliases=("CN",),
    ),
    CourseTarget(
        slug="software-development-process",
        label="SDP",
        codes=("CS 6300", "CS-6300", "CS6300"),
        names=("Software Development Process",),
        aliases=("SDP",),
    ),
    CourseTarget(
        slug="graduate-introduction-to-operating-systems",
        label="GIOS",
        codes=("CS 6200", "CS-6200", "CS6200"),
        names=("Graduate Introduction to Operating Systems",),
        aliases=("GIOS", "IOS"),
    ),
    CourseTarget(
        slug="advanced-operating-systems",
        label="AOS",
        codes=("CS 6210", "CS-6210", "CS6210"),
        names=("Advanced Operating Systems",),
        aliases=("AOS",),
    ),
    CourseTarget(
        slug="database-systems-concepts-and-design",
        label="DBS",
        codes=("CS 6400", "CS-6400", "CS6400"),
        names=("Database Systems Concepts and Design",),
        aliases=("DBS", "Database Systems"),
    ),
    CourseTarget(
        slug="human-computer-interaction",
        label="HCI",
        codes=("CS 6750", "CS-6750", "CS6750"),
        names=("Human Computer Interaction", "Human-Computer Interaction"),
        aliases=("HCI",),
    ),
    CourseTarget(
        slug="machine-learning-for-trading",
        label="ML4T",
        codes=("CS 7646", "CS-7646", "CS7646"),
        names=("Machine Learning for Trading",),
        aliases=("ML4T",),
    ),
    CourseTarget(
        slug="deep-learning",
        label="DL",
        codes=("CS 7643", "CS-7643", "CS7643"),
        names=("Deep Learning",),
        aliases=("DL",),
    ),
    CourseTarget(
        slug="reinforcement-learning-and-decision-making",
        label="RL",
        codes=("CS 7642", "CS-7642", "CS7642"),
        names=("Reinforcement Learning and Decision Making", "Reinforcement Learning"),
        aliases=("RL",),
    ),
    CourseTarget(
        slug="natural-language-processing",
        label="NLP",
        codes=("CS 7650", "CS-7650", "CS7650"),
        names=("Natural Language Processing",),
        aliases=("NLP",),
    ),
)


INTENTS = (
    "workload",
    "difficulty",
    "review",
    "advice",
    "prep",
    "exam",
    "project",
    "summer",
    "fall",
    "first course",
)


def normalize_reddit_url(url: str) -> tuple[str, str] | None:
    url = urllib.parse.unquote(url)
    match = POST_RE.search(url)
    if not match:
        return None
    post_id = match.group(1).lower()
    start, end = match.span()
    clean = url[start:end].rstrip("/")
    clean = re.sub(
        r"https?://(?:(?:www|old|new|sh)\.)?reddit\.com",
        "https://www.reddit.com",
        clean,
        count=1,
        flags=re.IGNORECASE,
    )
    return post_id, clean + "/"


def selected_courses(values: list[str]) -> list[CourseTarget]:
    if not values:
        return list(COURSES)

    lookup = {
        key.lower(): course
        for course in COURSES
        for key in (course.slug, course.label, *course.codes, *course.names, *course.aliases)
    }
    courses: list[CourseTarget] = []
    for value in values:
        course = lookup.get(value.lower())
        if course is None:
            valid = ", ".join(course.label for course in COURSES)
            raise SystemExit(f"Unknown course {value!r}. Valid labels: {valid}")
        if course not in courses:
            courses.append(course)
    return courses


def query_terms(course: CourseTarget) -> list[str]:
    terms = [*course.codes[:1], *course.names[:1], *course.aliases[:1]]
    if course.label not in terms:
        terms.append(course.label)
    return [term for term in terms if len(term) > 1]


def build_queries(course: CourseTarget, max_queries: int) -> list[str]:
    queries: list[str] = []
    for term in query_terms(course):
        quoted = f'"{term}"' if " " in term or "-" in term else term
        queries.append(f"site:reddit.com/r/OMSCS/comments {quoted}")
        queries.append(f"site:reddit.com/r/OMSCS {quoted}")
        queries.append(f"reddit OMSCS {quoted}")
        for intent in INTENTS:
            queries.append(f"site:reddit.com/r/OMSCS/comments {quoted} {intent}")
            queries.append(f"site:reddit.com/r/OMSCS {quoted} {intent}")
            queries.append(f"reddit OMSCS {quoted} {intent}")
    return queries[:max_queries]


def brave_search(query: str, api_key: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "count": str(min(count, 20)),
            "search_lang": "en",
            "country": "US",
            "safesearch": "moderate",
        }
    )
    request = urllib.request.Request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "omscentral-link-discovery/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Brave Search returned {exc.code}: {body}") from exc

    results = payload.get("web", {}).get("results", [])
    return [
        {
            "title": str(result.get("title", "")),
            "url": str(result.get("url", "")),
            "description": str(result.get("description", "")),
        }
        for result in results
    ]


def collect_links(
    courses: Iterable[CourseTarget],
    provider: str,
    per_course: int,
    max_queries: int,
    results_per_query: int,
    delay_seconds: float,
    raw_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if provider != "brave":
        raise SystemExit("Only --provider brave is currently implemented.")

    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set BRAVE_SEARCH_API_KEY before running this script.")

    rows: list[dict[str, str]] = []
    seen_ids_by_course: dict[str, set[str]] = {}

    for course in courses:
        seen_ids_by_course.setdefault(course.slug, set())
        print(f"Collecting {course.label} ({course.slug})...", file=sys.stderr)

        for query in build_queries(course, max_queries):
            if len(seen_ids_by_course[course.slug]) >= per_course:
                break

            print(f"  search: {query}", file=sys.stderr)
            for result in brave_search(query, api_key, results_per_query):
                if raw_rows is not None:
                    raw_rows.append(
                        {
                            "course_slug": course.slug,
                            "course_label": course.label,
                            "query": query,
                            "title": result["title"],
                            "url": result["url"],
                            "description": result["description"],
                        }
                    )
                normalized = normalize_reddit_url(result["url"])
                if normalized is None:
                    continue
                post_id, url = normalized
                if post_id in seen_ids_by_course[course.slug]:
                    continue

                seen_ids_by_course[course.slug].add(post_id)
                rows.append(
                    {
                        "approved": "",
                        "course_slug": course.slug,
                        "course_label": course.label,
                        "post_id": post_id,
                        "title": result["title"],
                        "url": url,
                        "query": query,
                        "description": result["description"],
                    }
                )
                if len(seen_ids_by_course[course.slug]) >= per_course:
                    break

            time.sleep(delay_seconds)

    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "approved",
        "course_slug",
        "course_label",
        "post_id",
        "title",
        "url",
        "query",
        "description",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_raw_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "course_slug",
        "course_label",
        "query",
        "title",
        "url",
        "description",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect candidate r/OMSCS Reddit thread links for review."
    )
    parser.add_argument(
        "--provider",
        default="brave",
        choices=("brave",),
        help="Search API provider.",
    )
    parser.add_argument(
        "--course",
        action="append",
        default=[],
        help="Course slug/label/code. Repeat to select multiple. Defaults to all priority courses.",
    )
    parser.add_argument(
        "--per-course",
        type=int,
        default=100,
        help="Maximum candidate links to keep per course.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=24,
        help="Maximum search queries per course.",
    )
    parser.add_argument(
        "--results-per-query",
        type=int,
        default=10,
        help="Search results requested per query.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Delay between search API calls.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reddit-candidates.csv"),
        help="CSV output path.",
    )
    parser.add_argument(
        "--raw-out",
        type=Path,
        default=None,
        help="Optional CSV of raw search results for debugging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    courses = selected_courses(args.course)
    raw_rows: list[dict[str, str]] | None = [] if args.raw_out else None
    rows = collect_links(
        courses=courses,
        provider=args.provider,
        per_course=args.per_course,
        max_queries=args.max_queries,
        results_per_query=args.results_per_query,
        delay_seconds=args.delay_seconds,
        raw_rows=raw_rows,
    )
    write_csv(args.out, rows)
    if args.raw_out and raw_rows is not None:
        write_raw_csv(args.raw_out, raw_rows)
        print(f"Wrote {len(raw_rows)} raw search results to {args.raw_out}", file=sys.stderr)
    print(f"Wrote {len(rows)} candidate links to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
