# ADR-003 — Structural flags inform, they do not hard-cap

**Status:** Accepted (2026-06-30, after the full 83-trail run)
**Phase:** 6 - Quality & Visibility Layer
**Supersedes:** the hard-cap rule noted in ADR-001 / red-team A5-A6.

## Context
ADR-001 wired a hard-cap: any HARD structural flag (within-trail verbatim repetition OR
description-content mismatch) capped a trail's composite below 16, regardless of the LLM's read.
This passed the 6-trail probes (none tripped it). The full 83-trail run exposed it as the single
largest error source:
- 14 trails were capped to 15; most were genuinely good (e.g. The Dopamine Paradox — the curator's 33,
  used as a calibration anchor — LLM-scored 25, capped to 15; The Stateless Self — 33 — raw 27 → 15).
- Removing the cap lifted rank correlation vs ground truth from **Spearman 0.45 → 0.70**.
- Root cause: the description-content-mismatch detector is false-positive-prone — which is exactly
  why `trail_constraints.validate_trail` keeps it **opt-in** (`check_description=False`). Wiring it
  always-on AND as an absolute floor compounded a heuristic's false positives into catastrophic
  mis-scores.

## Decision
Structural flags are **informational only**: surfaced in the dashboard Flags column and fed to the
LLM (which weighs them within the holistic 7-param score). They do **not** cap, floor, or override
the composite. `composite = sum(scores)`, always. Capping is disabled in `trail_quality.py`.

## Alternatives considered
- **Keep a gentler cap / penalty** (e.g. −3 per HARD flag) — deferred; no evidence yet that the LLM
  under-penalizes these once it sees the flag. Revisit only if the curator's T8 adjudication shows
  verbatim-repeat trails are systematically over-scored.
- **Demote only description-mismatch, keep verbatim-repeat as a cap** — verbatim repetition is a
  documented hard-failure signal, but a hard floor is still too blunt; the LLM already sees it.

## Consequences
- Existing scores re-derived from cached 7-param scores (zero new LLM calls); dashboard regenerated.
- The remaining gap to ground truth is a near-constant ~−4.2 offset + top compression — a calibration
  question for T8 (the curator's adjudication), not a structural bug.
- Lesson logged: do not promote a false-positive-prone, deliberately-opt-in heuristic to an always-on
  hard override.
