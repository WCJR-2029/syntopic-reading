"""Unit-style tests for trail_constraints.

Run: python -m src.agent.test_trail_constraints
"""

import tempfile
from pathlib import Path

from src.agent.trail_constraints import (
    check_single_author_dominance,
    check_minimum_book_diversity,
    check_excerpt_repetition,
    check_description_content_match,
    check_title_uniqueness,
    check_excerpt_overuse,
    validate_trail,
)


def _excerpt(seq, book, author, text):
    return {
        "sequence_order": seq,
        "book_title": book,
        "author": author,
        "text": text,
        "topics": [],
    }


def test_single_author_dominance_positive():
    """7 of 10 from one book → reject."""
    excerpts = [_excerpt(i, "Big Book", "X", f"text {i}") for i in range(1, 8)]
    excerpts += [_excerpt(i, f"Other {i}", "Y", f"t{i}") for i in range(8, 11)]
    v = check_single_author_dominance(excerpts)
    assert v is not None, "should reject 7/10 dominance"
    assert "70%" in v.message


def test_single_author_dominance_negative():
    """4 of 10 from one book → pass (under 45% cap)."""
    excerpts = [_excerpt(i, "Big Book", "X", f"t{i}") for i in range(1, 5)]
    excerpts += [_excerpt(i, f"Other {i}", "Y", f"t{i}") for i in range(5, 11)]
    v = check_single_author_dominance(excerpts)
    assert v is None, "4/10 should pass"


def test_minimum_book_diversity_positive():
    """3 distinct books → reject (min 5)."""
    excerpts = [_excerpt(i, f"Book {i % 3}", "X", f"t{i}") for i in range(1, 11)]
    v = check_minimum_book_diversity(excerpts)
    assert v is not None


def test_minimum_book_diversity_negative():
    """6 distinct books → pass."""
    excerpts = [_excerpt(i, f"Book {i % 6}", "X", f"t{i}") for i in range(1, 11)]
    v = check_minimum_book_diversity(excerpts)
    assert v is None


def test_excerpt_repetition_positive():
    """Same text in two excerpts → reject."""
    excerpts = [
        _excerpt(1, "A", "X", "Identical text here"),
        _excerpt(2, "B", "Y", "Identical text here"),
        _excerpt(3, "C", "Z", "Different text"),
    ]
    v = check_excerpt_repetition(excerpts)
    assert v is not None


def test_excerpt_repetition_negative():
    """All distinct → pass."""
    excerpts = [_excerpt(i, f"B{i}", "X", f"unique {i}") for i in range(1, 4)]
    v = check_excerpt_repetition(excerpts)
    assert v is None


def test_description_content_match_positive():
    """Description cites Pachinko but excerpts have no Pachinko → reject."""
    trail_idea = {
        "description": "Three continents (Pachinko, Native Son, Expendable Man)",
        "rationale": "the reviewer's request",
        "title": "Test",
    }
    excerpts = [
        _excerpt(1, "Some Other Book", "Author A", "text"),
        _excerpt(2, "Another One", "Author B", "text"),
    ]
    v = check_description_content_match(trail_idea, excerpts)
    assert v is not None
    assert "pachinko" in v.message.lower() or "native son" in v.message.lower()


def test_description_content_match_negative():
    """Description cites Lencioni and excerpts have Lencioni → pass."""
    trail_idea = {
        "description": "Team dynamics (Lencioni)",
        "rationale": "",
        "title": "Test",
    }
    excerpts = [_excerpt(1, "The Advantage", "Patrick Lencioni", "text")]
    v = check_description_content_match(trail_idea, excerpts)
    assert v is None


def test_description_no_citations():
    """No parentheticals → pass (nothing to verify)."""
    trail_idea = {"description": "Plain description.", "rationale": "", "title": "T"}
    excerpts = [_excerpt(1, "B", "X", "t")]
    v = check_description_content_match(trail_idea, excerpts)
    assert v is None


