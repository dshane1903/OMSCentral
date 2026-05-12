#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Add one curated Reddit source to the production evidence database.

Required environment:
  API_BASE_URL    Public API gateway URL, for example http://...elb.amazonaws.com
  ADMIN_API_KEY  Admin token accepted by the API gateway

Optional environment:
  AUTH_HEADER_NAME  Authorization | X-Admin-Token (default: Authorization)
  PROCESS_AFTER     true | false (default: true)

Example:
  API_BASE_URL=http://omscentral-api-alb-...amazonaws.com \
  ADMIN_API_KEY=... \
    scripts/add-manual-reddit-source.sh

The script prompts for course slug, Reddit permalink, title, author/subreddit,
then reads the excerpt from stdin. Finish the excerpt with Ctrl-D.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

: "${API_BASE_URL:?Set API_BASE_URL to the deployed API gateway URL.}"
: "${ADMIN_API_KEY:?Set ADMIN_API_KEY to the deployed admin token.}"

AUTH_HEADER_NAME="${AUTH_HEADER_NAME:-Authorization}"
PROCESS_AFTER="${PROCESS_AFTER:-true}"
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

prompt() {
  local label="$1"
  local default="${2:-}"
  local value

  if [[ -n "$default" ]]; then
    read -r -p "${label} [${default}]: " value
    printf '%s' "${value:-$default}"
  else
    read -r -p "${label}: " value
    printf '%s' "$value"
  fi
}

COURSE_SLUG="$(prompt "Course slug, e.g. computer-networks")"
REDDIT_URL="$(prompt "Reddit permalink")"
TITLE="$(prompt "Title")"
AUTHOR="$(prompt "Author" "unknown")"
SUBREDDIT="$(prompt "Subreddit" "OMSCS")"

echo
echo "Paste the relevant Reddit excerpt now. Finish with Ctrl-D:"
CONTENT="$(cat)"

if [[ -z "${CONTENT//[[:space:]]/}" ]]; then
  echo "Excerpt content cannot be empty." >&2
  exit 2
fi

PAYLOAD="$(
  COURSE_SLUG="$COURSE_SLUG" \
  REDDIT_URL="$REDDIT_URL" \
  TITLE="$TITLE" \
  AUTHOR="$AUTHOR" \
  SUBREDDIT="$SUBREDDIT" \
  CONTENT="$CONTENT" \
  PROCESS_AFTER="$PROCESS_AFTER" \
  python3 - <<'PY'
import json
import os

payload = {
    "course_slug": os.environ["COURSE_SLUG"].strip(),
    "title": os.environ["TITLE"].strip(),
    "url": os.environ["REDDIT_URL"].strip(),
    "content": os.environ["CONTENT"].strip(),
    "author": os.environ["AUTHOR"].strip() or "unknown",
    "subreddit": os.environ["SUBREDDIT"].strip().removeprefix("r/") or "OMSCS",
    "process_after": os.environ["PROCESS_AFTER"].lower() == "true",
}
print(json.dumps(payload))
PY
)"

curl -fsS -X POST "${API_BASE_URL}/sources/reddit/manual" \
  -H "${AUTH_HEADER_NAME}: ${AUTH_HEADER_VALUE}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  | python3 -m json.tool

echo "Manual Reddit source submitted."
