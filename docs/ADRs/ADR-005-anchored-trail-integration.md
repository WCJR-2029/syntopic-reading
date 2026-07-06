# ADR-005 — Anchored/steered trails: integration into trail_generator

**Status:** Accepted (2026-07-01)
**Phase:** 7 — anchored/steered trails
**Related:** `scripts/anchored_trail.py` (prototype), ADR-003 (no-hard-cap), ADR-001 (quality judge)

## Context

`trail_generator.py --idea "..."` cannot steer a trail to feature a specific book or a
specific cross-book bridge. Its STAGE-1 ideation re-derives its own high-novelty search
topics from the seed, so the anchor book is typically excluded entirely (verified
2026-07-01: five `--idea` runs anchored on *The Case Against Reality* produced zero
excerpts from it). A working prototype (`scripts/anchored_trail.py`) proved the fix:
require anchor chunks → embedding-centroid over topic vectors → union shared-topic
bridges → assemble. We are folding that into the generator as a first-class capability.

## Decision

1. **Integration point — anchor bypasses STAGE-1.** When `--anchor-book`/`--anchor-chunks`
   is provided, ideation's topic-invention is skipped; the anchor centroid + shared-topic
   bridges drive STAGE-2 retrieval. STAGE-1 still runs *only* to write the title/description
   from the anchor. STAGE-3 (polish) and STAGE-3.5 (gate) are unchanged in structure.
   `--idea` path is untouched — strict backward compatibility.

2. **Seed-topic source — explicit-or-derived.** The centroid is built from `--seed-topics`
   (existing labels) when given (full steering); otherwise auto-derived from the anchor
   book's own strongest topics (zero-config "just point at the book").

3. **Connection notes — generator STAGE-3 polish authors them.** Anchored trails are
   fully automated (one command → finished trail), same UX as `--idea`. The manual
   LLM-editorial path remains available via the prototype's JSON pack, but is not the
   default.

4. **Quality gate — keep, plus two fixes.** Retain the 45% single-author cap + 4-book
   minimum (anchor book counts as one author like any other). Add: (a) a back-matter
   filter (drop index/bibliography/copyright/TOC chunks, which the max-word-count-per-topic
   heuristic favors), and (b) a fix for the duplicate-excerpt false-kill observed
   2026-07-01 — dedupe identical excerpt text and backfill rather than reject the trail.

5. **Code structure — shared module.** Retrieval core moves to
   `src/agent/anchored_retrieval.py`; `trail_generator.py` imports it and
   `scripts/anchored_trail.py` becomes a thin CLI wrapper over the same code. No
   duplicated logic (avoids the drift that caused the hardcoded-model-name bug).

## Alternatives considered

- *Augment STAGE-2 only* (keep ideation, merge anchor centroid): rejected — invented
  topics still dilute the anchor and can pull the book back out.
- *Separate `--anchored` codepath*: rejected — two flows to maintain, more duplication.
- *Relax author cap in anchored mode*: rejected for now — keep one gate; revisit if
  legitimate book-centric trails get rejected in practice (would become ADR-00x).
- *Delete the prototype script*: rejected — the shared-module approach keeps the
  standalone/JSON-pack path with zero duplication.

## Consequences

- One capability, two entry points (generator flags + thin script), one retrieval core.
- Retrieval becomes testable in isolation (module boundary).
- Embedding facts this relies on: `topics.embedding` = 384-dim MiniLM, L2-normalized
  (cosine == dot); all 225k topics embedded; `vec0` extension broken/unneeded; topics
  dedupe by exact label (`canonical_id` NULL) so a shared label *is* a bridge.
- Gate fixes benefit `--idea` trails too (the dup-kill bug was general).