def test_excerpt_overuse_positive():
    """Excerpt appearing in 3+ existing trails → reject (cap=2)."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        long_text = "This is a unique passage about Toyota's andon cord and how the warp string breaks when production halts. The line stops automatically until a person fixes the thread."
        # Simulate 3 existing trails containing the same excerpt
        for i in range(3):
            (td_path / f"trail_{i}.md").write_text(f"# Title {i}\n\n> {long_text}\n")
        # New trail with one excerpt matching
        excerpts = [_excerpt(1, "Book A", "X", long_text)]
        v = check_excerpt_overuse(excerpts, td_path, max_appearances=2)
        assert v is not None, "should reject when excerpt appears 3+ times"
        assert "andon" in v.message.lower() or "book a" in v.message.lower()


def test_excerpt_overuse_negative():
    """Excerpt appearing in 1 existing trail → pass (cap=2)."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        long_text = "A different passage about the structural properties of cognitive architectures and how belief systems propagate through hierarchical organizations over time."
        (td_path / "trail_1.md").write_text(f"# X\n\n> {long_text}\n")
        excerpts = [_excerpt(1, "B", "Y", long_text)]
        v = check_excerpt_overuse(excerpts, td_path, max_appearances=2)
        assert v is None


def test_excerpt_overuse_short_excerpt_skipped():
    """Very short excerpts (< min_chars) skip the check — avoid false positives."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        short = "Short shared phrase"
        for i in range(5):
            (td_path / f"trail_{i}.md").write_text(f"# X\n\n> {short}\n")
        excerpts = [_excerpt(1, "B", "Y", short)]
        v = check_excerpt_overuse(excerpts, td_path, max_appearances=2)
        assert v is None


def test_title_uniqueness_positive():
    """Title shares many tokens with existing → reject."""
    trail_idea = {"title": "The Andon Cord Principle: Systems Learn to Scream"}
    existing = ["The Andon Cord Principle: Systems Empower Lowest Rung"]
    v = check_title_uniqueness(trail_idea, existing, threshold=0.4)
    assert v is not None


def test_title_uniqueness_negative():
    """Distinct titles → pass."""
    trail_idea = {"title": "The Geometry of Truth"}
    existing = ["The Scaffolding of the Self", "The Ledger at Dusk"]
    v = check_title_uniqueness(trail_idea, existing)
    assert v is None


def test_validate_trail_aggregates_violations():
    """Multiple violations are returned together."""
    trail_idea = {
        "title": "Test",
        "description": "Cites (Bad Blood, Midnight in Chernobyl)",
        "rationale": "",
    }
    # 7/10 from one book + only 2 distinct books + cited books not in excerpts
    excerpts = [_excerpt(i, "Big Book", "X", f"t{i}") for i in range(1, 8)]
    excerpts += [_excerpt(i, "Other Book", "Y", f"t{i}") for i in range(8, 11)]
    passed, violations = validate_trail(
        trail_idea, excerpts, trails_dir=None, check_description=True
    )
    assert not passed
    violation_names = {v.name for v in violations}
    assert "single_author_dominance" in violation_names
    assert "insufficient_book_diversity" in violation_names
    assert "description_content_mismatch" in violation_names


TESTS = [
    test_single_author_dominance_positive,
    test_single_author_dominance_negative,
    test_minimum_book_diversity_positive,
    test_minimum_book_diversity_negative,
    test_excerpt_repetition_positive,
    test_excerpt_repetition_negative,
    test_description_content_match_positive,
    test_description_content_match_negative,
    test_description_no_citations,
    test_title_uniqueness_positive,
    test_title_uniqueness_negative,
    test_excerpt_overuse_positive,
    test_excerpt_overuse_negative,
    test_excerpt_overuse_short_excerpt_skipped,
    test_validate_trail_aggregates_violations,
]


def main():
    passed = 0
    failed = 0
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
