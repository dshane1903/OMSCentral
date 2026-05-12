# Reddit Link Discovery

Reddit denied automated API access, so production Reddit ingestion should stay
curated. The scalable workflow is:

1. Discover candidate r/OMSCS links with a search API.
2. Review the CSV and mark strong threads as approved.
3. Import only approved threads as curated Reddit evidence.

## Collect Candidate Links

The collector currently supports Brave Search API.

```bash
export BRAVE_SEARCH_API_KEY=...

scripts/collect-reddit-links.py \
  --course GA \
  --course ML \
  --course AI \
  --per-course 100 \
  --out reddit-candidates.csv
```

Omit `--course` to collect candidates for all priority courses:

- GA
- ML
- AI
- CN
- SDP
- GIOS
- AOS
- DBS
- HCI
- ML4T
- DL
- RL
- NLP

The output CSV has an `approved` column. Use that for review before importing.
