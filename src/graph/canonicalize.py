"""Scale-safe topic canonicalization: merge near-duplicate topic labels.

Topic labels are free-text LLM output and are nearly unique per book, so the
PMI co-occurrence graph rarely finds anything to link across books. This pass
clusters semantically-equivalent labels by embedding similarity and merges
each cluster into ONE canonical topic by setting ``topics.canonical_id`` on
the other members (aliases). Nothing is deleted: alias rows, their embeddings,
and all ``chunk_topics`` links stay in place - downstream consumers resolve
aliases at read time through the shared helpers/views in
``src/graph/canonical_resolve.py`` (the single home of the resolution rule).

Scale safety (the hard constraint - the real library has ~218K topics):

* NEVER builds the N x N similarity matrix (at 218K topics that would be
  ~380 GB - the documented OOM landmine in the old
  ``src/extraction/deduplicator.py``, which this module supersedes).
* Embeddings are REUSED from ``topics.embedding`` (all-MiniLM, 384-dim,
  stored unit-normalized) - never recomputed - and spilled to a temporary
  on-disk float32 memmap, so resident memory never holds the full N x d
  matrix either.
* Nearest neighbours are found with a blocked top-K scan: query blocks of
  ``query_batch`` rows stream against database blocks of ``db_batch`` rows,
  keeping only a running top-K per query row. Peak RAM is
  O(query_batch * db_batch) for the similarity block (default ~32 MB)
  plus O(P) for the retained above-threshold pairs, where P <= N * K.
  Output/state is O(N * K); it is streaming and config-driven
  (threshold, K, batch sizes, optional cluster-size cap).
* Merging is union-find over the K-NN pair list (above the cosine
  threshold), processed in descending-similarity order: O(P alpha(N)).

Compute is exact (no recall loss from an approximate index): the blocked
scan performs O(N^2 * d) multiply-adds, but in BLAS blocks - measured at
~10 ms total for N=2,482 and an extrapolated ~5-10 minutes for N=218K on
commodity hardware, versus ~20 HOURS for per-topic sqlite-vec KNN queries
(vec0 is a brute-force scan per query, ~330 ms/query at that scale) and
"out of memory" for the all-pairs matrix. Memory - the actual landmine -
stays flat and bounded regardless of N.

Invariants maintained:

* A canonical topic always has ``canonical_id IS NULL``; an alias points
  DIRECTLY at its canonical topic (one level - no chains). If a previous
  run's canonical topic later becomes an alias, its existing aliases are
  re-pointed in the same transaction.
* Re-running the pass is a no-op once fixed-point is reached: only
  ``canonical_id IS NULL`` topics are scanned, and surviving canonicals of
  distinct clusters sit below the merge threshold (guaranteed when
  ``max_cluster_size`` is unset; a cap can defer some merges to a later run).
* ``vec_topics`` rows for new aliases are removed (best-effort - requires
  the sqlite-vec extension) so semantic search returns canonical topics.
"""

import os
import sqlite3
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# Union-find
# --------------------------------------------------------------------------- #
class UnionFind:
    """Union-find with path compression and union-by-size."""

    def __init__(self):
        self.parent: Dict[int, int] = {}
        self.size: Dict[int, int] = {}

    def find(self, x: int) -> int:
        root = x
        while self.parent.get(root, root) != root:
            root = self.parent[root]
        # Path compression
        while self.parent.get(x, x) != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def cluster_size(self, x: int) -> int:
        return self.size.get(self.find(x), 1)

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        sa, sb = self.size.get(ra, 1), self.size.get(rb, 1)
        if sa < sb:
            ra, rb = rb, ra
            sa, sb = sb, sa
        self.parent[rb] = ra
        self.size[ra] = sa + sb
        return True


