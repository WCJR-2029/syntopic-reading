# Roadmap

This is a working reference implementation of a syntopic-reading pipeline, shared for people who
want to read across their own libraries and to tinker with the approach. It works end-to-end today.
Here's what's known to be rough and what's planned next - in rough priority order. Issues and PRs
welcome.

## Planned

### Denser cross-book topic linking on small libraries
The PMI/Leiden topic graph only forms edges where the *same* topic recurs across books. Because
topic labels are LLM-generated free text, they're nearly unique per book, so on a small library the
graph is sparse and trail selection leans on novelty ranking + embedding similarity instead (see the
note in [README.md](README.md#how-it-works)). The trails are still strong, but the graph isn't
pulling its weight at small scale.

**The plan:** canonicalize semantically-equivalent topic labels *before* building co-occurrence -
cluster near-duplicate labels by embedding similarity (e.g. "princely cruelty and mercy" ≈ "a
ruler's cruelty" ≈ "severity in a prince") and merge them into a shared canonical topic, so the same
idea in three different books becomes one node the graph can link. This makes PMI edges actually
form at demo scale. (Global label dedup is quadratic and blows up on very large libraries, so this
will be scoped and bounded - likely blocked/batched by cluster - rather than run corpus-wide.)

### Cleaner excerpt rendering & source chunking
- Excerpt bodies currently render with extra blank lines between wrapped source lines; tighten the
  Markdown so excerpts read as continuous prose.
- Some public-domain EPUBs bundle multiple works in one file (e.g. Gutenberg's *The Prince* appends
  *The Life of Castruccio Castracani*); chunking can pick up the appended work as if it were the main
  text. Detect and split bundled works, or scope chunks to the primary work.

### Spend & progress visibility during ingestion
Ingesting a book makes ~2 LLM calls per chunk (a usefulness check + topic extraction), so a
multi-book ingest can run many minutes with no feedback on tokens or cost. Add a running
call/token counter (and an optional cost estimate) so you can see what a run is spending.

## Nice-to-have
- Silence third-party deprecation/warning noise on a clean run.
- Optional non-English embedding models.

## Not planned (by design)
- This is a personal thinking instrument, not a summarizer or a reading replacement. The synthesis
  it emits is explicitly a *draft to argue with* - the judgment stays with the reader. That framing
  is intentional and won't change.
