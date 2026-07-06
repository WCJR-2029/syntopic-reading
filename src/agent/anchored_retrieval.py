"""Anchored / steered trail retrieval core (Phase 7).

Single source of truth for the "steer a trail toward a specific book or
cross-book bridge" retrieval procedure. Imported by BOTH:

  * ``src/agent/trail_generator.py`` (the ``--anchor-book`` generator path), and
  * ``scripts/anchored_trail.py``   (the thin standalone / JSON-pack wrapper).

The procedure (ADR-005):

    require anchor chunks                     (the book/excerpts that MUST appear)
      -> embedding-centroid over topic vectors      (companions surface library-wide)
      -> union with verified shared-topic bridges    (named cross-book links)
      -> assemble anchor + companions (back-matter filtered)

Embedding facts this relies on (verified 2026-07-01):
  * ``topics.embedding`` is a 384-dim MiniLM vector, already L2-normalized, so
    cosine similarity == dot product (no re-normalization of stored vectors).
  * All ~225k topics are embedded; no model load and no ``vec0`` extension needed
    (the sqlite-vec virtual table fails to load in this DB — do not rely on it).
  * Topics dedupe by exact label (``canonical_id`` is NULL), so a shared label
    across two books *is* the bridge; ``topic_id`` is the shared key.

Every retrieval function takes a sqlite ``cur`` (cursor) so the module is
connection-agnostic and unit-testable; ``connect()`` is a convenience that opens
the configured live DB.
"""

import re
import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np

DTYPE = np.float32


# --------------------------------------------------------------------------- #
# Connection helper
# --------------------------------------------------------------------------- #
def connect() -> sqlite3.Connection:
    """Open a connection to the configured live database."""
    from src.config import DATABASE_PATH

    return sqlite3.connect(str(DATABASE_PATH))


# --------------------------------------------------------------------------- #
# Vector helpers
# --------------------------------------------------------------------------- #
def _norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def topic_vector(cur, label: str) -> Optional[np.ndarray]:
    """Return the stored (already-normalized) embedding for an exact topic label."""
    r = cur.execute(
        "SELECT embedding FROM topics WHERE label = ? AND embedding IS NOT NULL LIMIT 1",
        (label,),
    ).fetchone()
    return np.frombuffer(r[0], dtype=DTYPE) if r else None


def build_centroid(cur, labels: List[str]) -> Tuple[Optional[np.ndarray], List[str]]:
    """Mean of the labels' topic vectors, L2-normalized.

    Returns ``(centroid, missing)`` where ``missing`` lists labels that have no
    embedding (skipped). ``centroid`` is ``None`` only if none of the labels
    resolve to a vector.
    """
    vecs: List[np.ndarray] = []
    missing: List[str] = []
    for label in labels:
        v = topic_vector(cur, label)
        if v is not None:
            vecs.append(v)
        else:
            missing.append(label)
    if not vecs:
        return None, missing
    return _norm(np.mean(vecs, axis=0)), missing


def _chunk_topic_centroid(cur, chunk_id: int) -> Optional[np.ndarray]:
    """Normalized mean of the topic vectors attached to a chunk."""
    rs = cur.execute(
        """SELECT t.embedding FROM chunk_topics ct JOIN topics t ON t.id = ct.topic_id
           WHERE ct.chunk_id = ? AND t.embedding IS NOT NULL""",
        (chunk_id,),
    ).fetchall()
    if not rs:
        return None
    return _norm(np.mean([np.frombuffer(r[0], dtype=DTYPE) for r in rs], axis=0))


# --------------------------------------------------------------------------- #
# Back-matter / non-body filter (T2)
# --------------------------------------------------------------------------- #
# Strong markers of a copyright / publication / front-matter page. If any of
# these appears near the top of a chunk it is almost never trail-worthy body.
_MARKER_RE = re.compile(
    r"all rights reserved|library of congress|cataloging.?in.?publication|"
    r"\bisbn\b|printed in the united states|first (?:edition|published)|"
    r"copyright ©|©\s*\d{4}|www\.[a-z]|a cip catalog|cip data|"
    r"no part of this (?:book|publication)",
    re.IGNORECASE,
)

