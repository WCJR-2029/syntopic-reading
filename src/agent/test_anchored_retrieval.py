"""Tests for Phase 7 anchored/steered retrieval + the dup-excerpt gate fix.

Run standalone:   python -m src.agent.test_anchored_retrieval
Run via pytest:   pytest src/agent/test_anchored_retrieval.py

Unit tests for the back-matter filter and the dedupe/backfill fix are pure and
always run. Tests that touch the live vocabulary/DB (centroid, seed resolution,
bridge union, integration smoke) skip cleanly if the database is unavailable, so
the suite is green on any checkout.
"""

import numpy as np

from src.agent import anchored_retrieval as ar
from src.agent.trail_constraints import (
    dedupe_and_backfill_excerpts,
    check_excerpt_repetition,
    validate_trail,
)

# The anchor book used across the DB-backed tests (present in the live library).
ANCHOR = "The Case Against Reality"
# A verified cross-book bridge for that anchor (spans ~10 books).
KNOWN_BRIDGE = "nature of reality"


def _db():
    """Return a cursor to the live DB, or None if it can't be opened/seeded."""
    try:
        conn = ar.connect()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM topics WHERE embedding IS NOT NULL LIMIT 1")
        if cur.fetchone() is None:
            return None
        return cur
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# T1 — centroid build
# --------------------------------------------------------------------------- #
def test_build_centroid_normalized_and_missing():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    centroid, missing = ar.build_centroid(cur, [KNOWN_BRIDGE, "zzz_not_a_real_topic_xyz"])
    assert centroid is not None, "real label should yield a centroid"
    assert abs(float(np.linalg.norm(centroid)) - 1.0) < 1e-4, "centroid must be L2-normalized"
    assert "zzz_not_a_real_topic_xyz" in missing, "missing label should be reported"


def test_build_centroid_all_missing_returns_none():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    centroid, missing = ar.build_centroid(cur, ["zzz_nope_1", "zzz_nope_2"])
    assert centroid is None, "no resolvable labels → None centroid"
    assert len(missing) == 2


# --------------------------------------------------------------------------- #
# T2 — back-matter filter (pure, no DB)
# --------------------------------------------------------------------------- #
def test_back_matter_index_page_flagged():
    index_text = ("Sammaknejad, Negar, 29, 30, 33, 34, 35 Schack, Rüdiger, 10, 12 "
                  "Vision (Marr), xiv, 45, 49, 167 visual attention, 33, 45")
    assert ar.is_back_matter(index_text, chunk_index=162, total_chunks=164) is True


def test_back_matter_copyright_page_flagged():
    copyright_text = ("Copyright © 2019 by the author. All rights reserved. No part of this "
                      "book may be reproduced. ISBN 978-0-000-00000-0. Printed in the "
                      "United States of America. First edition.")
    assert ar.is_back_matter(copyright_text, chunk_index=1, total_chunks=200) is True


def test_back_matter_bibliography_header_flagged():
    biblio = "Bibliography\n\nHoffman, D. (2019). The Case Against Reality. Norton."
    assert ar.is_back_matter(biblio, chunk_index=160, total_chunks=164) is True


def test_back_matter_body_prose_retained():
    body = ("Natural selection does not favor veridical perceptions. It favors perceptions "
            "that guide adaptive behavior, and those need not resemble the objective world "
            "at all. A creature tuned to fitness will see fitness, not truth.")
    assert ar.is_back_matter(body, chunk_index=60, total_chunks=164) is False


def test_back_matter_late_body_without_numbers_retained():
    # Late in the book but ordinary prose (no page-number density) → keep.
    body = ("In the final analysis, the interface metaphor reframes the whole debate about "
            "what we can know. We are not seeing reality; we are managing it.")
    assert ar.is_back_matter(body, chunk_index=158, total_chunks=164) is False


# --------------------------------------------------------------------------- #
# T3 — seed resolution
# --------------------------------------------------------------------------- #
def test_resolve_seeds_explicit_skips_missing():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    kept, missing = ar.resolve_seeds(cur, [KNOWN_BRIDGE, "zzz_not_a_real_topic_xyz"], None)
    assert KNOWN_BRIDGE in kept
    assert "zzz_not_a_real_topic_xyz" in missing


def test_resolve_seeds_auto_derive_from_book():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    abid, _, _ = ar.book_lookup(cur, ANCHOR)
    assert abid is not None
    kept, missing = ar.resolve_seeds(cur, None, abid, top_n=6)
    assert len(kept) >= 1, "bare --anchor-book should auto-derive seeds"
    # every derived seed must resolve to an embedding
    centroid, _ = ar.build_centroid(cur, kept)
    assert centroid is not None