# --------------------------------------------------------------------------- #
# Candidate pair generation (blocked exact top-K, bounded memory)
# --------------------------------------------------------------------------- #
def _load_embeddings_memmap(
    cursor,
) -> Tuple[List[int], List[str], List[int], Optional[np.memmap], Optional[str]]:
    """Stream canonical-topic embeddings from SQLite into a disk memmap.

    Returns (topic_ids, labels, frequencies, memmap, memmap_path). The memmap
    keeps resident memory flat even at 218K x 384 floats (~335 MB on disk).
    Embeddings are stored unit-normalized (all-MiniLM via sentence-transformers),
    but we re-normalize defensively so cosine == dot product always holds.
    """
    upper_bound = cursor.execute("""
        SELECT COUNT(*) FROM topics
        WHERE canonical_id IS NULL AND embedding IS NOT NULL
    """).fetchone()[0]

    ids: List[int] = []
    labels: List[str] = []
    freqs: List[int] = []

    if upper_bound == 0:
        return ids, labels, freqs, None, None

    cursor.execute("""
        SELECT id, label, frequency, embedding
        FROM topics
        WHERE canonical_id IS NULL
          AND embedding IS NOT NULL
        ORDER BY id
    """)

    # Stream rows straight into a disk-backed memmap sized from the count
    # above - resident memory stays O(1 row) during the load.
    fd, path = tempfile.mkstemp(suffix=".embeddings.f32", prefix="canonicalize_")
    os.close(fd)
    memmap: Optional[np.memmap] = None
    dim = None
    written = 0

    for topic_id, label, freq, blob in cursor:
        vec = np.frombuffer(blob, dtype=np.float32)
        if dim is None:
            dim = len(vec)
            memmap = np.memmap(path, dtype=np.float32, mode="w+",
                               shape=(upper_bound, dim))
        elif len(vec) != dim:
            # Skip malformed/mismatched embeddings rather than corrupting math
            continue
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            continue
        memmap[written] = vec / norm
        written += 1
        ids.append(topic_id)
        labels.append(label)
        freqs.append(freq if freq is not None else 1)

    if memmap is None or written == 0:
        if memmap is not None:
            del memmap
        os.unlink(path)
        return ids, labels, freqs, None, None

    # Trim view to the rows actually written (skips shrink the count)
    return ids, labels, freqs, memmap[:written], path


def _knn_pairs_above_threshold(
    emb: np.memmap,
    n: int,
    threshold: float,
    k: int,
    query_batch: int,
    db_batch: int,
) -> Dict[Tuple[int, int], float]:
    """Blocked exact top-K scan -> {(i, j): cosine} with i < j, cosine >= threshold.

    Indices are positions into ``emb``/the ids list, not topic ids. Memory is
    bounded by the (query_batch x db_batch) similarity block and the running
    top-K buffers - the full N x N matrix is never materialized.
    """
    pairs: Dict[Tuple[int, int], float] = {}

    for qs in range(0, n, query_batch):
        qe = min(qs + query_batch, n)
        q = np.asarray(emb[qs:qe])  # (bq, d) copy - small
        bq = qe - qs

        top_sims = np.full((bq, k), -2.0, dtype=np.float32)
        top_idx = np.full((bq, k), -1, dtype=np.int64)

        for ds in range(0, n, db_batch):
            de = min(ds + db_batch, n)
            d_blk = np.asarray(emb[ds:de])  # (bd, d)
            sims = q @ d_blk.T  # (bq, bd) - the ONLY quadratic-shaped block, bounded

            # Mask self-similarity where the diagonal falls inside this block
            lo, hi = max(qs, ds), min(qe, de)
            if lo < hi:
                rows = np.arange(lo - qs, hi - qs)
                cols = np.arange(lo - ds, hi - ds)
                sims[rows, cols] = -2.0

            # Merge block results into the running top-K
            cand_sims = np.concatenate([top_sims, sims], axis=1)
            block_idx = np.broadcast_to(np.arange(ds, de), (bq, de - ds))
            cand_idx = np.concatenate([top_idx, block_idx], axis=1)

            take = min(k, cand_sims.shape[1])
            sel = np.argpartition(cand_sims, -take, axis=1)[:, -take:]
            top_sims = np.take_along_axis(cand_sims, sel, axis=1)
            top_idx = np.take_along_axis(cand_idx, sel, axis=1)

        # Emit above-threshold pairs for this query block
        keep_rows, keep_cols = np.where(top_sims >= threshold)
        for r, c in zip(keep_rows, keep_cols):
            i = qs + int(r)
            j = int(top_idx[r, c])
            if j < 0 or i == j:
                continue
            key = (i, j) if i < j else (j, i)
            sim = float(top_sims[r, c])
            if sim > pairs.get(key, -2.0):
                pairs[key] = sim

    return pairs


