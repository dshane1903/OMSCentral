#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


COURSE_CODE_PATTERN = re.compile(r"\b(?:CS|CSE)[-\s]?\d{4}[A-Z0-9-]*\b", re.IGNORECASE)
PRIORITY_COURSES = {
    "introduction-to-graduate-algorithms": ("GA", ("ga", "graduate algorithms", "cs 6515", "cs6515", "cs-6515")),
    "machine-learning": ("ML", ("ml", "machine learning", "cs 7641", "cs7641", "cs-7641")),
    "artificial-intelligence": ("AI", ("ai", "artificial intelligence", "cs 6601", "cs6601", "cs-6601")),
    "computer-networks": ("CN", ("cn", "computer networks", "cs 6250", "cs6250", "cs-6250")),
    "software-development-process": ("SDP", ("sdp", "software development process", "cs 6300", "cs6300", "cs-6300")),
    "graduate-introduction-to-operating-systems": ("GIOS", ("gios", "intro to os", "operating systems", "cs 6200", "cs6200", "cs-6200")),
    "advanced-operating-systems": ("AOS", ("aos", "advanced operating systems", "cs 6210", "cs6210", "cs-6210")),
    "database-systems-concepts-and-design": ("DBS", ("dbs", "database systems", "cs 6400", "cs6400", "cs-6400")),
    "human-computer-interaction": ("HCI", ("hci", "human computer interaction", "human-computer interaction", "cs 6750", "cs6750", "cs-6750")),
    "machine-learning-for-trading": ("ML4T", ("ml4t", "machine learning for trading", "cs 7646", "cs7646", "cs-7646")),
    "deep-learning": ("DL", ("dl", "deep learning", "cs 7643", "cs7643", "cs-7643")),
    "reinforcement-learning-and-decision-making": ("RL", ("rl", "reinforcement learning", "cs 7642", "cs7642", "cs-7642")),
    "natural-language-processing": ("NLP", ("nlp", "natural language processing", "cs 7650", "cs7650", "cs-7650")),
}
PRIORITY_LABELS = {
    "ga",
    "ml",
    "ai",
    "cn",
    "sdp",
    "gios",
    "aos",
    "dbs",
    "hci",
    "ml4t",
    "dl",
    "rl",
    "nlp",
}

GENERIC_PATTERNS = (
    "course & specs megathread",
    "course & specialization megathread",
    "course specialization megathread",
    "selection choices registration",
    "how hard is it to get into classes",
    "specialization courses",
    "finally got out",
    "looking back past the finish line",
    "oms cs journey",
    "omscs journey",
    "grad review",
    "planning order of courses",
    "course plan",
    "classes i want to take",
    "can use some advice",
)

LOW_VALUE_PATTERNS = (
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
    "reddit the heart of the internet",
    "please give some advice",
    "what do you think of my courses",
)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def normalized_course_codes(aliases: tuple[str, ...]) -> set[str]:
    codes = set()
    for alias in aliases:
        for match in COURSE_CODE_PATTERN.findall(alias):
            codes.add(re.sub(r"[^A-Z0-9]", "", match.upper()))
    return codes


def classify(row: dict[str, str], aliases: tuple[str, ...]) -> tuple[str, str]:
    title = normalize(row["title"])
    url = row["url"].lower()
    haystack = f"{title} {url}"

    reasons: list[str] = []
    if any(pattern in title for pattern in GENERIC_PATTERNS):
        reasons.append("generic_thread")
    if any(pattern in title for pattern in LOW_VALUE_PATTERNS):
        reasons.append("low_value_title")

    alias_hits = [alias for alias in aliases if normalize(alias) and normalize(alias) in haystack]
    if not alias_hits:
        reasons.append("missing_course_signal")

    expected_codes = normalized_course_codes(aliases)
    title_codes = {
        re.sub(r"[^A-Z0-9]", "", match.upper())
        for match in COURSE_CODE_PATTERN.findall(row["title"])
    }
    has_expected_code = bool(title_codes.intersection(expected_codes))
    if title_codes and expected_codes and not has_expected_code:
        reasons.append("other_course_code")

    expected_aliases = {normalize(alias) for alias in aliases}
    other_labels = PRIORITY_LABELS - expected_aliases
    title_tokens = set(title.split())
    if (
        other_labels.intersection(title_tokens)
        and not expected_aliases.intersection(title_tokens)
        and not has_expected_code
    ):
        reasons.append("other_course_alias")

    if row["chunk_count"] <= 0:
        reasons.append("not_chunked")

    if "other_course_code" in reasons or "other_course_alias" in reasons:
        return "delete", ";".join(reasons)
    if "generic_thread" in reasons:
        return "delete", ";".join(reasons)
    if "low_value_title" in reasons:
        return "delete", ";".join(reasons)
    if "not_chunked" in reasons:
        return "review", ";".join(reasons)
    if reasons:
        return "review", ";".join(reasons)
    return "keep", ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Reddit documents by course.")
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", "https://d38rezjkrvukjs.cloudfront.net"),
    )
    parser.add_argument("--out", type=Path, default=Path("reddit-source-audit.csv"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, object]] = []
    summary = Counter()
    by_course = defaultdict(Counter)

    for slug, (label, aliases) in PRIORITY_COURSES.items():
        payload = get_json(f"{args.api_base_url.rstrip('/')}/courses/{slug}/documents")
        for doc in payload.get("documents", []):
            if doc.get("source") != "reddit":
                continue
            action, reasons = classify(doc, aliases)
            row = {
                "action": action,
                "reasons": reasons,
                "course_slug": slug,
                "course_label": label,
                "document_id": doc["document_id"],
                "title": doc["title"],
                "url": doc["url"],
                "chunk_count": doc["chunk_count"],
                "metadata": json.dumps(doc.get("metadata") or {}, sort_keys=True),
            }
            rows.append(row)
            summary[action] += 1
            by_course[label][action] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "action",
                "reasons",
                "course_slug",
                "course_label",
                "document_id",
                "title",
                "url",
                "chunk_count",
                "metadata",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} Reddit source audit rows to {args.out}")
    print("Overall:", dict(summary))
    for label in sorted(by_course):
        print(label, dict(by_course[label]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