# Section headers that mark index / bibliography / notes back-matter, when they
# lead a chunk.
_SECTION_HEAD_RE = re.compile(
    r"^\W{0,4}(index|bibliography|works cited|references|notes|"
    r"table of contents|contents|acknowledg[ea]?ments|about the author|"
    r"also by|praise for|copyright|permissions|credits)\b",
    re.IGNORECASE,
)


def _digit_token_ratio(text: str) -> float:
    """Fraction of whitespace tokens that are (mostly) numeric.

    Index and citation pages are dense with page numbers ("Marr, 45, 49, 167"),
    which drives this well above the ~0.02–0.05 of ordinary prose.
    """
    toks = text.split()
    if not toks:
        return 0.0
    num = 0
    for t in toks:
        core = t.strip(".,;:()[]")
        if core and sum(c.isdigit() for c in core) >= max(1, len(core) - 1):
            num += 1
    return num / len(toks)


def is_back_matter(
    text: str,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> bool:
    """True if a chunk looks like index / bibliography / copyright / TOC matter.

    Combines content heuristics (strong publication markers, leading section
    headers, page-number density) with a positional signal (last ~6% of the
    book). A genuine body chunk that merely falls late in the book is retained
    unless its number density is also high, so the filter does not eat real
    late-book prose.
    """
    head = text.lstrip()[:300]

    # Strong publication / copyright markers anywhere near the top → drop.
    if _MARKER_RE.search(head):
        return True

    # A leading Index/Bibliography/Notes/TOC header → drop.
    if _SECTION_HEAD_RE.match(head):
        return True

    ratio = _digit_token_ratio(text)

    # Very high page-number density is an index/citation page regardless of
    # position (the max-word-count-per-topic heuristic loves these).
    if ratio >= 0.18:
        return True

    # Near the very end of the book AND moderately number-dense → back-matter.
    if (
        chunk_index is not None
        and total_chunks
        and chunk_index >= total_chunks * 0.94
        and ratio >= 0.10
    ):
        return True

    return False


# --------------------------------------------------------------------------- #
# Chunk / book lookup helpers
# --------------------------------------------------------------------------- #
def chunk_topic_labels(cur, chunk_id: int, limit: int = 12) -> List[str]:
    """All topic labels attached to a chunk (for excerpt metadata / gate)."""
    rows = cur.execute(
        """SELECT t.label FROM chunk_topics ct JOIN topics t ON t.id = ct.topic_id
           WHERE ct.chunk_id = ? ORDER BY t.label LIMIT ?""",
        (chunk_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _clean_text(text: str, n: int = 1100) -> str:
    # Phase 8.1 Fix 2: sentence-aware repair replaces the old mid-word [:850] cut.
    from src.readers.excerpt_repair import clean_for_reading
    return clean_for_reading(text, budget=n)


def book_lookup(cur, title_like: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Resolve a title substring to ``(book_id, title, author)`` (lowest id)."""
    r = cur.execute(
        "SELECT id, title, author FROM books WHERE title LIKE ? ORDER BY id LIMIT 1",
        (f"%{title_like}%",),
    ).fetchone()
    return (r[0], r[1], r[2]) if r else (None, None, None)


def _book_total_chunks(cur, book_id: int, _cache: Dict[int, int] = {}) -> int:
    if book_id not in _cache:
        r = cur.execute(
            "SELECT COUNT(*) FROM chunks WHERE book_id = ?", (book_id,)
        ).fetchone()
        _cache[book_id] = r[0] if r else 0
    return _cache[book_id]


def _excerpt(cur, book_id, book_title, author, chunk_id, chunk_index, word_count,
             text, role, matched_topic=None, similarity=None) -> Dict:
    return {
        "book_id": book_id,
        "book_title": book_title,
        "author": author or "Unknown",
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "word_count": word_count,
        "text": _clean_text(text),
        "topics": chunk_topic_labels(cur, chunk_id),
        "role": role,
        "matched_topic": matched_topic,
        "similarity": similarity,
    }


# --------------------------------------------------------------------------- #
# Seed-topic resolution (T3)
# --------------------------------------------------------------------------- #
def derive_seed_topics(cur, book_id: int, top_n: int = 6) -> List[str]:
    """Auto-derive steering seeds from a book's own strongest topics.

    Ranks the anchor book's embedded topics by in-book frequency (how many of
    the book's chunks carry the topic), which surfaces the book's dominant
    themes for the centroid. Ties fall back to global novelty when available so
    distinctive topics win over generic ones.
    """
    rows = cur.execute(
        """SELECT t.label, COUNT(*) AS in_book,
                  COALESCE((SELECT ns.mean_knn_distance FROM novelty_scores ns
                            WHERE ns.topic_id = t.id), 0) AS novelty
           FROM chunk_topics ct
           JOIN chunks ch ON ct.chunk_id = ch.id
           JOIN topics t ON t.id = ct.topic_id
           WHERE ch.book_id = ? AND t.embedding IS NOT NULL
           GROUP BY t.id
           ORDER BY in_book DESC, novelty DESC
           LIMIT ?""",
        (book_id, top_n),
    ).fetchall()
    return [r[0] for r in rows]


def resolve_seeds(
    cur,
    seed_topics: Optional[List[str]],
    anchor_book_id: Optional[int],
    top_n: int = 6,
) -> Tuple[List[str], List[str]]:
    """Resolve steering seeds → ``(labels, missing)``.

    Explicit ``seed_topics`` are validated against the vocabulary (missing ones
    are skipped and reported). With no seeds, they are auto-derived from the
    anchor book's top topics. Raises ``ValueError`` if neither path yields any
    usable seed.
    """
    if seed_topics:
        kept, missing = [], []
        for label in seed_topics:
            (kept if topic_vector(cur, label) is not None else missing).append(label)
        if not kept:
            raise ValueError(
                f"none of the seed topics exist in the vocabulary: {seed_topics}"
            )
        return kept, missing

    if anchor_book_id is not None:
        derived = derive_seed_topics(cur, anchor_book_id, top_n=top_n)
        if not derived:
            raise ValueError(
                f"could not auto-derive any seed topics from book id {anchor_book_id}"
            )
        return derived, []

    raise ValueError("seed resolution needs either --seed-topics or --anchor-book")


# --------------------------------------------------------------------------- #
# Shared-topic bridges (T4)
# --------------------------------------------------------------------------- #
def shared_topic_bridges(
    cur, book_id: int, min_books: int = 2, limit: int = 60
) -> List[Tuple[int, str]]:
    """Topics the anchor book shares with at least one other book.

    Because topics dedupe by exact label (``canonical_id`` NULL), a topic linked
    to both the anchor book and another book *is* a verified cross-book bridge.
    Returned as ``[(topic_id, label), ...]``, ranked by how many distinct books
    carry the topic (most-connected bridges first). Ubiquitous topics are still
    useful anchors for the union; the per-book companion cap keeps them from
    dominating downstream.
    """
    rows = cur.execute(
        """SELECT t.id, t.label, COUNT(DISTINCT ch.book_id) AS n_books
           FROM chunk_topics ct
           JOIN chunks ch ON ct.chunk_id = ch.id
           JOIN topics t ON t.id = ct.topic_id
           WHERE t.embedding IS NOT NULL
             AND t.id IN (
                 SELECT DISTINCT ct2.topic_id
                 FROM chunk_topics ct2
                 JOIN chunks ch2 ON ct2.chunk_id = ch2.id
                 WHERE ch2.book_id = ?)
           GROUP BY t.id
           HAVING n_books >= ?
           ORDER BY n_books DESC
           LIMIT ?""",
        (book_id, min_books, limit),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# --------------------------------------------------------------------------- #
# Anchor excerpts (pick_anchors)
# --------------------------------------------------------------------------- #
def pick_anchors(
    cur,
    book_id: int,
    book_title: str,
    author: Optional[str],
    centroid: np.ndarray,
    n: int = 2,
    min_words: int = 120,
    front_matter_skip: int = 12,
    forced_chunk_ids: Optional[List[int]] = None,
) -> List[Dict]:
    """Anchor excerpts from the anchor book.

    With ``forced_chunk_ids`` the given chunks are used verbatim (explicit
    ``--anchor-chunks``). Otherwise the book's body chunks are scored by their
    topic-centroid against the steering centroid and the top ``n`` are taken,
    skipping front matter (``chunk_index < front_matter_skip``) and back matter.
    """
    total = _book_total_chunks(cur, book_id)

    if forced_chunk_ids:
        anchors = []
        for cid in forced_chunk_ids:
            r = cur.execute(
                "SELECT chunk_index, word_count, text FROM chunks WHERE id = ?", (cid,)
            ).fetchone()
            if r:
                anchors.append(
                    _excerpt(cur, book_id, book_title, author, cid, r[0], r[1], r[2],
                             role="anchor")
                )
        return anchors

    cand = cur.execute(
        """SELECT id, chunk_index, word_count, text FROM chunks
           WHERE book_id = ? AND word_count > ? AND chunk_index >= ?""",
        (book_id, min_words, front_matter_skip),
    ).fetchall()

    scored = []
    for cid, idx, wc, text in cand:
        if is_back_matter(text, idx, total):
            continue
        v = _chunk_topic_centroid(cur, cid)
        if v is not None:
            scored.append((float(v @ centroid), cid, idx, wc, text))
    scored.sort(key=lambda x: x[0], reverse=True)

    anchors = []
    for _, cid, idx, wc, text in scored[:n]:
        anchors.append(
            _excerpt(cur, book_id, book_title, author, cid, idx, wc, text, role="anchor")
        )
    return anchors


# --------------------------------------------------------------------------- #
# Companion excerpts (find_companions)
# --------------------------------------------------------------------------- #
def _best_body_chunk_for_topic(cur, topic_id: int, min_words: int, scan: int = 6):
    """Highest-word-count *body* chunk for a topic (back-matter skipped).

    Scans the top ``scan`` chunks by word count (the original heuristic favored
    the single largest, which is exactly what lands on index/bibliography pages)
    and returns the first that survives the back-matter filter.
    """
    rows = cur.execute(
        """SELECT b.id, b.title, b.author, c.id, c.chunk_index, c.word_count, c.text
           FROM chunk_topics ct
           JOIN chunks c ON c.id = ct.chunk_id
           JOIN books b ON b.id = c.book_id
           WHERE ct.topic_id = ? AND c.word_count > ?
           ORDER BY c.word_count DESC LIMIT ?""",
        (topic_id, min_words, scan),
    ).fetchall()
    for bid, btitle, bauthor, cid, idx, wc, text in rows:
        if is_back_matter(text, idx, _book_total_chunks(cur, bid)):
            continue
        return bid, btitle, bauthor, cid, idx, wc, text
    return None


def find_companions(
    cur,
    centroid: np.ndarray,
    k: int = 6,
    per_book: int = 1,
    min_words: int = 120,
    exclude_book_ids: Optional[set] = None,
    exclude_substrings: Optional[List[str]] = None,
    bridge_topic_ids: Optional[List[int]] = None,
    overfetch: int = 0,
) -> List[Dict]:
    """Companion excerpts: centroid nearest-neighbor topics, bridges unioned first.

    Loads all embedded topic vectors, ranks them by similarity to the centroid,
    and prepends verified bridge topics so they are guaranteed candidates
    alongside the centroid NN set. Walks the ordered topics, taking the best
    *body* chunk per topic, one excerpt per book (``per_book`` cap), until ``k``
    (+``overfetch`` spare, for downstream dedupe backfill) companions are found.
    """
    exclude_book_ids = set(exclude_book_ids or [])
    excl = [e.strip().lower() for e in (exclude_substrings or []) if e.strip()]
    target = k + max(0, overfetch)

    rows = cur.execute(
        "SELECT id, label, embedding FROM topics WHERE embedding IS NOT NULL"
    ).fetchall()
    tids = [r[0] for r in rows]
    labels = {r[0]: r[1] for r in rows}
    index_of = {r[0]: i for i, r in enumerate(rows)}
    M = np.stack([np.frombuffer(r[2], dtype=DTYPE) for r in rows])
    sims = M @ centroid
    order = list(np.argsort(-sims))

    # Union: move bridge topics to the front (dedup), preserving their internal
    # (most-connected-first) order, so they are always considered as candidates.
    if bridge_topic_ids:
        bridge_idx = [index_of[t] for t in bridge_topic_ids if t in index_of]
        bridge_set = set(bridge_idx)
        order = bridge_idx + [i for i in order if i not in bridge_set]

    per_book_count: Dict[int, int] = {}
    companions: List[Dict] = []
    seen_topics: set = set()
    for i in order:
        tid = tids[i]
        if tid in seen_topics:
            continue
        seen_topics.add(tid)
        best = _best_body_chunk_for_topic(cur, tid, min_words)
        if not best:
            continue
        bid, btitle, bauthor, cid, idx, wc, text = best
        if bid in exclude_book_ids:
            continue
        if any(e in btitle.lower() for e in excl):
            continue
        if per_book_count.get(bid, 0) >= per_book:
            continue
        per_book_count[bid] = per_book_count.get(bid, 0) + 1
        companions.append(
            _excerpt(cur, bid, btitle, bauthor, cid, idx, wc, text, role="companion",
                     matched_topic=labels[tid], similarity=round(float(sims[i]), 3))
        )
        if len(companions) >= target:
            break
    return companions


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def retrieve_anchored(
    cur,
    *,
    anchor_book: Optional[str] = None,
    anchor_chunks: Optional[List[int]] = None,
    seed_topics: Optional[List[str]] = None,
    k: int = 6,
    per_book: int = 1,
    min_words: int = 120,
    anchor_n: int = 2,
    front_matter_skip: int = 12,
    exclude_books: Optional[List[str]] = None,
    seed_top_n: int = 6,
    backfill: int = 4,
    use_bridges: bool = True,
) -> Dict:
    """Full anchored/steered retrieval → a structured pack.

    Returns a dict with: ``seed_topics``, ``missing_seeds``, ``anchor_book``,
    ``anchor_book_id``, ``anchors`` (excerpts), ``companions`` (first ``k``), and
    ``spare`` (extra companions for dedupe backfill). Each excerpt carries
    book/author/chunk metadata, full topic list, matched topic + similarity
    (companions), and text. Supports anchored mode (``--anchor-book``) and
    topic-only mode (seeds only).
    """
    abid = atitle = aauthor = None
    if anchor_book:
        abid, atitle, aauthor = book_lookup(cur, anchor_book)
        if not abid:
            raise ValueError(f"anchor book not found: {anchor_book!r}")

    seeds, missing = resolve_seeds(cur, seed_topics, abid, top_n=seed_top_n)
    centroid, seed_missing = build_centroid(cur, seeds)
    if centroid is None:
        raise ValueError(f"no usable embeddings for seed topics: {seeds}")
    missing = list(dict.fromkeys(missing + seed_missing))

    anchors: List[Dict] = []
    if abid:
        anchors = pick_anchors(
            cur, abid, atitle, aauthor, centroid, n=anchor_n,
            min_words=min_words, front_matter_skip=front_matter_skip,
            forced_chunk_ids=anchor_chunks,
        )
    elif anchor_chunks:
        # anchor chunks without a named book: honor them anyway
        for cid in anchor_chunks:
            r = cur.execute(
                """SELECT c.book_id, b.title, b.author, c.chunk_index, c.word_count, c.text
                   FROM chunks c JOIN books b ON b.id = c.book_id WHERE c.id = ?""",
                (cid,),
            ).fetchone()
            if r:
                anchors.append(
                    _excerpt(cur, r[0], r[1], r[2], cid, r[3], r[4], r[5], role="anchor")
                )

    bridge_ids = None
    if use_bridges and abid:
        bridge_ids = [tid for tid, _ in shared_topic_bridges(cur, abid)]

    exclude_ids = {abid} if abid else set()
    companions = find_companions(
        cur, centroid, k=k, per_book=per_book, min_words=min_words,
        exclude_book_ids=exclude_ids, exclude_substrings=exclude_books,
        bridge_topic_ids=bridge_ids, overfetch=backfill,
    )

    return {
        "seed_topics": seeds,
        "missing_seeds": missing,
        "anchor_book": atitle,
        "anchor_book_id": abid,
        "anchors": anchors,
        "companions": companions[:k],
        "spare": companions[k:],
    }
