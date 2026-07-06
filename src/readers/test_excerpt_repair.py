"""Tests for excerpt repair (Phase 8.1 Fix 2) — real failure shapes from the library."""

from src.readers.excerpt_repair import (
    strip_junk_lines, trim_to_sentence_start, trim_to_sentence_end,
    repair_excerpt, clean_for_reading,
)


def test_trim_midsentence_start_real_shape():
    # The True Believer opener: drops into the middle of a James sentence.
    t = ("the sin ranges from drunkenness to spiritual pride, to show that the sense "
         "of it may beset one. A good one to use as an example is that of Mr. Hadley.")
    out = trim_to_sentence_start(t)
    assert out.startswith("A good one to use")


def test_clean_start_untouched():
    t = "General relativity and quantum mechanics are in the end not incompatible."
    assert trim_to_sentence_start(t) == t
    # opening quotes count as sentence starts
    assert trim_to_sentence_start('“One Tuesday evening I sat in a saloon.”').startswith("“One")


def test_no_terminator_in_scan_keeps_text():
    t = "a very long clause without any terminator " * 12
    assert trim_to_sentence_start(t).strip() == t.strip()


def test_trim_midsentence_end():
    # Terminator past the halfway mark → cut the dangling tail.
    t = ("The time has come to attempt some conclusions about what quantum mechanics "
         "tells us. The infinities disappear. And then it trails off into the nature of t")
    out = trim_to_sentence_end(t)
    assert out.endswith("The infinities disappear.")


def test_end_cut_never_discards_majority():
    # Only terminator is at 3% of the text → conservative rule keeps the tail.
    t = "One short. " + "then an endless unterminated clause " * 30
    assert trim_to_sentence_end(t) == t.rstrip()


def test_strip_junk_lines_real_shape():
    # Rovelli chunk tail: footnote number + part header + ALL-CAPS title.
    t = "But, howsoever, strange and admirable.\n\n2\n\nPart Four\nBEYOND SPACE AND TIME"
    assert strip_junk_lines(t) == "But, howsoever, strange and admirable."


def test_junk_does_not_eat_prose():
    t = "I said NO. That was final.\nHe wrote Chapter Eleven of his memoir that night."
    assert strip_junk_lines(t) == t  # caps inside prose + 'Chapter' mid-sentence survive


def test_junk_never_eats_italic_isolated_words():
    # epub extraction isolates italicized words onto their own lines - these are QUOTE
    # CONTENT, not junk. Regression for the IGNORECASE corruption bug (2026-07-04).
    t = ("so the self is neither a bundle of faculties, nor something distinct underlying\n\n"
         "them\n\n. The sorites paradox\n\n(from Greek\n\nsoros\n\n, a heap) begins here.")
    out = strip_junk_lines(t)
    for word in ("them", "soros", "The sorites paradox"):
        assert word in out, f"{word!r} was eaten"


def test_junk_single_letter_and_pronoun_survive():
    t = "He said\n\nI\n\nwould go, circa\n\nc\n\n. 350 BC."
    out = strip_junk_lines(t)
    assert "I" in out.split() and "c" in out.split()  # 1-char lines are never junk now


def test_repair_idempotent():
    t = ("tail of a broken thought. The real sentence lives here, complete and whole. "
         "And then it trails off into ano")
    once = repair_excerpt(t)
    assert once == repair_excerpt(once)
    assert once == "The real sentence lives here, complete and whole."


def test_clean_for_reading_budget_cuts_at_sentence():
    sent = "This is a complete sentence of moderate length that repeats. "
    t = sent * 40
    out = clean_for_reading(t, budget=850)
    assert len(out) <= 850
    assert out.endswith("repeats.")  # sentence boundary, not mid-word


def test_clean_for_reading_short_text_untouched():
    t = "Short and complete."
    assert clean_for_reading(t) == t
