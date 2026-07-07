"""Tests for the canonical-resolution helper module
(src/graph/canonical_resolve.py) and the two consumers newly routed through
it: the topic-search CLI (src/cli/topic_search.py) and the ideation agent
(src/agent/ideation.py).

The contract under test: **a canonical topic stands for its whole alias
cluster's chunks, on every path** - lookups by an alias's id or label resolve
UP to the canonical topic, and chunk/book lookups on a canonical topic expand
DOWN over every alias. Same self-contained style as tests/test_canonicalize.py:
tiny hand-built SQLite databases, no LLM, no sentence-transformers, no
sqlite-vec.
"""

import numpy as np
import pytest
from click.testing import CliRunner

from src.database.operations import Database
from src.database.schema import create_database
from src.graph.canonical_resolve import (
    CHUNK_TOPICS_CANONICAL_VIEW,
    canonical_cluster_ids,
    cluster_frequency,
    ensure_canonical_views,
    resolve_canonical,
    resolve_label,
)
from src.graph.canonicalize import canonicalize_topics


DIM = 8


def _axis(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def _emb(base: np.ndarray, cosine: float, orth_axis: int) -> bytes:
    orth = np.zeros(DIM, dtype=np.float32)
    orth[orth_axis] = 1.0
    orth = orth - (orth @ base) * base
    orth /= np.linalg.norm(orth)
    vec = cosine * base + np.sqrt(max(0.0, 1.0 - cosine * cosine)) * orth
    return vec.astype(np.float32).tobytes()


def _add_topic(cur, label: str, embedding: bytes = None, frequency: int = 1,
               canonical_id=None) -> int:
    cur.execute(
        "INSERT INTO topics (label, canonical_id, frequency, embedding) "
        "VALUES (?, ?, ?, ?)",
        (label, canonical_id, frequency, embedding),
    )
    return cur.lastrowid


def _add_book_chunk(cur, book_title: str, chunk_index: int = 0,
                    text: str = "text") -> int:
    cur.execute(
        "INSERT INTO books (title, author, file_path) VALUES (?, 'T', '/x')",
        (book_title,),
    )
    book_id = cur.lastrowid
    cur.execute(
        "INSERT INTO chunks (book_id, chunk_index, text, word_count) "
        "VALUES (?, ?, ?, 10)",
        (book_id, chunk_index, text),
    )
    return cur.lastrowid


def _link(cur, chunk_id: int, topic_id: int) -> None:
    cur.execute("INSERT INTO chunk_topics (chunk_id, topic_id) VALUES (?, ?)",
                (chunk_id, topic_id))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    create_database(path)
    with Database(path) as database:
        yield database


@pytest.fixture
def merged_db(db):
    """A database with one merged cluster (canon <- alias1, alias2) and one
    unmerged topic, plus chunks in four distinct books:

    - book 1 chunk tagged with the CANONICAL wording
    - books 2 and 3: chunks tagged only with ALIAS wordings
    - book 4 chunk tagged with the alias2 wording AND the unmerged topic

    Returns (db, ids-dict).
    """
    cur = db.conn.cursor()
    canon = _add_topic(cur, "nature of happiness", frequency=3)
    alias1 = _add_topic(cur, "nature of true happiness", frequency=2,
                        canonical_id=canon)
    alias2 = _add_topic(cur, "the happy life", frequency=1,
                        canonical_id=canon)
    other = _add_topic(cur, "gardening tips", frequency=5)

    chunk1 = _add_book_chunk(cur, "Book One")
    chunk2 = _add_book_chunk(cur, "Book Two")
    chunk3 = _add_book_chunk(cur, "Book Three")
    chunk4 = _add_book_chunk(cur, "Book Four")
    _link(cur, chunk1, canon)
    _link(cur, chunk2, alias1)
    _link(cur, chunk3, alias2)
    _link(cur, chunk4, alias2)
    _link(cur, chunk4, other)
    db.conn.commit()

    ids = dict(canon=canon, alias1=alias1, alias2=alias2, other=other,
               chunks=[chunk1, chunk2, chunk3, chunk4])
    return db, ids


# --------------------------------------------------------------------------- #
# Resolver primitives
# --------------------------------------------------------------------------- #
def test_resolve_canonical(merged_db):
    db, ids = merged_db
    cur = db.conn.cursor()
    # Canonical and unmerged topics resolve to themselves
    assert resolve_canonical(cur, ids["canon"]) == ids["canon"]
    assert resolve_canonical(cur, ids["other"]) == ids["other"]
    # Aliases resolve up
    assert resolve_canonical(cur, ids["alias1"]) == ids["canon"]
    assert resolve_canonical(cur, ids["alias2"]) == ids["canon"]
    # Unknown id -> None
    assert resolve_canonical(cur, 999_999) is None


def test_resolve_label(merged_db):
    db, ids = merged_db
    cur = db.conn.cursor()
    assert resolve_label(cur, "nature of happiness") == ids["canon"]
    # An ALIAS label resolves to the canonical id - this is the lookup path
    assert resolve_label(cur, "the happy life") == ids["canon"]
    assert resolve_label(cur, "gardening tips") == ids["other"]
    assert resolve_label(cur, "no such label") is None


def test_canonical_cluster_ids_total_on_any_member(merged_db):
    db, ids = merged_db
    cur = db.conn.cursor()
    cluster = sorted([ids["canon"], ids["alias1"], ids["alias2"]])
    # Same full cluster whether asked via the canonical or ANY alias
    assert canonical_cluster_ids(cur, ids["canon"]) == cluster
    assert canonical_cluster_ids(cur, ids["alias1"]) == cluster
    assert canonical_cluster_ids(cur, ids["alias2"]) == cluster
    # Unmerged topic is its own singleton cluster
    assert canonical_cluster_ids(cur, ids["other"]) == [ids["other"]]
    # Unknown id -> empty
    assert canonical_cluster_ids(cur, 999_999) == []


def test_cluster_frequency_sums_whole_cluster(merged_db):
    db, ids = merged_db
    cur = db.conn.cursor()
    # 3 (canon) + 2 (alias1) + 1 (alias2) = 6, from any member
    assert cluster_frequency(cur, ids["canon"]) == 6
    assert cluster_frequency(cur, ids["alias1"]) == 6
    assert cluster_frequency(cur, ids["other"]) == 5
    assert cluster_frequency(cur, 999_999) == 0


def test_chunk_topics_canonical_view_pools_alias_chunks(merged_db):
    db, ids = merged_db
    cur = db.conn.cursor()
    ensure_canonical_views(cur)
    rows = cur.execute(f"""
        SELECT DISTINCT chunk_id FROM {CHUNK_TOPICS_CANONICAL_VIEW}
        WHERE topic_id = ? ORDER BY chunk_id
    """, (ids["canon"],)).fetchall()
    # All four chunks - including the three linked only via aliases
    assert [r[0] for r in rows] == ids["chunks"]
    # No row in the view carries an alias id
    alias_rows = cur.execute(f"""
        SELECT COUNT(*) FROM {CHUNK_TOPICS_CANONICAL_VIEW}
        WHERE topic_id IN (?, ?)
    """, (ids["alias1"], ids["alias2"])).fetchone()[0]
    assert alias_rows == 0
    # Idempotent, and a no-op on the database file (TEMP views only)
    ensure_canonical_views(cur)
    assert cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'"
    ).fetchone()[0] == 0


