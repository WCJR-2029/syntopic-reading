"""Excerpt repair — make retrieval chunks readable as excerpts (Phase 8.1, Fix 2).

Chunks are ~400-word retrieval windows cut at word-count boundaries: they start and end
mid-sentence and carry structural debris (bare page/footnote numbers, "Part Four" headers,
ALL-CAPS section titles). These helpers repair an excerpt's edges and strip line-level junk
so a human can enter it cold. Conservative by design: when in doubt, keep text.

Used by: anchored_retrieval (replaces the mid-word [:850] cut), research.py (the --idea
path), and scripts/backfill_readability.py (one-time repair of existing trails).
"""

import re

# A sentence terminator: . ! ? optionally followed by closing quotes/brackets.
_SENT_END = re.compile(r'[.!?…]["\'”’)\]]*')
# A plausible sentence opener: capital letter, opening quote/bracket, or dash.
_SENT_START = re.compile(r'["\'“‘(\[]?[A-Z0-9]')

# CAUTION: epub extraction isolates *italicized words* onto their own lines ("them",
# "soros"), so any "short line = junk" heuristic silently deletes words from quotes.
# Each branch below is therefore narrow and case-EXACT (an early IGNORECASE version
# of this matched every short letter-only line via the caps branch and corrupted
# quotes library-wide — caught 2026-07-04 in the readability backfill).
_JUNK_NUMBER = re.compile(r"^\d{1,4}$")                       # bare page/footnote number
_JUNK_ROMAN = re.compile(r"^(?:[ivxlcdm]{2,7}|[IVXLCDM]{2,7})$")  # page-numeral (2+ chars)
_JUNK_HEADER = re.compile(r"^(part|chapter|book|section|volume)\s+\w+([:.]\s*\S.*)?$",
                          re.IGNORECASE)
_JUNK_FIGURE = re.compile(r"^figure\s+[\d.]+.*$", re.IGNORECASE)


def _is_junk_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _JUNK_NUMBER.match(s) or _JUNK_ROMAN.match(s):
        return True
    if _JUNK_HEADER.match(s) or _JUNK_FIGURE.match(s):
        return True
    # True ALL-CAPS heading: no lowercase anywhere, a real word count, at least 3 chars.
    if len(s) >= 3 and s.isupper() and len(s.split()) <= 7:
        return True
    return False


def strip_junk_lines(text: str) -> str:
    """Drop structural-debris lines (page numbers, headers, figure captions)."""
    kept = [ln for ln in text.split("\n") if not _is_junk_line(ln)]
    out = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def trim_to_sentence_start(text: str, max_scan: int = 300) -> str:
    """If the text opens mid-sentence, drop up to the end of that partial sentence.

    Conservative: if the text already looks like a sentence start, or no terminator is
    found within `max_scan` chars, return unchanged.
    """
    t = text.lstrip()
    t = re.sub(r"^[\s.,;:—\-–]+", "", t)  # leading orphan punctuation
    if _SENT_START.match(t):
        return t
    m = _SENT_END.search(t, 0, max_scan)
    if not m:
        return t
    rest = t[m.end():].lstrip()
    return rest if rest and _SENT_START.match(rest) else t


def trim_to_sentence_end(text: str, min_keep: float = 0.5) -> str:
    """If the text ends mid-sentence, cut back to the last complete sentence.

    Conservative: only cut if a terminator exists past `min_keep` of the length —
    never throw away half the excerpt to win a clean edge.
    """
    t = text.rstrip()
    if t and _SENT_END.fullmatch(t[-1]) or re.search(r'[.!?…]["\'”’)\]]*$', t):
        return t
    last = None
    for m in _SENT_END.finditer(t):
        last = m
    if last and last.end() >= len(t) * min_keep:
        return t[:last.end()].rstrip()
    return t


def flatten_prose(text: str) -> str:
    """Collapse to one line and re-attach punctuation orphaned by the epub extraction's
    italic-word line isolation ('them\\n\\n. It' → 'them. It')."""
    t = " ".join(text.split())
    t = re.sub(r"\s+([.,;:!?…’”)\]])", r"\1", t)
    t = re.sub(r"([“‘(\[])\s+", r"\1", t)
    return t


def repair_excerpt(text: str) -> str:
    """Full repair: junk lines out, clean sentence edges. Idempotent."""
    return trim_to_sentence_end(trim_to_sentence_start(strip_junk_lines(text)))


def clean_for_reading(text: str, budget: int = 1100) -> str:
    """Flatten whitespace and fit within `budget` chars ending at a sentence boundary.

    Replaces the old hard `[:n]` cut in anchored retrieval: repairs first, then — if over
    budget — cuts at the last sentence end inside the budget (falling back to the hard cut
    only if the excerpt is one enormous sentence).
    """
    t = flatten_prose(repair_excerpt(text))
    if len(t) <= budget:
        return t
    head = t[:budget]
    last = None
    for m in _SENT_END.finditer(head):
        last = m
    if last and last.end() >= budget * 0.5:
        return head[:last.end()].rstrip()
    return head
