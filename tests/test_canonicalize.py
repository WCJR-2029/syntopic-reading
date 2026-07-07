"""Tests for topic canonicalization (src/graph/canonicalize.py) and the
canonical-resolution behavior of the co-occurrence build and research agent.

All tests build their own tiny SQLite database with hand-crafted embeddings
(no sentence-transformers, no LLM, no sqlite-vec extension required), so they
run everywhere the base suite runs and never skip.
"""

import sqlite3

import numpy as np
import pytest

from src.database.operations import Database
from src.database.schema import create_database
from src.graph.canonicalize import canonicalize_topics
from src.graph.cooccurrence import build_cooccurrence_edges


DIM = 8  # canonicalize is dimension-agnostic; tiny dims keep tests readable


def _emb(base: np.ndarray, cosine: float, orth_axis: int) -> bytes:
    """Unit vector at exactly `cosine` similarity to `base` (also unit)."""
    orth = np.zeros(DIM, dtype=np.float32)
    orth[orth_axis] = 1.0
    # Gram-Schmidt so orth is exactly perpendicular to base
    orth = orth - (orth @ base) * base
    orth /= np.linalg.norm(orth)
    vec = cosine * base + np.sqrt(max(0.0, 1.0 - cosine * cosine)) * orth
    return vec.astype(np.float32).tobytes()