def test_identity_mapping_on_never_canonicalized_db(db):
    """On a database with no merges every helper degrades to the identity."""
    cur = db.conn.cursor()
    t = _add_topic(cur, "lone topic", frequency=7)
    c = _add_book_chunk(cur, "Only Book")
    _link(cur, c, t)
    db.conn.commit()

    assert resolve_canonical(cur, t) == t
    assert resolve_label(cur, "lone topic") == t
    assert canonical_cluster_ids(cur, t) == [t]
    assert cluster_frequency(cur, t) == 7
    ensure_canonical_views(cur)
    rows = cur.execute(
        f"SELECT chunk_id, topic_id FROM {CHUNK_TOPICS_CANONICAL_VIEW}"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(c, t)]


# --------------------------------------------------------------------------- #
# Consumer: topic_search CLI (the recall-loss gap)
# --------------------------------------------------------------------------- #
def _patch_topic_search_db(monkeypatch, db):
    from src.cli import topic_search as ts
    db_path = db.db_path
    monkeypatch.setattr(ts, "get_db", lambda: Database(db_path))
    return ts


def test_keyword_search_finds_topic_by_alias_label(merged_db, monkeypatch):
    """Pre-fix behavior: `keyword` only matched canonical labels, so a topic
    merged under a different wording was unfindable by its alias's words."""
    db, ids = merged_db
    ts = _patch_topic_search_db(monkeypatch, db)

    runner = CliRunner()
    # "happy" appears ONLY in the alias label "the happy life"
    result = runner.invoke(ts.cli, ["keyword", "happy", "--format", "plain"])
    assert result.exit_code == 0, result.output
    # The CANONICAL topic is returned (not the alias row)
    assert "nature of happiness" in result.output
    assert "the happy life" not in result.output
    # Frequency shown is the cluster total (3+2+1), not the canonical row's 3
    assert "Frequency: 6" in result.output