# --------------------------------------------------------------------------- #
# Canonical selection
# --------------------------------------------------------------------------- #
def _select_canonical(members: List[int], labels: List[str], freqs: List[int]) -> int:
    """Pick the canonical member of a cluster (returns a position index).

    Strategy (matches the spirit of the legacy deduplicator): highest
    frequency wins; ties break to the shortest label, then alphabetical,
    then lowest id-position - fully deterministic.
    """
    return min(members, key=lambda m: (-freqs[m], len(labels[m]), labels[m], m))


# --------------------------------------------------------------------------- #
# vec_topics maintenance (best-effort)
# --------------------------------------------------------------------------- #
def _remove_aliases_from_vec_topics(conn, alias_ids: List[int]) -> bool:
    """Delete alias rows from the sqlite-vec index so semantic search returns
    canonical topics only. Best-effort: requires the vec0 extension on this
    connection; on failure the index is merely stale (alias hits still resolve
    through canonical_id at read time), never wrong data."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'vec_topics'"
    ).fetchone()
    if not row:
        return False
    try:
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
        except Exception:  # noqa: BLE001 - may already be loaded/unavailable
            pass
        cur = conn.cursor()
        cur.executemany(
            "DELETE FROM vec_topics WHERE topic_id = ?",
            [(a,) for a in alias_ids],
        )
        return True
    except sqlite3.OperationalError as e:
        print(f"  Note: could not update vec_topics ({e}); semantic search "
              f"will resolve aliases through canonical_id instead.")
        return False


# --------------------------------------------------------------------------- #
# Main pass
# --------------------------------------------------------------------------- #
def canonicalize_topics(
    db_connection,
    merge_threshold: float = 0.90,
    knn: int = 10,
    max_cluster_size: Optional[int] = None,
    query_batch: int = 1024,
    db_batch: int = 8192,
    dry_run: bool = False,
) -> dict:
    """Cluster near-duplicate topic labels and merge each cluster into one
    canonical topic by setting ``canonical_id`` on the other members.

    Args:
        db_connection: Database connection (``database.operations.get_db()``)
        merge_threshold: Cosine similarity above which two labels are treated
            as the same idea. 0.90 (default) merges rewordings ("nature of
            happiness" / "nature of true happiness") while keeping genuinely
            different qualifiers apart; 0.85 is noticeably more aggressive.
        knn: Neighbourhood cap - each topic contributes at most this many
            candidate neighbours. Bounds output size at O(N * knn).
        max_cluster_size: Optional hard cap on merged-cluster size. Pairs are
            union-ed in descending-similarity order and a union is skipped if
            it would exceed the cap (guards against transitive over-merge
            chains; NOTE a cap can make re-runs perform deferred merges, i.e.
            strict idempotency holds only when unset).
        query_batch / db_batch: Block sizes for the bounded-memory KNN scan.
        dry_run: Compute and report merges without writing anything.

    Returns:
        Statistics dict (counts, largest cluster, sample clusters, config).
    """
    cursor = db_connection.conn.cursor()

    print("\nCanonicalizing topics (merging near-duplicate labels)...")
    print(f"  merge_threshold={merge_threshold}, knn={knn}, "
          f"max_cluster_size={max_cluster_size or 'unlimited'}")

    ids, labels, freqs, emb, emb_path = _load_embeddings_memmap(cursor)
    n = len(ids)
    print(f"  Scanned {n} canonical topics with embeddings")

    stats = {
        'topics_scanned': n,
        'pairs_above_threshold': 0,
        'clusters_merged': 0,
        'aliases_created': 0,
        'canonical_topics_after': n,
        'largest_cluster': 0,
        'merge_threshold': merge_threshold,
        'knn': knn,
        'sample_clusters': [],
        'dry_run': dry_run,
    }

    if n < 2 or emb is None:
        if emb_path and os.path.exists(emb_path):
            del emb
            os.unlink(emb_path)
        print("  Nothing to canonicalize.")
        return stats

    try:
        # 1. Candidate pairs: exact top-K per topic, bounded memory
        pairs = _knn_pairs_above_threshold(
            emb, n, merge_threshold, min(knn, n - 1), query_batch, db_batch
        )
        stats['pairs_above_threshold'] = len(pairs)
        print(f"  Found {len(pairs)} topic pairs at cosine >= {merge_threshold}")
    finally:
        del emb
        if emb_path and os.path.exists(emb_path):
            os.unlink(emb_path)

    if not pairs:
        print("  No merge candidates - topics are already distinct at this threshold.")
        return stats

    # 2. Union-find, strongest merges first (deterministic; the ordering only
    #    matters when max_cluster_size is set)
    uf = UnionFind()
    ordered = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    for (i, j), _sim in ordered:
        if max_cluster_size is not None:
            if uf.cluster_size(i) + uf.cluster_size(j) > max_cluster_size:
                continue
        uf.union(i, j)

    clusters: Dict[int, List[int]] = {}
    for i in set(x for pair in pairs for x in pair):
        clusters.setdefault(uf.find(i), []).append(i)
    clusters = {r: sorted(m) for r, m in clusters.items() if len(m) >= 2}

    # 3. Canonical selection + alias mapping
    alias_to_canonical: List[Tuple[int, int]] = []  # (canonical_topic_id, alias_topic_id)
    sample_clusters: List[List[str]] = []
    largest = 0
    for members in clusters.values():
        canon_pos = _select_canonical(members, labels, freqs)
        largest = max(largest, len(members))
        for m in members:
            if m != canon_pos:
                alias_to_canonical.append((ids[canon_pos], ids[m]))
        if len(sample_clusters) < 15:
            sample_clusters.append(
                [labels[canon_pos]] + [labels[m] for m in members if m != canon_pos]
            )

    stats['clusters_merged'] = len(clusters)
    stats['aliases_created'] = len(alias_to_canonical)
    stats['canonical_topics_after'] = n - len(alias_to_canonical)
    stats['largest_cluster'] = largest
    stats['sample_clusters'] = sample_clusters

    print(f"  Merging {len(clusters)} clusters "
          f"({len(alias_to_canonical)} topics become aliases; "
          f"largest cluster: {largest})")

    if dry_run:
        print("  DRY RUN - no changes written.")
        return stats

    # 4. Write merges. Alias rows are NEVER deleted and chunk_topics is never
    #    touched - resolution happens at read time through the shared helpers
    #    in src/graph/canonical_resolve.py (COALESCE(canonical_id, id) + alias-
    #    cluster expansion). Ensure the index that makes cluster expansion
    #    (WHERE canonical_id = ?) cheap exists even on databases created before
    #    it was added to the schema.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_topics_canonical ON topics(canonical_id)"
    )
    cursor.executemany(
        "UPDATE topics SET canonical_id = ? WHERE id = ?",
        alias_to_canonical,
    )
    # Preserve the one-level invariant: if a topic that just became an alias
    # was itself the canonical target of older aliases, re-point them.
    cursor.executemany(
        "UPDATE topics SET canonical_id = ? WHERE canonical_id = ?",
        alias_to_canonical,
    )
    db_connection.conn.commit()

    # 5. Keep the vector index canonical-only (best-effort)
    _remove_aliases_from_vec_topics(
        db_connection.conn, [alias for _canon, alias in alias_to_canonical]
    )
    db_connection.conn.commit()

    print(f"  Done: {stats['canonical_topics_after']} canonical topics remain "
          f"(was {n}).")
    return stats


if __name__ == "__main__":
    from src.database.operations import get_db

    print("\nTesting Topic Canonicalization")
    print("=" * 60)
    with get_db() as db:
        result = canonicalize_topics(db, merge_threshold=0.90, knn=10)
        print("\nSample merged clusters (canonical first):")
        for cluster in result['sample_clusters']:
            print("  * " + " | ".join(cluster))
