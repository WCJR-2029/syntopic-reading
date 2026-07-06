# ADR-001 — Hybrid trail-quality judge (deterministic facts + single LLM pass)

**Status:** Accepted (grooming, 2026-06-30)
**Phase:** 6 - Quality & Visibility Layer

## Context
The library has no automated, *repeatable* quality signal. It DOES already have (discovered while
building T1): an expert **7-parameter rubric** and a **rated ground-truth set of 83 active trails**
(`data/trails/PATTERN-OBSERVATIONS.md` + `TRAIL-RATINGS.md`) — produced by a one-time offline analysis
session, never re-runnable and never human-validated by the curator. So the gap is not "invent a rubric" but
"automate the existing expert rubric and calibrate it to the curator's taste." The score must run on all 83
trails to reproduce/validate the existing ratings, and be cheap enough to re-run as a yardstick for
the next two retrieval slices.

## Decision
Use a **hybrid** engine built on the curator's **existing 7-parameter rubric** (NOT a fresh 4-dim one):
- **Rubric backbone (T1, `trail_quality_rubric.py`):** the seven parameters — conceptual through-line,
  dialectic vs. stacking, excerpt strength, synthesis depth, non-obviousness, generativity,
  anti-pattern absence — each scored **1-5**, with anchors grounded in the documented causal patterns.
- **Deterministic layer** computes structural *facts* per trail by reusing `trail_constraints.py`:
  single-author concentration %, distinct-book count, cross-trail reuse flags. Free, instant, repeatable.
- **LLM layer** makes ONE call per trail, fed the trail text **and** the deterministic facts, and
  returns the seven 1-5 scores, a one-line rationale (TRAIL-RATINGS style), and a gap note.
- **Composite = sum of the seven → /35**, using the curator's buckets (Exemplar 30-35 / Solid 22-29 /
  Mediocre 15-21 / Failure <15). The **/35 scale is deliberate** so scores are directly comparable to
  the 83 existing ratings (enables the agreement validation in T8).
- **Dashboard headline** = a 3-number rollup of the seven into the dims the curator picked (narrative
  coherence ← through-line; cross-author interplay ← dialectic; novelty honesty ← non-obviousness +
  generativity) **+ the gap note**. Headline dims are presentation only; the /35 composite uses all seven.
- Structural gate violations surface in a **Flags** column, NOT folded into the score — quality and
  rule-tripping stay distinct.

## Alternatives considered
- **Invent a fresh 4-dimension rubric** (the original groom plan) — rejected once the existing
  7-param rubric + 83-trail rated set were found. Reinventing would discard a proven, curator-authored
  framework and forgo the ground truth needed to validate the automated judge.
- **LLM-judge only** — flexible but non-deterministic and a call per trail per run; no free structural
  grounding, so the model re-derives facts it could be handed.
- **Deterministic metrics only** — free and repeatable but cannot judge narrative quality or produce
  gap analysis in plain English, which is the whole point of the visibility layer.

## Validation (consequence of the /35 choice)
Because the composite is on the same /35 scale as the 83 existing ratings, T8 validates the automated
judge by **agreement with ground truth**: rank correlation + bucket-match rate across the 83, then
the curator adjudicates only the bucket-level disagreements. This both calibrates the new judge and gives the
existing (never-human-validated) ratings their first calibration — clearing the Phase-6 human gate.

## Consequences
- v1 narrative coherence is an LLM judgment, not an embedding metric (chunks aren't embedded until
  Phase-6 slice #1 / steal #1). Forward hook: add an embedding-based consecutive-excerpt coherence
  check once chunk vectors exist, to corroborate the LLM coherence score.
- Re-running the judge after slices #1/#2 is the designed way to PROVE those changes helped (eval-first
  discipline, the gbrain steal).
- Cost is bounded: one LLM call × ~91 trails per full run; incremental caching avoids re-scoring
  unchanged trails.
