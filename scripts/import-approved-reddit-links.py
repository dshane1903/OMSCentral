#!/usr/bin/env python3
"""
Import approved Reddit link-discovery rows as curated Reddit evidence.

This importer does not crawl Reddit. It imports the reviewed link, title, and
search-result snippet as first-pass evidence, with metadata so richer summaries
can replace or augment it later.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


APPROVED_VALUES = {"1", "true", "yes", "y", "approved", "x"}
COURSE_CODE_PATTERN = re.compile(r"\b(?:CS|CSE)[-\s]?\d{4}[A-Z0-9-]*\b", re.IGNORECASE)
COURSE_CODES = {
    "introduction-to-graduate-algorithms": {"CS6515"},
    "machine-learning": {"CS7641"},
    "artificial-intelligence": {"CS6601"},
    "computer-networks": {"CS6250"},
    "software-development-process": {"CS6300"},
    "graduate-introduction-to-operating-systems": {"CS6200"},
    "advanced-operating-systems": {"CS6210"},
    "database-systems-concepts-and-design": {"CS6400"},
    "human-computer-interaction": {"CS6750"},
    "machine-learning-for-trading": {"CS7646"},
    "deep-learning": {"CS7643"},
    "reinforcement-learning-and-decision-making": {"CS7642"},
    "reinforcement-learning": {"CS7642"},
    "natural-language-processing": {"CS7650"},
}
NOISY_TITLE_PATTERNS = (
    "course & specs megathread",
    "course & specialization megathread",
    "course specialization megathread",
    "selection choices registration",
    "how hard is it to get into classes",
    "specialization courses",
    "finally got out",
    "looking back past the finish line",
    "omscs journey",
    "grad review",
    "planning order of courses",
    "course plan",
    "classes i want to take",
    "can use some advice",
    "videos link",
    "video course overview",
    "does anyone have course videos",
    "where can i watch course videos",
    "conference",
    "survey",
    "cios survey",
    "available online",
    "section do i choose",
    "grades release",
    "opened for the summer semester",
    "reddit - the heart of the internet",
    "please give some advice",
    "what do you think of my courses",
)


def is_approved(value: str) -> bool:
    return value.strip().lower() in APPROVED_VALUES


def is_noisy(row: dict[str, str]) -> bool:
    title = html.unescape(row.get("title", "")).strip().lower()
    if any(pattern in title for pattern in NOISY_TITLE_PATTERNS):
        return True

    expected_codes = COURSE_CODES.get(row.get("course_slug", ""), set())
    title_codes = {
        re.sub(r"[^A-Z0-9]", "", match.upper())
        for match in COURSE_CODE_PATTERN.findall(title)
    }
    if title_codes and expected_codes and not title_codes.intersection(expected_codes):
        return True

    return False


def build_content(row: dict[str, str]) -> str:
    title = html.unescape(row.get("title", "")).strip()
    description = html.unescape(row.get("description", "")).strip()
    query = html.unescape(row.get("query", "")).strip()
    label = row.get("course_label", "").strip()

    parts = [
        f"Approved Reddit thread candidate for {label or row['course_slug']}.",
        f"Thread title: {title}.",
    ]
    if description:
        parts.append(f"Search result snippet: {description}")
    if query:
        parts.append(f"Discovery query: {query}.")
    parts.append(
        "This is first-pass curated link evidence from search discovery; "
        "the Reddit permalink should be used for full thread context."
    )
    return "\n\n".join(parts)


def post_json(api_base_url: str, token: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base_url.rstrip('/')}/sources/reddit/manual",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import approved Reddit candidate CSV rows."
    )
    parser.add_argument("--csv", type=Path, required=True, help="Candidate CSV path.")
    parser.add_argument(
        "--import-all",
        action="store_true",
        help="Import every row, ignoring the approved column.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum rows to import after filtering.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rows that would import without calling the API.",
    )
    parser.add_argument(
        "--include-noisy",
        action="store_true",
        help="Import generic/low-value thread titles instead of skipping them.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.5,
        help="Delay between imports.",
    )
    parser.add_argument(
        "--process-after",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Trigger processing after each imported document.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_base_url = os.environ.get("API_BASE_URL", "").strip()
    admin_api_key = os.environ.get("ADMIN_API_KEY", "").strip()

    if not api_base_url and not args.dry_run:
        raise SystemExit("Set API_BASE_URL before running this importer.")
    if not admin_api_key and not args.dry_run:
        raise SystemExit("Set ADMIN_API_KEY before running this importer.")

    with args.csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    selected = []
    skipped_noisy = 0
    for row in rows:
        if not (args.import_all or is_approved(row.get("approved", ""))):
            continue
        if is_noisy(row) and not args.include_noisy:
            skipped_noisy += 1
            continue
        selected.append(row)
    if args.limit is not None:
        selected = selected[: args.limit]

    if skipped_noisy:
        print(f"Skipped {skipped_noisy} noisy candidate rows.", file=sys.stderr)

    if not selected:
        print("No rows selected for import.", file=sys.stderr)
        return 0

    imported = 0
    failed = 0
    for index, row in enumerate(selected, start=1):
        payload = {
            "course_slug": row["course_slug"].strip(),
            "title": html.unescape(row.get("title", "")).strip(),
            "url": row["url"].strip(),
            "content": build_content(row),
            "author": "unknown",
            "subreddit": "OMSCS",
            "process_after": args.process_after,
            "metadata": {
                "ingestion_mode": "search_link_batch",
                "discovery_query": row.get("query", ""),
                "post_id": row.get("post_id", ""),
                "course_label": row.get("course_label", ""),
            },
        }
        print(
            f"[{index}/{len(selected)}] {payload['course_slug']} | {payload['title']}",
            file=sys.stderr,
        )
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            continue

        try:
            response = post_json(api_base_url, admin_api_key, payload)
            imported += 1
            print(
                f"  imported {response.get('document_id')} "
                f"chunks={response.get('processing_chunks_created')}",
                file=sys.stderr,
            )
        except Exception as exc:
            failed += 1
            print(f"  failed: {exc}", file=sys.stderr)

        time.sleep(args.delay_seconds)

    print(f"Import finished: imported={imported} failed={failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
