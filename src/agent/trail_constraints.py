"""Trail Constraints - Quality gate that rejects structural failures before storage.

Applied between Polish and Save in the trail generation pipeline. Catches the
mechanical failure modes identified in the 96-trail critical analysis
(see data/trails/PATTERN-OBSERVATIONS.md):

  1. Single-author dominance (>45% from one book)
  2. Insufficient book diversity (<5 distinct books)
  3. Verbatim excerpt repetition within a trail
  4. Description-content mismatch (cited books missing from excerpts)
  5. Near-duplicate title against existing trails (Jaccard >0.5)
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ConstraintViolation:
    """Represents a single failed constraint check."""

    def __init__(self, name: str, message: str):
        self.name = name
        self.message = message

    def __repr__(self) -> str:
        return f"{self.name}: {self.message}"


def check_single_author_dominance(
    excerpts: List[Dict], max_share: float = 0.45
) -> Optional[ConstraintViolation]:
    """Reject if any single book contributes more than max_share of excerpts."""
    if not excerpts:
        return None
    book_counts: Dict[str, int] = {}
    for e in excerpts:
        book_counts[e["book_title"]] = book_counts.get(e["book_title"], 0) + 1
    total = len(excerpts)
    for book, count in book_counts.items():
        share = count / total
        if share > max_share:
            return ConstraintViolation(
                "single_author_dominance",
                f"'{book}' contributes {count}/{total} excerpts "
                f"({share:.0%} > {max_share:.0%} cap)",
            )
    return None


def check_minimum_book_diversity(
    excerpts: List[Dict], min_books: int = 4
) -> Optional[ConstraintViolation]:
    """Reject if fewer than min_books distinct books represented."""
    books = {e["book_title"] for e in excerpts}
    if len(books) < min_books:
        return ConstraintViolation(
            "insufficient_book_diversity",
            f"only {len(books)} distinct books (minimum {min_books})",
        )
    return None


def _normalize_excerpt_text(text: str) -> str:
    """Strip markdown bold/italic and collapse whitespace for repetition comparison."""
    # Strip ** and __ (bold) and * and _ (italic) markdown
    stripped = re.sub(r"[*_]+", "", text)
    return " ".join(stripped.split())


def dedupe_and_backfill_excerpts(
    excerpts: List[Dict],
    spare: Optional[List[Dict]] = None,
) -> Tuple[List[Dict], int]:
    """Drop verbatim-duplicate excerpts, optionally backfilling from spare candidates.

    The quality gate's ``check_excerpt_repetition`` rejects an entire trail when
    two excerpts share identical text (after stripping markdown highlights). That
    is too blunt: an accidental duplicate should be *removed*, not fatal (repro
    2026-07-01). This pre-pass, run between research and the gate, keeps the first
    occurrence of each distinct excerpt and — where ``spare`` candidates are
    provided — pulls in fresh, non-duplicate replacements to preserve the excerpt
    count. Sequence order is renumbered on the survivors.

    Returns ``(kept, removed_count)``. With no duplicates and no spare it returns
    the input unchanged (byte-for-byte), so the ``--idea`` path is unaffected in
    the normal case. Benefits ``--idea`` trails too (the dup-kill bug was general).
    """
    seen: set = set()
    kept: List[Dict] = []
    for e in excerpts:
        key = _normalize_excerpt_text(e.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append(e)

    removed = len(excerpts) - len(kept)

    for cand in spare or []:
        if len(kept) >= len(excerpts):
            break
        key = _normalize_excerpt_text(cand.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append(cand)

    for i, e in enumerate(kept, 1):
        e["sequence_order"] = i

    return kept, removed


def check_excerpt_repetition(excerpts: List[Dict]) -> Optional[ConstraintViolation]:
    """Reject if any two excerpts have identical text (ignoring markdown highlights)."""
    seen: Dict[str, int] = {}
    for e in excerpts:
        normalized = _normalize_excerpt_text(e["text"])
        if normalized in seen:
            return ConstraintViolation(
                "excerpt_repetition",
                f"excerpts {seen[normalized]} and {e['sequence_order']} "
                f"have identical text (after stripping markdown highlights)",
            )
        seen[normalized] = e["sequence_order"]
    return None


# Stopword set for tokenization and citation filtering
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "and", "or", "but", "for",
    "with", "as", "by", "from", "is", "are", "was", "were", "be", "been",
}

# Phrases that signal a parenthetical is descriptive, not a citation
_CITATION_SKIP_TOKENS = {
    "e.g.", "i.e.", "etc", "see ", "note", "for example", "such as", "across",
    "including", "from", "between",
}


def _tokenize(text: str) -> set:
    """Lowercase, drop stopwords and short tokens."""
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= 3}


def check_description_content_match(
    trail_idea: Dict, excerpts: List[Dict]
) -> Optional[ConstraintViolation]:
    """Reject if description's parenthetical citations name books missing from excerpts.

    Parenthetical content like "(Lencioni)" or "(Bad Blood, Midnight in Chernobyl)"
    is heuristically treated as a citation. Each token is checked against the
    union of excerpt book_titles and authors. Descriptive parentheticals
    (e.g., "(see chapter 3)") are filtered out.
    """
    description = (
        trail_idea.get("description", "") + " " + trail_idea.get("rationale", "")
    ).strip()

    paren_content = re.findall(r"\(([^)]+)\)", description)
    if not paren_content:
        return None

    actual_content = " ".join(
        e.get("book_title", "") + " " + e.get("author", "") for e in excerpts
    ).lower()

    missing: List[str] = []
    for citation in paren_content:
        # Skip descriptive parentheticals
        citation_lower = citation.lower()
        if any(skip in citation_lower for skip in _CITATION_SKIP_TOKENS):
            continue

        # Multi-citation (comma-separated)
        for token in citation.split(","):
            token_clean = token.strip()
            if len(token_clean) < 4:
                continue
            # Heuristic: only treat as citation if at least one Title-Case word.
            # Lowercase-only parentheticals are descriptive concepts ("citizenship",
            # "a manufacturing trust mechanism") not book/author references.
            has_proper_noun = any(
                w[:1].isupper() and not w.isupper()
                for w in token_clean.split()
                if len(w) >= 4
            )
            if not has_proper_noun:
                continue
            # Citation matches if any non-stopword word in it appears in book/author list
            words = [w for w in _tokenize(token_clean) if len(w) >= 4]
            if not words:
                continue
            if not any(w in actual_content for w in words):
                missing.append(token_clean)

    if missing:
        return ConstraintViolation(
            "description_content_mismatch",
            f"description cites {missing!r} but none appear in excerpt books/authors",
        )
    return None


# Files in the trails dir that are not themselves trails (skip when scanning)
_CORPUS_SKIP_PREFIXES = ("TRAIL-RATINGS", "PATTERN-OBSERVATIONS", "README",
                         "LIBRARY-BOUND", "CLEANUP")


def build_existing_trail_corpus(trails_dir: Path) -> Dict[str, str]:
    """Read all existing trail markdown files into {filename: normalized_text}.

    Normalizes once up front so callers can do cheap substring membership tests.
    Shared by the quality gate (check_excerpt_overuse) and the research agent's
    pre-filter, so both measure cross-trail reuse identically.
    """
    corpus: Dict[str, str] = {}
    if not trails_dir.exists():
        return corpus
    for md_file in trails_dir.glob("*.md"):
        if md_file.name.startswith(_CORPUS_SKIP_PREFIXES):
            continue
        try:
            corpus[md_file.name] = _normalize_excerpt_text(
                md_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError):
            continue
    return corpus


def count_excerpt_appearances(
    text: str,
    normalized_corpus: Dict[str, str],
    min_chars: int = 80,
) -> int:
    """Count how many existing trails an excerpt already appears in.

    Uses a stable substring near the middle of the normalized excerpt as the
    fingerprint (dodges boundary truncation from highlighting). Returns 0 for
    excerpts too short to fingerprint reliably (min_chars guards false positives).
    The `normalized_corpus` values must already be normalized (see
    build_existing_trail_corpus).
    """
    fingerprint = _normalize_excerpt_text(text)
    if len(fingerprint) < min_chars:
        return 0
    sample_start = max(0, len(fingerprint) // 2 - min_chars // 2)
    sample = fingerprint[sample_start : sample_start + min_chars]
    return sum(1 for content in normalized_corpus.values() if sample in content)


def check_excerpt_overuse(
    excerpts: List[Dict],
    trails_dir: Path,
    max_appearances: int = 2,
    min_chars: int = 80,
) -> Optional[ConstraintViolation]:
    """Reject if any excerpt already appears in more than max_appearances existing trails.

    Addresses the cross-trail overuse pattern identified in the cleanup analysis
    (e.g., the "andon cord" excerpt appearing in 7+ trails). Compares a fingerprint
    of each excerpt (a sufficiently long substring after normalization) against
    all existing trail markdown files.

    Note: min_chars protects against false positives from short shared phrases.
    """
    if not trails_dir.exists() or not excerpts:
        return None

    corpus = build_existing_trail_corpus(trails_dir)

    for excerpt in excerpts:
        appearances = count_excerpt_appearances(
            excerpt.get("text", ""), corpus, min_chars=min_chars
        )
        if appearances > max_appearances:
            book = excerpt.get("book_title", "<unknown>")[:50]
            return ConstraintViolation(
                "excerpt_overused_across_trails",
                f"excerpt from '{book}' already appears in {appearances} "
                f"existing trails (cap {max_appearances})",
            )
    return None


def check_title_uniqueness(
    trail_idea: Dict, existing_titles: List[str], threshold: float = 0.4
) -> Optional[ConstraintViolation]:
    """Reject if Jaccard similarity to any existing title exceeds threshold."""
    new_tokens = _tokenize(trail_idea.get("title", ""))
    if not new_tokens:
        return None

    for existing in existing_titles:
        existing_tokens = _tokenize(existing)
        if not existing_tokens:
            continue
        intersect = new_tokens & existing_tokens
        union = new_tokens | existing_tokens
        if union:
            jaccard = len(intersect) / len(union)
            if jaccard > threshold:
                return ConstraintViolation(
                    "near_duplicate_title",
                    f"title shares {jaccard:.0%} tokens with existing trail "
                    f"'{existing}' (cap {threshold:.0%})",
                )
    return None


def get_existing_trail_titles(trails_dir: Path) -> List[str]:
    """Read existing trail markdown files and extract their titles (line 1, # Title)."""
    if not trails_dir.exists():
        return []
    titles: List[str] = []
    skip_prefixes = ("TRAIL-RATINGS", "PATTERN-OBSERVATIONS", "README")
    for md_file in trails_dir.glob("*.md"):
        if md_file.name.startswith(skip_prefixes):
            continue
        try:
            first_line = md_file.read_text(encoding="utf-8").split("\n", 1)[0]
            if first_line.startswith("# "):
                titles.append(first_line[2:].strip())
        except (OSError, UnicodeDecodeError):
            continue
    return titles


def validate_trail(
    trail_idea: Dict,
    excerpts: List[Dict],
    trails_dir: Optional[Path] = None,
    *,
    check_description: bool = False,
) -> Tuple[bool, List[ConstraintViolation]]:
    """Run constraint checks. Returns (passed, list-of-violations).

    Default checks (mechanical, low false-positive):
      - single_author_dominance (≤45% from one book)
      - minimum_book_diversity (≥4 distinct books)
      - excerpt_repetition (no verbatim duplicates within trail)
      - excerpt_overused_across_trails (if trails_dir provided; ≤2 prior appearances)
      - near_duplicate_title (if trails_dir provided; Jaccard ≤40%)

    Opt-in check (heuristic, higher false-positive rate):
      - description_content_match: pass `check_description=True` to enable.
        Catches descriptions that name specific books missing from excerpts,
        but can over-trigger on concept references like "(Nansen passports)".
    """
    violations: List[ConstraintViolation] = []

    for check_fn, args in [
        (check_single_author_dominance, (excerpts,)),
        (check_minimum_book_diversity, (excerpts,)),
        (check_excerpt_repetition, (excerpts,)),
    ]:
        v = check_fn(*args)
        if v:
            violations.append(v)

    if check_description:
        v = check_description_content_match(trail_idea, excerpts)
        if v:
            violations.append(v)

    if trails_dir is not None:
        v = check_excerpt_overuse(excerpts, trails_dir)
        if v:
            violations.append(v)

        existing = get_existing_trail_titles(trails_dir)
        v = check_title_uniqueness(trail_idea, existing)
        if v:
            violations.append(v)

    return (len(violations) == 0, violations)


def format_violations(violations: List[ConstraintViolation]) -> str:
    """Format violations for CLI display."""
    if not violations:
        return "(no violations)"
    return "\n".join(f"  • {v.name}: {v.message}" for v in violations)
