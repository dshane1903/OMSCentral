#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Collect candidate Reddit links for all priority OMSCS courses and import them.

Required environment:
  BRAVE_SEARCH_API_KEY  Brave Search API key

Optional environment:
  API_BASE_URL          API gateway URL
  ADMIN_API_KEY         Admin token. If omitted, fetched from AWS Secrets Manager.
  PER_COURSE            Candidate links per course (default: 100)
  MAX_QUERIES           Search queries per course (default: 24)
  RESULTS_PER_QUERY     Search results per query (default: 10)
  SEARCH_DELAY_SECONDS  Delay between search API calls (default: 1)
  IMPORT_DELAY_SECONDS  Delay between imports (default: 0.1)
  OUT                   Candidate CSV path (default: reddit-candidates-priority.csv)
  RAW_OUT               Raw search CSV path (default: reddit-raw-priority.csv)

Example:
  export BRAVE_SEARCH_API_KEY=...
  scripts/collect-and-import-priority-reddit.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

: "${BRAVE_SEARCH_API_KEY:?Set BRAVE_SEARCH_API_KEY before running this script.}"

API_BASE_URL="${API_BASE_URL:-http://omscentral-api-alb-185117391.us-east-1.elb.amazonaws.com}"
PER_COURSE="${PER_COURSE:-100}"
MAX_QUERIES="${MAX_QUERIES:-24}"
RESULTS_PER_QUERY="${RESULTS_PER_QUERY:-10}"
SEARCH_DELAY_SECONDS="${SEARCH_DELAY_SECONDS:-1}"
IMPORT_DELAY_SECONDS="${IMPORT_DELAY_SECONDS:-0.1}"
OUT="${OUT:-reddit-candidates-priority.csv}"
RAW_OUT="${RAW_OUT:-reddit-raw-priority.csv}"

if [[ -z "${ADMIN_API_KEY:-}" ]]; then
  ADMIN_API_KEY="$(aws secretsmanager get-secret-value \
    --region us-east-1 \
    --secret-id omscentral/prod/admin-api-key \
    --query SecretString \
    --output text)"
  export ADMIN_API_KEY
fi

export API_BASE_URL

echo "Collecting candidate Reddit links into ${OUT}..."
scripts/collect-reddit-links.py \
  --per-course "$PER_COURSE" \
  --max-queries "$MAX_QUERIES" \
  --results-per-query "$RESULTS_PER_QUERY" \
  --delay-seconds "$SEARCH_DELAY_SECONDS" \
  --out "$OUT" \
  --raw-out "$RAW_OUT"

echo
echo "Candidate counts by course:"
python3 - "$OUT" <<'PY'
import csv
import sys
from collections import Counter

rows = list(csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")))
counts = Counter(row["course_label"] for row in rows)
for label, count in sorted(counts.items()):
    print(f"{label}: {count}")
print(f"total: {len(rows)}")
PY

echo
echo "Importing all candidate rows as first-pass Reddit link evidence..."
scripts/import-approved-reddit-links.py \
  --csv "$OUT" \
  --import-all \
  --no-process-after \
  --delay-seconds "$IMPORT_DELAY_SECONDS"

echo
echo "Triggering one processing pass for imported Reddit evidence..."
curl -fsS -X POST "${API_BASE_URL%/}/process" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"limit":500,"max_batches":20,"reprocess":false}' \
  | python3 -m json.tool

echo "Priority Reddit link discovery/import finished."
