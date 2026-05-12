#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Seed OMSCS production data through the protected API gateway.

Required environment:
  API_BASE_URL       Public API gateway URL, for example https://api.example.com
  ADMIN_API_KEY     Admin token accepted by the API gateway

Optional environment:
  SEED_TARGET             all | courses | reddit (default: all)
  AUTH_HEADER_NAME        Authorization | X-Admin-Token (default: Authorization)
  COURSE_LIMIT            OMSCentral course limit (default: 132)
  REDDIT_LIMIT            Reddit course limit (default: 132)
  REDDIT_POSTS_PER_COURSE Reddit posts per course (default: 50)
  WAIT_FOR_COMPLETION     true | false (default: true)
  POLL_INTERVAL_SECONDS   Seconds between job polls (default: 30)

Example:
  API_BASE_URL=https://api.example.com ADMIN_API_KEY=... \
    scripts/seed-production-data.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

: "${API_BASE_URL:?Set API_BASE_URL to the deployed API gateway URL.}"
: "${ADMIN_API_KEY:?Set ADMIN_API_KEY to the deployed admin token.}"

SEED_TARGET="${SEED_TARGET:-all}"
AUTH_HEADER_NAME="${AUTH_HEADER_NAME:-Authorization}"
COURSE_LIMIT="${COURSE_LIMIT:-132}"
REDDIT_LIMIT="${REDDIT_LIMIT:-132}"
REDDIT_POSTS_PER_COURSE="${REDDIT_POSTS_PER_COURSE:-50}"
WAIT_FOR_COMPLETION="${WAIT_FOR_COMPLETION:-true}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-30}"

API_BASE_URL="${API_BASE_URL%/}"

case "$AUTH_HEADER_NAME" in
  Authorization)
    AUTH_HEADER_VALUE="Bearer ${ADMIN_API_KEY}"
    ;;
  X-Admin-Token)
    AUTH_HEADER_VALUE="${ADMIN_API_KEY}"
    ;;
  *)
    echo "Unsupported AUTH_HEADER_NAME: ${AUTH_HEADER_NAME}" >&2
    exit 2
    ;;
esac

extract_json_field() {
  local field="$1"
  python3 -c 'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$field"
}

post_job() {
  local path="$1"
  local payload="$2"

  curl -fsS -X POST "${API_BASE_URL}${path}" \
    -H "${AUTH_HEADER_NAME}: ${AUTH_HEADER_VALUE}" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

get_job() {
  local job_id="$1"

  curl -fsS "${API_BASE_URL}/index/jobs/${job_id}" \
    -H "${AUTH_HEADER_NAME}: ${AUTH_HEADER_VALUE}"
}

poll_job() {
  local label="$1"
  local job_id="$2"
  local response
  local status
  local indexed
  local total
  local persisted
  local chunks

  if [[ "$WAIT_FOR_COMPLETION" != "true" ]]; then
    echo "${label} job queued: ${job_id}"
    return 0
  fi

  while true; do
    response="$(get_job "$job_id")"
    status="$(printf '%s' "$response" | extract_json_field status)"
    indexed="$(printf '%s' "$response" | extract_json_field courses_indexed)"
    total="$(printf '%s' "$response" | extract_json_field total_courses)"
    persisted="$(printf '%s' "$response" | extract_json_field documents_persisted)"
    chunks="$(printf '%s' "$response" | extract_json_field processing_chunks_created)"

    echo "${label}: status=${status} courses=${indexed}/${total} documents=${persisted} chunks=${chunks}"

    case "$status" in
      completed)
        return 0
        ;;
      completed_with_errors)
        echo "${label} completed with errors. Full job response:" >&2
        printf '%s\n' "$response" >&2
        return 1
        ;;
      failed)
        echo "${label} failed. Full job response:" >&2
        printf '%s\n' "$response" >&2
        return 1
        ;;
    esac

    sleep "$POLL_INTERVAL_SECONDS"
  done
}

seed_courses() {
  local payload
  local response
  local job_id

  payload="$(printf '{"course_slugs":[],"missing_only":true,"include_reviews":true,"process_after":true,"limit":%s}' "$COURSE_LIMIT")"
  response="$(post_job "/index/courses" "$payload")"
  job_id="$(printf '%s' "$response" | extract_json_field job_id)"

  if [[ -z "$job_id" ]]; then
    echo "Could not read OMSCentral job_id from response: ${response}" >&2
    exit 1
  fi

  poll_job "OMSCentral seed" "$job_id"
}

seed_reddit() {
  local payload
  local response
  local job_id

  payload="$(printf '{"course_slugs":[],"missing_only":false,"posts_per_course":%s,"include_aliases":true,"search_modes":["relevance_all","top_all","top_year","new_year"],"max_search_results_per_query":25,"process_after":true,"limit":%s}' "$REDDIT_POSTS_PER_COURSE" "$REDDIT_LIMIT")"
  response="$(post_job "/index/reddit" "$payload")"
  job_id="$(printf '%s' "$response" | extract_json_field job_id)"

  if [[ -z "$job_id" ]]; then
    echo "Could not read Reddit job_id from response: ${response}" >&2
    exit 1
  fi

  poll_job "Reddit seed" "$job_id"
}

case "$SEED_TARGET" in
  all)
    seed_courses
    seed_reddit
    ;;
  courses)
    seed_courses
    ;;
  reddit)
    seed_reddit
    ;;
  *)
    echo "Unsupported SEED_TARGET: ${SEED_TARGET}" >&2
    usage >&2
    exit 2
    ;;
esac

echo "Seed run finished."
