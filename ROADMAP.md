# Roadmap

This is a working reference implementation of a syntopic-reading pipeline, shared for people who
want to read across their own libraries and to tinker with the approach. It works end-to-end today.
Here's what's known to be rough and what's planned next - in rough priority order. Issues and PRs
welcome.

## Planned
Nothing currently queued - see [Nice-to-have](#nice-to-have) for smaller open items.

## Nice-to-have
- Optional non-English embedding models.
- **Within-book ingest resume.** `add_book.py` resumes at the *book* level (re-running skips
  finished books), but a book is only committed once fully processed - so a large book (e.g.
  Aristotle, ~11 min) killed mid-run loses that book's progress and re-ingests from scratch. Persist
  chunk/topic progress incrementally so an interrupted big book resumes where it left off. (Run long
  ingests detached, not in a short-timeout foreground, until then.)
- **Anchored mode could leverage canonicalization.** `--anchor-book` retrieval
  (`src/agent/anchored_retrieval.py`) still finds cross-book bridges by exact topic label rather
  than through `canonical_id`, so it doesn't yet benefit from topic merging the way the default
  `--idea` path does. It is not *broken* by canonicalization (behaviour is unchanged), just not
  enhanced by it - routing its bridge and chunk lookups through `canonical_resolve` would let
  anchored trails span merged topics too.

## Shipped
- **Denser cross-book topic linking (topic canonicalization)** - `build_graph.py` now merges
  semantically-equivalent topic labels ("princely cruelty and mercy" ≈ "a ruler's severity") into
  one canonical topic *by default* before building co-occurrence, so the same idea worded
  differently across books becomes one graph node (demo library: 20 → 50 edges). Scale-safe
  (bounded-memory blocked KNN, ~12 min on a ~225K-topic library), reversible, idempotent; opt out
  with `--no-canonicalize`. Every read path resolves aliases through one shared module
  (`src/graph/canonical_resolve.py`). Design notes: [DESIGN.md](DESIGN.md).
- **Cleaner excerpt rendering** - excerpt text carries hard-wrapped line breaks from the source
  EPUB (a newline at every HTML text-node boundary, including inline tags); `format_trail_markdown`
  in `src/agent/polish.py` now dewraps them back into real paragraphs before quoting, and quotes
  every line (including paragraph-break blanks, as a bare `>`) so an excerpt renders as one
  continuous blockquote instead of dozens of blank-line-separated fragments. Dewrapping runs
  *before* highlight matching, so a highlight phrase that used to span a line break can match again.
- **No more appended-work bleed in the demo library** - `bootstrap_demo.py`'s Gutenberg edition of
  *The Prince* (ID 1232, W. K. Marriott translation) bundled a second work, *The Life of Castruccio
  Castracani of Lucca*, after The Prince's own ending; chunking had no way to know the appended text
  wasn't part of The Prince. Switched to Gutenberg ID 57037 (Luigi Ricci translation), verified by
  hand to be The Prince only (ends on "THE END" straight into the standard license boilerplate).
- **Silenced known-harmless warnings on a clean run** - three warnings that showed up on an
  otherwise clean ingest/trail-generation run, each silenced narrowly (not a blanket filter):
  a sentence-transformers `FutureWarning` off `get_sentence_embedding_dimension()`, huggingface_hub's
  "unauthenticated requests" HF_TOKEN notice (a `logging` call, not a `warnings` one - silenced by
  raising that specific logger's level, in `src/embeddings/embedding_model.py`), and the `runpy`
  `RuntimeWarning` from `python -m src.agent.trail_generator` (caused by `src/agent/__init__.py`
  eagerly importing `trail_generator`, so runpy finds it already in `sys.modules` - filtered in
  `src/agent/__init__.py`, before that import runs).
- **Spend visibility during ingestion** - `LLMClient` (`src/llm/client.py`) now tracks a running
  count of actual provider calls (every attempt, including retries); `process_with_topics.py` and
  `extract_missing_topics.py` print it periodically during a long ingest and as a total at the end.
  Deliberately just a call count, not token/dollar accounting - neither provider SDK response is
  parsed for usage, so there's nothing invasive to add here.

## Not planned (by design)
- This is a personal thinking instrument, not a summarizer or a reading replacement. The synthesis
  it emits is explicitly a *draft to argue with* - the judgment stays with the reader. That framing
  is intentional and won't change.
