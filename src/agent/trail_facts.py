"""Deterministic trail facts + flags (T3).

Pure, no-LLM structural signals fed to the judge (so it weighs but does not re-derive them)
and surfaced as the dashboard's Flags column. Reuses trail_constraints.py for the mechanical
checks and adds the AUTHOR-level dominance fact the red-team flagged as the #1 failure predictor
(book-level share alone misses one author spread across several of their own books).

facts dict shape (consumed by trail_quality_rubric.build_judge_prompt):
  book_count, excerpt_count, top_book, top_book_share, top_author, top_author_share, flags[list[str]]
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from src.agent.trail_parser import TrailDoc
from src.agent.trail_constraints import (
    check_single_author_dominance,
    check_minimum_book_diversity,
    check_excerpt_repetition,
    check_description_content_match,
    check_excerpt_overuse,
)

# The book_title format is "<Title>   <Author>" — 3+ spaces delimit the trailing author.
_AUTHOR_SPLIT_RE = re.compile(r"\s{3,}")


def resolve_author(book_title: str) -> str:
    """Extract the author from the embedded book_title string.

    'Origins of Totalitarianism, The   Hannah Arendt' -> 'Hannah Arendt'
    Falls back to the whole title if no 3-space delimiter is present (keeps grouping stable).
    """
    parts = _AUTHOR_SPLIT_RE.split(book_title.strip())
    if len(parts) >= 2 and parts[-1].strip():
        return parts[-1].strip()
    return book_title.strip()


def _surname(resolved: str) -> str:
    """Last alpha token (len>=4) of a resolved author, lowercased — the grouping key."""
    toks = [t for t in re.findall(r"[A-Za-z]+", resolved.lower()) if len(t) >= 4]
    return toks[-1] if toks else resolved.lower()


def author_counts(book_items: List) -> Dict[str, int]:
    """Group {book_title: excerpt_count} into {canonical_author: total_count}.

    Resolves each title's author, then CONSOLIDATES by surname so a malformed title
    ('emotional intelligence goleman', no delimiter) groups with well-formed ones
    ('... Daniel Goleman') — otherwise the #1 failure predictor (author dominance)
    undercounts exactly the worst offenders. Canonical label prefers a delimiter-parsed
    ("strong") name. Heuristic limitation: two distinct authors sharing a surname within
    one trail would merge (rare; biases toward flagging, the conservative direction).
    """
    groups: Dict[str, Dict] = {}
    for title, cnt in book_items:
        resolved = resolve_author(title)
        strong = bool(_AUTHOR_SPLIT_RE.search(title.strip()))
        key = _surname(resolved)
        g = groups.setdefault(key, {"count": 0, "labels_strong": [], "labels_weak": []})
        g["count"] += cnt
        (g["labels_strong"] if strong else g["labels_weak"]).append(resolved)
    out: Dict[str, int] = {}
    for g in groups.values():
        label = max(g["labels_strong"], key=len) if g["labels_strong"] else max(g["labels_weak"], key=len)
        out[label] = out.get(label, 0) + g["count"]
    return out


def _share(counts: Dict[str, int]) -> tuple[Optional[str], float, int]:
    """Return (top_key, top_share, total) for a {key: count} map."""
    total = sum(counts.values())
    if total == 0:
        return None, 0.0, 0
    top_key = max(counts, key=counts.get)
    return top_key, counts[top_key] / total, total


def compute_facts(doc: TrailDoc, trails_dir: Optional[Path] = None) -> Dict:
    """Compute structural facts + flags for a parsed trail."""
    excerpts = doc.excerpts

    # Per-book and per-author excerpt counts (prefer the explicit Books-in-Trail counts when present,
    # else fall back to counting parsed excerpts).
    book_counts: Dict[str, int] = {}
    if doc.books:
        book_items = [(b["title"], b["excerpt_count"]) for b in doc.books]
    else:
        book_items = [(e["book_title"], 1) for e in excerpts]
    for title, cnt in book_items:
        book_counts[title] = book_counts.get(title, 0) + cnt

    auth_counts = author_counts(book_items)
    top_book, top_book_share, total_excerpts = _share(book_counts)
    top_author, top_author_share, _ = _share(auth_counts)

    facts: Dict = {
        "book_count": len(book_counts),
        "excerpt_count": total_excerpts or len(excerpts),
        "top_book": top_book,
        "top_book_share": round(top_book_share, 3),
        "top_author": top_author,
        "top_author_share": round(top_author_share, 3),
        "flags": _compute_flags(doc, top_author, top_author_share, trails_dir),
    }
    return facts


def _compute_flags(
    doc: TrailDoc,
    top_author: Optional[str],
    top_author_share: float,
    trails_dir: Optional[Path],
) -> List[str]:
    """Mechanical flags. The two HARD signals (within-trail verbatim repeat, description-content
    mismatch) are tagged HARD so the engine (T4) can apply the composite<16 cap."""
    flags: List[str] = []
    excerpts = doc.excerpts

    # Book-level single-source dominance (existing check, keyed on book_title).
    v = check_single_author_dominance(excerpts)
    if v:
        flags.append(f"book_dominance: {v.message}")

    # Author-level dominance — the documented #1 failure predictor (A3).
    if top_author and top_author_share > 0.45:
        flags.append(
            f"author_dominance: '{top_author}' = {top_author_share:.0%} of excerpts "
            f"(>45% warning; >50% strong low-score predictor)"
        )

    v = check_minimum_book_diversity(excerpts)
    if v:
        flags.append(f"low_book_diversity: {v.message}")

    # HARD: within-trail verbatim repetition.
    v = check_excerpt_repetition(excerpts)
    if v:
        flags.append(f"HARD verbatim_repetition: {v.message}")

    # HARD: description cites books absent from excerpts (reverse-engineering tell).
    v = check_description_content_match(doc.trail_idea, excerpts)
    if v:
        flags.append(f"HARD description_mismatch: {v.message}")

    # Cross-trail overuse (optional; needs the corpus dir).
    if trails_dir is not None:
        v = check_excerpt_overuse(excerpts, Path(trails_dir))
        if v:
            flags.append(f"cross_trail_overuse: {v.message}")

    return flags


def has_hard_flag(facts: Dict) -> bool:
    """True if any HARD structural flag is present (triggers the composite<16 cap in T4)."""
    return any(f.startswith("HARD ") for f in facts.get("flags", []))
