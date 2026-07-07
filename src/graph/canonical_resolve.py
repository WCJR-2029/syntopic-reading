"""Canonical-topic resolution: the ONE home for the alias-resolution contract.

The canonicalization pass (``src/graph/canonicalize.py``) merges near-duplicate
topic labels by setting ``topics.canonical_id`` on the losers ("aliases").
Nothing is deleted and ``chunk_topics`` is never rewritten, which means every
reader of topic data must apply the same two-way rule at query time:

1. **Resolve up**: any topic id or label stands for its canonical topic -
   ``COALESCE(canonical_id, id)``. An alias is never a graph node, never a
   search result, never an enumeration entry.
2. **Expand down**: a canonical topic stands for its WHOLE alias cluster's
   chunks - chunk lookups must match ``chunk_topics.topic_id`` against the
   canonical id *and* every alias id pointing at it, or merged topics silently
   lose the chunks that were tagged under their other wordings.

Hand-reimplementing this at each call site is how the rule gets omitted
(that's exactly the recall-loss bug this module fixed in ``topic_search`` and
``ideation``). Consumers MUST route through the helpers or views below and
never write ``COALESCE(canonical_id, id)`` themselves.

Two access styles are provided:

* **Per-id helpers** - :func:`resolve_canonical`, :func:`resolve_label`,
  :func:`canonical_cluster_ids`, :func:`cluster_frequency` - for point
  lookups (research agent, CLI result rows).
* **Set-based TEMP views** - :func:`ensure_canonical_views` creates
  ``topic_canonical_map(topic_id, canonical_id)`` and
  ``chunk_topics_canonical(chunk_id, topic_id)`` on the current connection -
  for bulk/aggregate SQL (co-occurrence build, ideation's book-span filter,
  keyword search). The views are TEMP (per-connection, auto-dropped) so they
  never mutate the database file.

On a database that has never been canonicalized (all ``canonical_id`` NULL),
every helper degrades to the identity mapping - callers need no special case.
"""

from typing import List, Optional

# View names, exported so consumers reference them by constant rather than by
# retyped string literals.
TOPIC_CANONICAL_MAP_VIEW = "topic_canonical_map"
CHUNK_TOPICS_CANONICAL_VIEW = "chunk_topics_canonical"


def resolve_canonical(cursor, topic_id: int) -> Optional[int]:
    """Resolve a topic id to its canonical topic id.

    Returns the canonical id (the id itself if the topic is already
    canonical/unmerged), or ``None`` if no such topic exists.
    """
    row = cursor.execute(
        "SELECT COALESCE(canonical_id, id) FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    return row[0] if row else None


def resolve_label(cursor, label: str) -> Optional[int]:
    """Resolve an exact topic label (canonical OR alias) to a canonical id.

    Returns ``None`` when no topic has that label. Labels are unique in the
    schema, so at most one row matches.
    """
    row = cursor.execute(
        "SELECT COALESCE(canonical_id, id) FROM topics WHERE label = ? LIMIT 1",
        (label,),
    ).fetchone()
    return row[0] if row else None


def canonical_cluster_ids(cursor, topic_id: int) -> List[int]:
    """All topic ids in the alias cluster that ``topic_id`` belongs to.

    Total on any member: the argument may be the canonical topic OR one of its
    aliases - it is resolved up first, then expanded down. The returned list
    always contains the canonical id and every alias pointing at it (a lone
    unmerged topic yields ``[topic_id]``). Returns ``[]`` for an unknown id.

    Use the result to expand chunk lookups::

        ids = canonical_cluster_ids(cursor, tid)
        cursor.execute(
            f"... WHERE ct.topic_id IN ({','.join('?' * len(ids))})", ids)
    """
    canon = resolve_canonical(cursor, topic_id)
    if canon is None:
        return []
    rows = cursor.execute(
        "SELECT id FROM topics WHERE id = ? OR canonical_id = ? ORDER BY id",
        (canon, canon),
    ).fetchall()
    return [r[0] for r in rows]


def cluster_frequency(cursor, topic_id: int) -> int:
    """Total ``frequency`` across the whole alias cluster of ``topic_id``.

    The canonical row's own ``frequency`` column is NOT updated by a merge, so
    reading it alone under-reports how often the merged idea occurs; this sums
    over the canonical topic and all of its aliases. Total on any cluster
    member (resolves up first); returns 0 for an unknown id.
    """
    canon = resolve_canonical(cursor, topic_id)
    if canon is None:
        return 0
    row = cursor.execute(
        "SELECT COALESCE(SUM(frequency), 0) FROM topics "
        "WHERE id = ? OR canonical_id = ?",
        (canon, canon),
    ).fetchone()
    return int(row[0] or 0)


def ensure_canonical_views(cursor) -> None:
    """Create the canonical-resolution TEMP views on this connection.

    Idempotent and side-effect-free on the database file (TEMP views live in
    the connection's temp schema and vanish when it closes). After this call
    the following are queryable:

    * ``topic_canonical_map(topic_id, canonical_id)`` - one row per topic,
      mapping every topic id (alias or not) to its canonical id.
    * ``chunk_topics_canonical(chunk_id, topic_id)`` - ``chunk_topics`` with
      ``topic_id`` already resolved to the canonical id, so grouping/joining
      on ``topic_id`` pools each merged idea's chunks onto ONE topic. May
      contain (chunk_id, topic_id) duplicates when a chunk was tagged with
      two wordings that merged - use DISTINCT where that matters.
    """
    cursor.execute(f"""
        CREATE TEMP VIEW IF NOT EXISTS {TOPIC_CANONICAL_MAP_VIEW} AS
        SELECT id AS topic_id, COALESCE(canonical_id, id) AS canonical_id
        FROM topics
    """)
    cursor.execute(f"""
        CREATE TEMP VIEW IF NOT EXISTS {CHUNK_TOPICS_CANONICAL_VIEW} AS
        SELECT ct.chunk_id AS chunk_id, m.canonical_id AS topic_id
        FROM chunk_topics ct
        JOIN {TOPIC_CANONICAL_MAP_VIEW} m ON m.topic_id = ct.topic_id
    """)
