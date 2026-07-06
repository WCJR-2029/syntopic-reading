"""Tests for the book-metadata normalization guard.

Run standalone: python -m src.readers.test_metadata_utils
Run via pytest:  pytest src/readers/test_metadata_utils.py
"""

from src.readers.metadata_utils import normalize_metadata


def test_recovers_author_from_embedded_title():
    t, a = normalize_metadata(
        "Why Nations Fail  The Origins of Power   Daron Acemoglu & James A. Robinson", None)
    assert a == "Daron Acemoglu & James A. Robinson"
    assert t == "Why Nations Fail The Origins of Power"  # whitespace collapsed


def test_strips_dedup_marker_from_recovered_author():
    t, a = normalize_metadata("Advantage, The   Patrick M. Lencioni (1)", None)
    assert a == "Patrick M. Lencioni"
    assert t == "Advantage, The"


def test_recovers_author_from_filename_stem():
    t, a = normalize_metadata("Art of Thinking Clearly, The", None,
                              filename_stem="Art of Thinking Clearly, The - Rolf Dobelli")
    assert a == "Rolf Dobelli"
    assert t == "Art of Thinking Clearly, The"


def test_existing_author_is_preserved():
    t, a = normalize_metadata("The Case Against Reality", "Donald Hoffman",
                              filename_stem="whatever - Someone Else")
    assert a == "Donald Hoffman"  # never overwrite a real author
    assert t == "The Case Against Reality"


def test_no_author_anywhere_stays_none():
    # Genuinely authorless source (some Gutenberg texts) — nothing to invent.
    t, a = normalize_metadata("Win Every Argument", None)
    assert a is None
    assert t == "Win Every Argument"


def test_does_not_split_on_normal_double_space():
    # Two spaces (subtitle sep), not three — must NOT be treated as an author gap.
    t, a = normalize_metadata("Enlightenment Now  The Case for Reason", None)
    assert a is None
    assert t == "Enlightenment Now The Case for Reason"


def test_rejects_implausible_author_tail():
    # A trailing numeric/short tail is not a name → don't misfile it as author.
    t, a = normalize_metadata("Some Title   1994", None)
    assert a is None


TESTS = [
    test_recovers_author_from_embedded_title,
    test_strips_dedup_marker_from_recovered_author,
    test_recovers_author_from_filename_stem,
    test_existing_author_is_preserved,
    test_no_author_anywhere_stays_none,
    test_does_not_split_on_normal_double_space,
    test_rejects_implausible_author_tail,
]


def main():
    passed = failed = 0
    for t in TESTS:
        try:
            t(); print(f"  ✓ {t.__name__}"); passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}"); failed += 1
    print(f"\n{passed}/{passed + failed} tests passed")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
