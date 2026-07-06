# ADR-006 — Human reactions sidecar (`data/trails/reactions.json`)

**Status:** Accepted (Phase 8, 2026-07-04)
**Phase:** 8 - Reading Room

## Context
Phase 8 makes the dashboard capture the curator's reading encounters: read-state, a 1-5 rating,
per-excerpt margin notes and stars, theme tags, and free-text reactions. This human data needs a
home. ADR-004 established `quality-scores.json` as the automated judge's system of record — mixing
human data into it would blur that boundary and complicate incremental re-scoring. The SQLite DB
is pipeline-owned and rebuild-prone; human reactions must survive any pipeline rebuild.

## Decision
A separate JSON sidecar, `data/trails/reactions.json`, keyed by trail filename (same key as
quality-scores.json so joins are trivial). Written ONLY by the dashboard (and hand-edits), never
by the pipeline or the judge. Atomic writes (temp file + `os.replace`). Git-tracked.

Schema per trail entry (all fields optional; absent trail = unread):
```json
{
  "<trail_file>.md": {
    "state": "unread | reading | read | archived",
    "started_at": "ISO-8601", "finished_at": "ISO-8601",
    "rating": 1-5,
    "reaction": "trail-level free text - what did this change in my thinking?",
    "excerpts": {"<seq>": {"starred": true, "note": "margin note"}},
    "tags": ["theme-tag", ...],
    "updated_at": "ISO-8601"
  }
}
```

- **The curator's rating is 1-5, not /35.** The 7-parameter rubric is the judge's instrument; forcing it
  on a human kills the capture habit. T8 showed bucket-level human judgment is the valuable signal.
- **`archived` lives here** (it's a human triage verdict, not a judge output) — supersedes the v2
  deferral note that wanted a separate triaged/archived sidecar.
- Excerpt keys are the trail's `sequence_order` as strings (JSON object keys).

## Alternatives considered
- **Fold into quality-scores.json** — rejected: violates ADR-004's single-writer boundary; a
  `--force` judge re-run must never be able to clobber human notes.
- **SQLite table** — rejected: DB is rebuildable pipeline state; reactions are precious and want
  git history + hand-editability + trivial reads from downstream essay tooling.
- **One file per trail** (à la trails themselves) — rejected: 90+ tiny files for structured data
  that is always read whole; single-file matches the established ADR-002 pattern.

## Consequences
- Judge-vs-curator agreement (slice D) is a pure join on filename across the two sidecars.
- downstream essay tooling reads reactions.json directly (seed packets embed the relevant notes anyway).
- If a trail file is renamed, its reactions entry must be renamed with it (the same caveat
  already true of quality-scores.json; acceptable at this scale).