def _axis(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def _add_topic(cur, label: str, embedding: bytes, frequency: int = 1,
               canonical_id=None) -> int:
    cur.execute(
        "INSERT INTO topics (label, canonical_id, frequency, embedding) "
        "VALUES (?, ?, ?, ?)",
        (label, canonical_id, frequency, embedding),
    )
    return cur.lastrowid


@pytest.fixture
def db(tmp_path):
    """Fresh schema-complete database (no vec_topics - exercises the
    graceful path where the sqlite-vec index is absent)."""
    path = tmp_path / "test.db"
    create_database(path)
    with Database(path) as database:
        yield database


def _canonical_map(conn) -> dict:
    return dict(conn.execute("SELECT id, canonical_id FROM topics").fetchall())


# --------------------------------------------------------------------------- #
# Merge correctness
# --------------------------------------------------------------------------- #
def test_merge_creates_aliases_and_keeps_rows(db):
    cur = db.conn.cursor()
    base = _axis(0)
    # Three rewordings of one idea (pairwise-similar through the base vector)
    a = _add_topic(cur, "princely cruelty and mercy", base.tobytes(), frequency=3)
    b = _add_topic(cur, "a ruler's severity", _emb(base, 0.96, 1), frequency=1)
    c = _add_topic(cur, "cruelty in a prince", _emb(base, 0.94, 2), frequency=1)
    # An unrelated topic
    d = _add_topic(cur, "gardening tips", _axis(5).tobytes(), frequency=1)
    db.conn.commit()

    stats = canonicalize_topics(db, merge_threshold=0.90, knn=5)

    cmap = _canonical_map(db.conn)
    # Canonical = highest frequency member; others point at it
    assert cmap[a] is None
    assert cmap[b] == a
    assert cmap[c] == a
    assert cmap[d] is None
    # No rows deleted, embeddings kept on aliases
    assert db.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 4
    assert db.conn.execute(
        "SELECT COUNT(*) FROM topics WHERE embedding IS NOT NULL"
    ).fetchone()[0] == 4
    assert stats["aliases_created"] == 2
    assert stats["clusters_merged"] == 1
    assert stats["canonical_topics_after"] == 2


def test_canonical_selection_prefers_frequency_then_short_label(db):
    cur = db.conn.cursor()
    base = _axis(0)
    long_freq = _add_topic(cur, "a much longer topic label", base.tobytes(), frequency=5)
    short_rare = _add_topic(cur, "short label", _emb(base, 0.97, 1), frequency=1)
    db.conn.commit()

    canonicalize_topics(db, merge_threshold=0.90, knn=5)
    cmap = _canonical_map(db.conn)
    # Frequency wins over label length
    assert cmap[long_freq] is None
    assert cmap[short_rare] == long_freq


# --------------------------------------------------------------------------- #
# Threshold behavior
# --------------------------------------------------------------------------- #
def test_threshold_gates_merging(db):
    cur = db.conn.cursor()
    base = _axis(0)
    e = _add_topic(cur, "topic e", base.tobytes(), frequency=2)
    f = _add_topic(cur, "topic f", _emb(base, 0.80, 1), frequency=1)
    db.conn.commit()

    # cosine 0.80 pair: below a 0.90 threshold -> no merge
    stats_high = canonicalize_topics(db, merge_threshold=0.90, knn=5)
    assert stats_high["aliases_created"] == 0
    assert _canonical_map(db.conn) == {e: None, f: None}

    # ...but above a 0.75 threshold -> merges
    stats_low = canonicalize_topics(db, merge_threshold=0.75, knn=5)
    assert stats_low["aliases_created"] == 1
    assert _canonical_map(db.conn)[f] == e


def test_max_cluster_size_caps_transitive_chains(db):
    cur = db.conn.cursor()
    base = _axis(0)
    ids = [
        _add_topic(cur, "chain 1", base.tobytes(), frequency=4),
        _add_topic(cur, "chain 2", _emb(base, 0.97, 1), frequency=1),
        _add_topic(cur, "chain 3", _emb(base, 0.95, 2), frequency=1),
        _add_topic(cur, "chain 4", _emb(base, 0.93, 3), frequency=1),
    ]
    db.conn.commit()

    canonicalize_topics(db, merge_threshold=0.90, knn=5, max_cluster_size=2)
    cmap = _canonical_map(db.conn)
    merged = [tid for tid in ids if cmap[tid] is not None]
    # Cap of 2 allows at most 1 alias per cluster; unions that would exceed
    # the cap are skipped
    clusters = {}
    for tid in ids:
        root = cmap[tid] or tid
        clusters.setdefault(root, []).append(tid)
    assert max(len(m) for m in clusters.values()) <= 2
    assert len(merged) >= 1  # the strongest pair still merged


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_rerun_is_noop(db):
    cur = db.conn.cursor()
    base = _axis(0)
    _add_topic(cur, "idea alpha", base.tobytes(), frequency=2)
    _add_topic(cur, "idea alpha reworded", _emb(base, 0.95, 1), frequency=1)
    _add_topic(cur, "unrelated beta", _axis(4).tobytes(), frequency=1)
    db.conn.commit()

    first = canonicalize_topics(db, merge_threshold=0.90, knn=5)
    state_after_first = _canonical_map(db.conn)
    assert first["aliases_created"] == 1

    second = canonicalize_topics(db, merge_threshold=0.90, knn=5)
    assert second["aliases_created"] == 0
    assert second["clusters_merged"] == 0
    assert _canonical_map(db.conn) == state_after_first


def test_chain_flattening_preserves_one_level_invariant(db):
    """If a previous run's canonical topic later becomes an alias itself,
    its old aliases must be re-pointed (never a 2-level chain)."""
    cur = db.conn.cursor()
    base = _axis(0)
    a = _add_topic(cur, "winner", base.tobytes(), frequency=9)
    b = _add_topic(cur, "old canonical", _emb(base, 0.95, 1), frequency=1)
    # Pre-existing alias from an earlier run, pointing at b
    z = _add_topic(cur, "old alias of b", _emb(base, 0.95, 2), frequency=1,
                   canonical_id=b)
    db.conn.commit()

    canonicalize_topics(db, merge_threshold=0.90, knn=5)
    cmap = _canonical_map(db.conn)
    assert cmap[a] is None
    assert cmap[b] == a
    assert cmap[z] == a  # re-pointed, not left chained at b
    # Invariant: every alias points at a topic whose canonical_id IS NULL
    chained = db.conn.execute("""
        SELECT COUNT(*) FROM topics t
        JOIN topics c ON t.canonical_id = c.id
        WHERE c.canonical_id IS NOT NULL
    """).fetchone()[0]
    assert chained == 0


def test_dry_run_writes_nothing(db):
    cur = db.conn.cursor()
    base = _axis(0)
    _add_topic(cur, "dry one", base.tobytes(), frequency=2)
    _add_topic(cur, "dry two", _emb(base, 0.95, 1), frequency=1)
    db.conn.commit()

    stats = canonicalize_topics(db, merge_threshold=0.90, knn=5, dry_run=True)
    assert stats["aliases_created"] == 1  # reported...
    assert all(v is None for v in _canonical_map(db.conn).values())  # ...not written


# --------------------------------------------------------------------------- #
# chunk_topics resolution through canonical_id (co-occurrence build)
# --------------------------------------------------------------------------- #
def _add_book_chunk(cur, book_title: str, chunk_index: int = 0) -> int:
    cur.execute(
        "INSERT INTO books (title, author, file_path) VALUES (?, 'T', '/x')",
        (book_title,),
    )
    book_id = cur.lastrowid
    cur.execute(
        "INSERT INTO chunks (book_id, chunk_index, text, word_count) "
        "VALUES (?, ?, 'text', 10)",
        (book_id, chunk_index),
    )
    return cur.lastrowid


def test_cooccurrence_resolves_aliases_to_canonical(db):
    """The core promise: the same idea worded differently in two books
    becomes ONE node and its co-occurrences pool - so a cross-book edge
    forms that could never exist pre-merge."""
    cur = db.conn.cursor()
    base = _axis(0)
    idea_a = _add_topic(cur, "princely cruelty and mercy", base.tobytes(), frequency=2)
    idea_b = _add_topic(cur, "a ruler's severity", _emb(base, 0.95, 1), frequency=1)
    shared = _add_topic(cur, "statecraft", _axis(5).tobytes(), frequency=2)

    chunk1 = _add_book_chunk(cur, "Book One")  # uses wording A
    chunk2 = _add_book_chunk(cur, "Book Two")  # uses wording B
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)", (chunk1, idea_a))
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)", (chunk1, shared))
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)", (chunk2, idea_b))
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)", (chunk2, shared))
    db.conn.commit()

    chunk_topics_before = db.conn.execute("SELECT COUNT(*) FROM chunk_topics").fetchone()[0]

    # WITHOUT canonicalization: (idea, statecraft) co-occurs once per wording,
    # below min_cooccurrence=2 -> no edges
    stats_before = build_cooccurrence_edges(db, min_cooccurrence=2, min_pmi=0.0)
    assert stats_before["edges_stored"] == 0

    # WITH canonicalization: both wordings resolve to one node -> count 2 -> edge
    canonicalize_topics(db, merge_threshold=0.90, knn=5)
    assert _canonical_map(db.conn)[idea_b] == idea_a
    stats_after = build_cooccurrence_edges(db, min_cooccurrence=2, min_pmi=0.0)
    assert stats_after["edges_stored"] == 1

    edge = db.conn.execute(
        "SELECT topic_a_id, topic_b_id, cooccurrence_count FROM topic_edges"
    ).fetchone()
    assert set(edge[:2]) == {idea_a, shared}  # canonical ids only, no alias id
    assert edge[2] == 2

    # chunk_topics links were never rewritten or lost
    assert db.conn.execute("SELECT COUNT(*) FROM chunk_topics").fetchone()[0] == chunk_topics_before
    assert db.conn.execute(
        "SELECT COUNT(*) FROM chunk_topics WHERE topic_id = ?", (idea_b,)
    ).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# Research agent resolution through canonical_id
