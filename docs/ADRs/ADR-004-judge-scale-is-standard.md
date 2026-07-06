# ADR-004 — The automated judge's scale is the quality standard

**Status:** Accepted (2026-06-30, the curator's T8 adjudication)
**Phase:** 6 - Quality & Visibility Layer

## Context
The full 91-trail run showed the hybrid judge agreeing with the legacy `TRAIL-RATINGS.md` at
bucket-match 52%, rank correlation ρ≈0.70, with a consistent ~−4.2 offset (judge stricter) and
compression at the top. The legacy ratings were never human-validated (84/96 were subagent-scored)
and the original analysis self-flagged a right-skew/leniency risk. Two ways to close the offset:
trust the legacy ratings and apply a +4 calibration constant, or accept the judge's scale.

The curator adjudicated a 5-trail disagreement set (Scaffolding, The Unbearable Cost of Seeing, The True
Believer's Religious Psychology, The Anatomy of Genius, The Stateless Self) — trails they had reviewed
before — and **sided with the judge on all five.**

## Decision
The automated judge's `/35` scale is the **quality system of record**. No calibration constant is
applied. `data/trails/quality-scores.json` + `QUALITY-DASHBOARD.md` supersede `TRAIL-RATINGS.md`,
which is retained as historical analysis + the rubric's source. The Phase-6 human-calibration gate
(the prerequisite blocking the self-improvement loop) is **cleared**: the judge is validated against
the curator's taste and adopted.

## Consequences
- The dashboard scores are authoritative; the ~−4 gap to legacy ratings is "legacy was generous,"
  not "judge is wrong."
- Re-running the judge after retrieval changes (Phase-6 slices #1/#2) is now a trustworthy yardstick.
- The judge remains, by design (PATTERN-OBSERVATIONS §6.5), strong at the floor + flags and
  compressed at the ceiling — exemplar ranking at the very top stays a human call, but bucket-level
  quality and structural flags are automated.
- `TRAIL-RATINGS.md` gets a superseded banner pointing at the new system of record.