def test_keyword_search_lists_cluster_once(merged_db, monkeypatch):
    """Both 'nature of happiness' and 'nature of true happiness' match
    'nature' - the merged cluster must appear exactly once."""
    db, ids = merged_db
    ts = _patch_topic_search_db(monkeypatch, db)

    runner = CliRunner()
    result = runner.invoke(ts.cli, ["keyword", "nature", "--format", "plain"])
    assert result.exit_code == 0, result.output
    assert result.output.count("nature of happiness") == 1
    assert "nature of true happiness" not in result.output
    assert "Frequency: 6" in result.output  # not doubled by the two matches


def test_similar_search_resolves_stale_alias_hits(merged_db, monkeypatch):
    """`similar` must survive a stale vec index that still returns alias ids:
    hits resolve to the canonical topic and duplicates collapse."""
    db, ids = merged_db
    ts = _patch_topic_search_db(monkeypatch, db)

    class FakeStore:
        def __init__(self, _path):
            pass

        def search_by_text(self, query, k, model):
            # Stale index: nearest hit is an ALIAS id, second hit is its
            # canonical - only ONE result (the canonical) should survive.
            return [
                (ids["alias1"], "nature of true happiness", 0.10),
                (ids["canon"], "nature of happiness", 0.20),
                (ids["other"], "gardening tips", 0.90),
            ]

    monkeypatch.setattr(ts, "VectorStore", FakeStore)
    monkeypatch.setattr(ts, "EmbeddingModel", lambda: None)

    runner = CliRunner()
    result = runner.invoke(ts.cli, ["similar", "happiness", "--format", "plain"])
    assert result.exit_code == 0, result.output
    assert result.output.count("nature of happiness") == 1
    assert "nature of true happiness" not in result.output
    assert "gardening tips" in result.output
    assert "Top 2 topics" in result.output  # 3 raw hits -> 2 after dedup


# --------------------------------------------------------------------------- #
# Consumer: ideation agent (book-span + freshness over the alias cluster)
# --------------------------------------------------------------------------- #
def _make_ideation_agent(monkeypatch, db, percentile: int = 50):
    from src.agent import ideation as ideation_mod
    from src.agent.ideation import IdeationAgent

    db_path = db.db_path
    monkeypatch.setattr(ideation_mod, "get_db", lambda: Database(db_path))
    # Deterministic: no trail corpus on disk -> every topic counts as fresh
    monkeypatch.setattr(ideation_mod, "build_existing_trail_corpus",
                        lambda _dir: {})

    agent = object.__new__(IdeationAgent)  # skip __init__ (needs an LLM key)
    agent.percentile = percentile
    agent.num_ideas = 5
    return agent


