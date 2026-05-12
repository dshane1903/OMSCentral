#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def post_json(api_base_url: str, token: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{api_base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete audit rows marked action=delete.")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("API_BASE_URL", "http://omscentral-api-alb-185117391.us-east-1.elb.amazonaws.com"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("ADMIN_API_KEY", "").strip()
    if not token and not args.dry_run:
        raise SystemExit("Set ADMIN_API_KEY before pruning.")

    with args.audit.open(newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(handle)
            if row.get("action", "").strip().lower() == "delete"
        ]

    document_ids = [row["document_id"] for row in rows]
    print(f"Selected {len(document_ids)} documents for deletion.", file=sys.stderr)
    if args.dry_run:
        for row in rows:
            print(f"{row['course_label']} | {row['document_id']} | {row['title']}")
        return 0

    deleted = 0
    for start in range(0, len(document_ids), args.batch_size):
        batch = document_ids[start : start + args.batch_size]
        response = post_json(
            args.api_base_url,
            token,
            "/documents/delete",
            {"document_ids": batch, "source": "reddit"},
        )
        deleted += int(response.get("deleted_count", 0))
        print(
            f"Deleted batch {start // args.batch_size + 1}: "
            f"{response.get('deleted_count', 0)}/{len(batch)}",
            file=sys.stderr,
        )

    print(f"Prune finished: deleted={deleted}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