# --------------------------------------------------------------------------- #
def test_research_resolution_through_canonical(db, monkeypatch, tmp_path):
    """_resolve_topic_ids maps an alias's exact label to its canonical id, and
    find_chunks_for_topics expands the canonical id back over alias-linked
    chunks - so trail retrieval keeps every chunk reachable after a merge."""
    from src.agent import research as research_mod
    from src.agent.research import ResearchAgent

    cur = db.conn.cursor()
    base = _axis(0)
    canon = _add_topic(cur, "nature of happiness", base.tobytes(), frequency=2)
    alias = _add_topic(cur, "nature of true happiness", _emb(base, 0.95, 1), frequency=1)
    chunk_via_alias = _add_book_chunk(cur, "Book Alias")
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)",
                (chunk_via_alias, alias))
    db.conn.commit()

    canonicalize_topics(db, merge_threshold=0.90, knn=5)
    assert _canonical_map(db.conn)[alias] == canon

    db_path = db.db_path
    monkeypatch.setattr(research_mod, "get_db", lambda: Database(db_path))

    agent = object.__new__(ResearchAgent)  # skip __init__ (needs an LLM key)
    monkeypatch.setattr(agent.__class__, "_get_vector_search",
                        lambda self: (None, None), raising=True)

    # Exact label of the ALIAS resolves to the canonical topic id
    ids = agent._resolve_topic_ids(["nature of true happiness"])
    assert ids == [canon]

    # ...and the canonical id finds the chunk that is linked via the alias
    chunks = agent.find_chunks_for_topics(["nature of true happiness"])
    assert [c["chunk_id"] for c in chunks] == [chunk_via_alias]
