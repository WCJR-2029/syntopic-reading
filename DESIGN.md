# Topic Canonicalization Design

**Feature:** merge semantically-equivalent topic labels into one canonical topic
before PMI/Leiden graph construction, so the same idea worded differently across
books ("princely cruelty and mercy" ≈ "a ruler's severity" ≈ "cruelty in a
prince") becomes ONE node the graph can link across those books.

**Entry point:** `python scripts/build_graph.py
[--merge-threshold 0.90] [--merge-knn 10] [--merge-max-cluster N]
[--no-canonicalize]`
(**on by default** — the pass runs before every graph build; `--no-canonicalize`
opts out, e.g. to rebuild edges quickly without re-scanning topic similarity.
The scan costs seconds on a small library and ~12 minutes / <1 GB peak RAM on a
very large ~225K-topic one, measured on the real library.)

**Resolution contract:** `src/graph/canonical_resolve.py` is the single home
of the read-time rule — a topic id/label resolves UP to
`COALESCE(canonical_id, id)`, and a canonical topic's chunk lookups expand
DOWN over its whole alias cluster. Every consumer (co-occurrence, trail
research, topic search CLI, ideation) routes through its helpers or TEMP
views; none hand-writes the SQL.

**Module:** `src/graph/canonicalize.py` (supersedes the all-pairs
`src/extraction/deduplicator.py`, which computes a full N×N
`cosine_similarity` matrix and re-encodes every label — the documented O(N²)
OOM landmine this design exists to avoid).

---

## Why the graph was vestigial

Topic labels are free-text LLM output. On the 5-book demo library only ~0.2%
of labels appear in ≥2 books (5 of 2,482), so within-chunk co-occurrence with
`min_cooccurrence=2` almost never fires: 2,482 nodes, 20 edges, 9 non-singleton
communities. Trails still work because retrieval falls back to embeddings +
novelty, but the advertised PMI/Leiden layer underdelivers.

## Clustering approach

1. **Candidate pairs — blocked exact top-K over reused embeddings.**
   Every canonical topic (`canonical_id IS NULL AND embedding IS NOT NULL`)
   is streamed from SQLite into a temporary on-disk float32 memmap
   (embeddings are the stored all-MiniLM 384-dim vectors — REUSED, never
   recomputed; they are stored unit-normalized, and are defensively
   re-normalized on load, so cosine ≡ dot product).
   A double-blocked scan (query blocks of 1,024 × database blocks of 8,192)
   computes similarity blocks and keeps only a *running top-K per topic*
   (K = `--merge-knn`, default 10). Pairs with cosine ≥ `--merge-threshold`
   survive.

2. **Merge — union-find over the K-NN pair list.** Pairs are union-ed in
   descending-similarity order (deterministic). An optional
   `--merge-max-cluster` cap skips unions that would exceed the cap,
   guarding against transitive over-merge chains at aggressive thresholds
   (see threshold rationale). Union-find with path compression:
   O(P·α(N)), P = retained pairs ≤ N·K.

3. **Canonical selection.** Per cluster: highest `frequency` wins; ties break
   to shortest label, then alphabetical, then lowest id — fully deterministic
   (same spirit as the legacy deduplicator).

4. **Write.** Every non-canonical member gets `canonical_id = <canonical id>`.
   **Nothing is deleted**: alias rows, their embeddings, and all
   `chunk_topics` links stay in place. New-alias rows are removed from the
   `vec_topics` sqlite-vec index (best-effort; if the extension isn't loadable
   the index is merely stale, and read paths resolve aliases anyway).

### Invariants

- A canonical topic always has `canonical_id IS NULL`; an alias points
  **directly** at its canonical topic — one level, never a chain. If a
  previous run's canonical topic itself becomes an alias, its old aliases are
  re-pointed in the same transaction
  (`UPDATE topics SET canonical_id = :new WHERE canonical_id = :demoted`).
- **Idempotent:** only `canonical_id IS NULL` topics are scanned, so aliases
  never re-enter the pool; after a run, surviving canonicals of distinct
  clusters sit below the threshold, so a re-run finds 0 pairs (verified live
  and in tests). Exception: with `--merge-max-cluster` set, deferred unions
  may complete on a later run — the cap trades strict idempotency for
  chain-guarding, which is why it's off by default.
- Reversible: `UPDATE topics SET canonical_id = NULL` restores the pre-merge
  world exactly (no data was destroyed or rewritten anywhere else).

## Scale safety: the O(N·K)-not-O(N²) argument

The real library has ~218K topics. The landmine is **quadratic memory**:

- N×N similarity matrix at 218K topics ≈ **380 GB** → guaranteed OOM.
  This pass **never materializes it**. The largest transient allocation is one
  `query_batch × db_batch` similarity block (1,024 × 8,192 × 4 B ≈ **32 MB**),
  constant in N.
- Even the full N×d embedding matrix (~335 MB at 218K) is kept **on disk**
  (memmap) rather than resident; rows are paged in per block and evictable
  under memory pressure.
- Retained state is **O(N·K)**: per-topic top-K buffers and the
  above-threshold pair dict (≤ N·K entries; in practice far fewer because of
  the threshold), plus O(N) union-find parents.

Time complexity, stated honestly: the scan is *exact*, so it performs
O(N²·d) multiply-adds — but as BLAS block matmuls, which is the standard
exact-KNN tradeoff when you refuse both an extra ANN dependency and recall
loss. **Measured**: N = 2,482 (demo) completes in well under a second;
N = 218,000 synthetic 384-dim embeddings completes the full blocked top-K scan
in **~649 s (10.8 min) with peak RSS 687 MB** on commodity hardware (Ryzen-class
CPU) — a one-time cost per library build. For contrast, the two rejected shapes:

| Shape | Memory | Wall-clock @218K |
|---|---|---|
| All-pairs matrix (`deduplicator.py`) | ~380 GB | OOM before finishing |
| Per-topic sqlite-vec KNN queries | fine | ~20 h (vec0 is a brute-force scan per query; measured 3.8 ms at N=2.5K → ~330 ms at 218K, × 218K queries) |
| **Blocked top-K scan (this design)** | **~32 MB working set + O(N·K) state** | **~11 min (measured)** |

Candidate generation is isolated in `_knn_pairs_above_threshold()`; if the
library grows past ~1M topics, it can be swapped for a true ANN index (FAISS /
hnswlib) without touching merge, selection, or write logic.

## Threshold rationale (default 0.90)

Empirical bands on the 5-book demo (all-MiniLM cosine between topic labels):

- **≥ 0.95** — pure rewordings: "guelph and ghibelline conflicts" ↔ "guelph
  and ghibelline conflict", "science versus philosophy" ↔ "philosophy versus
  science".
- **0.90–0.95** — same idea, different phrasing: "nature of happiness" ↔
  "nature of true happiness", "impermanence of worldly things" ↔
  "impermanence of all things". Safe to merge.
- **0.85–0.90** — mostly synonyms but with real casualties: "wagner and
  musical romanticism" ↔ "wagner and *french* romanticism", "stoic philosophy
  concepts" ↔ "stoic philosophy *critique*". Different qualifiers start to
  blur.
- **< 0.85** — related-but-distinct: "virtue and pleasure" ↔ "virtue and
  suffering".

Worse, union-find is transitive: at 0.85 the demo produced a **46-member
chain** joining "virtue as a mean" to "moral psychology of vice" through
intermediate hops — each link plausible, the endpoints clearly distinct
ideas. At 0.90 the largest cluster is 9 members and every inspected cluster is
a genuine synonym set. Hence: **0.90 default** (conservative, no observed
over-merges), `--merge-threshold 0.85` available for aggressive merging, best
paired with `--merge-max-cluster` to cap chains.

## How canonical_id + chunk_topics resolution works

`chunk_topics` is **never rewritten**. Aliases are resolved at read time, and
the resolution rule lives in exactly one module —
**`src/graph/canonical_resolve.py`** — as per-id helpers (`resolve_canonical`,
`resolve_label`, `canonical_cluster_ids`, `cluster_frequency`) and two TEMP
views for bulk SQL (`topic_canonical_map(topic_id, canonical_id)` and
`chunk_topics_canonical(chunk_id, topic_id)`). Consumers never hand-write
`COALESCE(canonical_id, id)`; a `grep -rn COALESCE src/ scripts/` finds it
only inside the resolver module. How each consumer routes through it:

- **Co-occurrence** (`src/graph/cooccurrence.py`): chunk→topic links and topic
  frequencies both load from the `chunk_topics_canonical` view, so a chunk
  tagged with any wording of an idea counts toward the ONE canonical node and
  PMI probabilities stay consistent. With zero aliases the view is the
  identity mapping — the queries are equivalent to the old ones.
- **Graph / Leiden / novelty**: already operate on `canonical_id IS NULL`
  topics — they inherit the merged world with no changes.
- **Trail research** (`src/agent/research.py`): exact-label lookup goes
  through `resolve_label` (an alias's label resolves to its canonical topic),
  semantic hits through `resolve_canonical`, and chunk lookup expands a
  canonical id back over its whole cluster via `canonical_cluster_ids` — so
  every chunk reachable before a merge stays reachable after it.
- **Topic search CLI** (`src/cli/topic_search.py`): `keyword` matches
  canonical **and alias** labels and returns the canonical topic once, with
  the cluster-total frequency (pre-fix, a topic merged under a different
  wording was unfindable by its alias's words); `similar` resolves stale
  alias hits from the vec index and de-duplicates onto canonicals;
  `novel`/`hubs` report cluster-total frequencies.
- **Ideation** (`src/agent/ideation.py`): the book-span gate counts books over
  the `chunk_topics_canonical` view (pre-fix, a merged topic's span was
  undercounted and it was wrongly dropped below the ≥4-book minimum), and the
  freshness probe expands over `canonical_cluster_ids`.
- **Anchored retrieval** joins `topics` rows directly by id and alias rows
  keep their embeddings, so that path is unaffected.

A supporting index, `idx_topics_canonical ON topics(canonical_id)`, ships in
the schema and is also created (idempotently) by the canonicalization pass, so
cluster expansion stays cheap on pre-existing databases.

## Results (5-book demo, `verify.db` copy)

| Metric | Before | After (default pass, threshold 0.90) |
|---|---|---|
| Rows in `topics` | 2,482 | 2,482 (nothing deleted) |
| Canonical topics | 2,482 | 2,272 (210 aliases, 138 clusters, max 9) |
| Topics spanning ≥2 books | 5 | 11 |
| Graph edges | 20 | **50** |
| Non-singleton level-0 communities | 9 | **24** (58 topics) |

Re-running the pass on the merged database: 0 pairs, 0 merges (no-op).
Full suite: 73 passed, 6 skipped (baseline was 64 + 9 new canonicalization
tests in `tests/test_canonicalize.py`).
