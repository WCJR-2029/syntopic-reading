# ADR-002 — Trail score persistence as a JSON sidecar (not a DB table)

**Status:** Accepted (grooming, 2026-06-30)
**Phase:** 6 - Quality & Visibility Layer

## Context
Trails live as markdown files in `data/trails/` — they are NOT rows in the DB (the `trails` /
`trail_excerpts` tables are empty). The quality judge produces structured scores per trail that the
dashboard renders and that future surfaces (per-trail card, Streamlit page) will reuse.

## Decision
Persist scores in a single **`data/trails/quality-scores.json`**, keyed by trail filename. Each entry
holds the four dimension scores, overall, structural flags, the gap note, the model used, and a
timestamp. The dashboard (`QUALITY-DASHBOARD.md`) is **generated from** this JSON. Scoring is
incremental: a trail already present (and unchanged by content hash) is skipped unless `--force`.

## Alternatives considered
- **New `trail_scores` DB table** — cleaner relationally, but trails aren't in the DB, so the table
  would key on filenames anyway; adds a schema migration for no v1 benefit. Revisit if/when trails
  become first-class DB rows.
- **Per-trail sidecar files** (one JSON per trail) — more files to manage; a single keyed JSON is
  simpler to load for a whole-library dashboard.

## Consequences
- Zero schema change; the score store is human-readable and git-diffable (a visible record of quality
  over time, which is itself part of the "visibility" goal).
- The same JSON feeds any later surface (card, HTML/Streamlit) without rework.
- Content-hash keying means re-running after a retrieval change cleanly re-scores only affected trails.