def test_get_novel_topics_counts_book_span_across_aliases(merged_db, monkeypatch):
    """The recall-loss gap: the merged idea spans 4 books, but 3 of those books
    are reachable only through ALIAS-tagged chunks. Pre-fix, book_span counted
    the canonical row's own chunks (1 book) and dropped the topic below
    MIN_BOOK_SPAN=4."""
    db, ids = merged_db
    cur = db.conn.cursor()
    # Novelty scores exist only for canonical topics (novelty_scoring
    # operates on canonical_id IS NULL rows)
    cur.execute("INSERT INTO novelty_scores (topic_id, mean_knn_distance, k_value) "
                "VALUES (?, 0.9, 10)", (ids["canon"],))
    cur.execute("INSERT INTO novelty_scores (topic_id, mean_knn_distance, k_value) "
                "VALUES (?, 0.1, 10)", (ids["other"],))
    db.conn.commit()

    agent = _make_ideation_agent(monkeypatch, db, percentile=0)
    topics = agent.get_novel_topics(limit=10)

    labels = [t["label"] for t in topics]
    # The merged topic IS surfaced: its cluster spans Books One-Four
    assert "nature of happiness" in labels
    # ...and the 1-book unmerged topic is (still, correctly) excluded
    assert "gardening tips" not in labels


def test_topic_has_fresh_chunk_looks_through_aliases(merged_db, monkeypatch):
    """Freshness must see chunks tagged under merged wordings. Give the
    canonical topic NO directly-tagged chunks: only the alias-linked ones
    can prove freshness."""
    db, ids = merged_db
    cur = db.conn.cursor()
    # Remove the canonical topic's own chunk link, leaving only alias links
    cur.execute("DELETE FROM chunk_topics WHERE topic_id = ?", (ids["canon"],))
    db.conn.commit()

    agent = _make_ideation_agent(monkeypatch, db)
    # Non-empty corpus that matches nothing -> every found chunk is fresh;
    # the question is purely whether any chunk is FOUND for the topic.
    corpus = {"some-trail.md": "completely unrelated trail text"}
    assert agent._topic_has_fresh_chunk(cur, ids["canon"], corpus) is True

    # Control: sever the alias links too and freshness correctly disappears
    cur.execute("DELETE FROM chunk_topics WHERE topic_id IN (?, ?)",
                (ids["alias1"], ids["alias2"]))
    db.conn.commit()
    assert agent._topic_has_fresh_chunk(cur, ids["canon"], corpus) is False


def test_get_novel_topics_after_real_canonicalize_pass(db, monkeypatch):
    """End-to-end through the real merge pass (not hand-set canonical_id):
    embeddings -> canonicalize_topics -> ideation sees the merged span."""
    cur = db.conn.cursor()
    base = _axis(0)
    canon = _add_topic(cur, "nature of happiness", base.tobytes(), frequency=3)
    alias = _add_topic(cur, "nature of true happiness", _emb(base, 0.95, 1),
                       frequency=1)
    far = _add_topic(cur, "gardening tips", _axis(5).tobytes(), frequency=1)

    # canon appears in books 1+2 directly; alias adds books 3+4
    for title, tid in [("B1", canon), ("B2", canon), ("B3", alias), ("B4", alias)]:
        _link(cur, _add_book_chunk(cur, title), tid)
    _link(cur, _add_book_chunk(cur, "B5"), far)
    db.conn.commit()

    canonicalize_topics(db, merge_threshold=0.90, knn=5)
    assert db.conn.execute(
        "SELECT canonical_id FROM topics WHERE id = ?", (alias,)
    ).fetchone()[0] == canon

    cur = db.conn.cursor()
    for tid, score in [(canon, 0.9), (far, 0.8)]:
        cur.execute("INSERT INTO novelty_scores (topic_id, mean_knn_distance, k_value) "
                    "VALUES (?, ?, 10)", (tid, score))
    db.conn.commit()

    agent = _make_ideation_agent(monkeypatch, db, percentile=0)
    topics = agent.get_novel_topics(limit=10)
    assert [t["label"] for t in topics] == ["nature of happiness"]
