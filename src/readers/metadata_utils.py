"""Book metadata normalization — front-end guard against the NULL-author defect.

Root cause (found 2026-07-01): 368/375 library books had `author = NULL`. The
ingestion pipeline stored `author = OPF dc:creator`, but many source EPUBs either
had no `dc:creator` (Project Gutenberg, theosophy scans, manual slug adds) or had
the author baked into the title (`"Title  Subtitle   Author"`), and nothing
recovered it.

Deeper root cause (found 2026-07-04): the OPF extraction itself was dead code —
selectolax CSS selectors can't match namespaced tags like <dc:creator>, so the
parser returned author=None for EVERY epub, even ones with clean metadata.
Fixed in epub_parser._parse_opf_metadata (tag-name traversal). This module
remains the guard for epubs whose metadata is genuinely absent or title-mangled.

`normalize_metadata()` is the single guard applied right before a book row is
inserted. It:
  1. recovers an author from a `"Title   Author"` (3+ space) trailing segment
     when the author field is empty — the exact split that cleaned the 286
     "Category A" rows in the backfill; then
  2. as a fallback, recovers an author from a `"Title - Author"` filename stem
     (how many readable EPUB files are named); then
  3. normalizes title whitespace (collapse runs of 2+ spaces).

It can only recover an author that is *present somewhere* in the title or
filename — a book with no author anywhere in its source (some Gutenberg texts)
still comes through with author=None, which is correct (nothing to invent).
"""

import re
from typing import Optional, Tuple

_TITLE_AUTHOR_SPLIT = re.compile(r"\s{3,}")   # 3+ spaces separates title from an appended author
_DEDUP_MARKER = re.compile(r"\s*\(\d+\)\s*$")  # trailing " (1)" import-collision marker


def _valid_author(cand: str) -> bool:
    """A recovered author segment must look like a name, not a stray title tail."""
    return bool(cand) and 2 <= len(cand) <= 80 and cand[0].isalpha()


def normalize_metadata(
    title: Optional[str],
    author: Optional[str],
    filename_stem: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Return a cleaned ``(title, author)`` pair. See module docstring for rules."""
    title = (title or "").strip()
    author = (author or "").strip() or None

    # (1) author embedded in the title after a 3+ space gap
    if not author and title:
        parts = _TITLE_AUTHOR_SPLIT.split(title)
        if len(parts) >= 2:
            cand = _DEDUP_MARKER.sub("", parts[-1]).strip()
            if _valid_author(cand):
                author = cand
                title = "   ".join(parts[:-1])

    # (2) fallback: "Title - Author" filename stem
    if not author and filename_stem and " - " in filename_stem:
        stem_title, _, stem_author = filename_stem.rpartition(" - ")
        cand = stem_author.strip()
        if _valid_author(cand) and stem_title.strip():
            author = cand

    # (3) normalize title whitespace
    title = re.sub(r"\s{2,}", " ", title).strip()

    return title, author