# --------------------------------------------------------------------------- #
# T4 — shared-topic bridge union
# --------------------------------------------------------------------------- #
def test_shared_topic_bridges_include_known_bridge():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    abid, _, _ = ar.book_lookup(cur, ANCHOR)
    bridges = ar.shared_topic_bridges(cur, abid)
    labels = {label for _, label in bridges}
    assert KNOWN_BRIDGE in labels, f"{KNOWN_BRIDGE!r} should surface as a bridge"


# --------------------------------------------------------------------------- #
# T6 — duplicate-excerpt gate fix
# --------------------------------------------------------------------------- #
def _ex(seq, book, text):
    return {"sequence_order": seq, "book_title": book, "author": "A",
            "text": text, "topics": []}


def test_dedupe_removes_identical_excerpt():
    dup = "The very same words repeated across two excerpts, long enough to fingerprint."
    excerpts = [_ex(1, "Book A", dup), _ex(2, "Book B", dup), _ex(3, "Book C", "different text here")]
    kept, removed = dedupe_and_backfill_excerpts(excerpts)
    assert removed == 1
    assert len(kept) == 2
    # the survivors must no longer trip the repetition gate (the false-kill)
    assert check_excerpt_repetition(kept) is None


def test_dedupe_backfills_from_spare():
    dup = "Identical passage that appears twice and must be collapsed to one."
    excerpts = [_ex(1, "Book A", dup), _ex(2, "Book B", dup)]
    spare = [_ex(9, "Book C", "a genuinely fresh replacement passage for backfill")]
    kept, removed = dedupe_and_backfill_excerpts(excerpts, spare)
    assert removed == 1
    assert len(kept) == 2, "backfill should restore the count"
    assert {e["book_title"] for e in kept} == {"Book A", "Book C"}
    # sequence renumbered contiguously
    assert [e["sequence_order"] for e in kept] == [1, 2]


def test_dedupe_noop_when_unique():
    excerpts = [_ex(1, "Book A", "alpha passage one"), _ex(2, "Book B", "beta passage two")]
    kept, removed = dedupe_and_backfill_excerpts(excerpts)
    assert removed == 0
    assert kept == excerpts


# --------------------------------------------------------------------------- #
# T8 — integration smoke: full anchored retrieval assembles a gate-passing trail
# --------------------------------------------------------------------------- #
def test_integration_anchored_retrieval_passes_gate():
    cur = _db()
    if cur is None:
        print("    (skipped: no DB)")
        return
    pack = ar.retrieve_anchored(cur, anchor_book=ANCHOR, k=6, per_book=1)

    # (a) anchor book present
    assert pack["anchor_book"] == ANCHOR
    assert len(pack["anchors"]) >= 1, "at least one anchor excerpt required"
    assert all(a["book_id"] == pack["anchor_book_id"] for a in pack["anchors"])

    # assemble research-style excerpts (anchors + companions), as the generator does
    excerpts = []
    seq = 1
    for e in pack["anchors"] + pack["companions"]:
        excerpts.append({
            "sequence_order": seq, "book_title": e["book_title"], "author": e["author"],
            "text": e["text"], "topics": e["topics"], "chunk_id": e["chunk_id"],
        })
        seq += 1

    # (b) no companion is back-matter (index/biblio pages)
    for c in pack["companions"]:
        assert not ar.is_back_matter(
            c["text"], c["chunk_index"], ar._book_total_chunks(cur, c["book_id"])
        ), f"companion from {c['book_title']} is back-matter"

    # (c) the assembled trail passes the stock quality gate
    passed, violations = validate_trail({"title": "Anchored smoke test",
                                         "description": "", "rationale": ""}, excerpts)
    assert passed, f"assembled anchored trail should pass the gate: {violations}"


# --------------------------------------------------------------------------- #
# Standalone runner (mirrors the other test modules' convention)
# --------------------------------------------------------------------------- #
TESTS = [
    test_build_centroid_normalized_and_missing,
    test_build_centroid_all_missing_returns_none,
    test_back_matter_index_page_flagged,
    test_back_matter_copyright_page_flagged,
    test_back_matter_bibliography_header_flagged,
    test_back_matter_body_prose_retained,
    test_back_matter_late_body_without_numbers_retained,
    test_resolve_seeds_explicit_skips_missing,
    test_resolve_seeds_auto_derive_from_book,
    test_shared_topic_bridges_include_known_bridge,
    test_dedupe_removes_identical_excerpt,
    test_dedupe_backfills_from_spare,
    test_dedupe_noop_when_unique,
    test_integration_anchored_retrieval_passes_gate,
]


def main():
    passed = failed = 0
    for test in TESTS:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {test.__name__} (unexpected): {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} tests passed")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
