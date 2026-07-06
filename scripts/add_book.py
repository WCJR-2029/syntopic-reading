#!/usr/bin/env python3
"""add_book.py — the single, verified, idempotent way to add ONE book to the
syntopic library end-to-end.

Pipeline: parse -> chunk -> topics -> embed -> vec -> graph, with STATE DETECTION
so a re-run resumes/repairs the exact missing stage instead of duplicating the book
or silently leaving it half-finished (the failure mode that produced the
92K-unembedded-topics swath).

    python scripts/add_book.py <epub-path> [--defer-graph] [--force]

--defer-graph : skip the (heavy, global) graph rebuild — use when batch-adding many
                books, then run `python scripts/build_graph.py` once at the end, or
                `python scripts/doctor.py --repair`.
--force       : re-process even a book that is already complete (deletes its chunks +
                links + row first, keeps shared topics).

This script ORCHESTRATES already-proven units; it does not reimplement them:
  - process_with_topics.process_book_with_topics  (chunks + topics for a fresh book)
  - extract_missing_topics.extract_topics_for_book (topics for an already-chunked book)
  - refresh_embeddings_and_vec.py                  (incremental embed + vec rebuild)
  - build_graph.py                                 (PMI / clustering / novelty)
"""

import os
import sys
import argparse
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
# `src.*` is importable without a path hack once the package is `pip install -e .`'d.
# This insert stays: it's for `from process_with_topics import ...` / `from
# extract_missing_topics import ...` below, which are sibling scripts, not part of
# the installed `src` package.
sys.path.insert(0, str(Path(__file__).parent))  # for sibling scripts

from rich.console import Console
from src.config import DATABASE_PATH, BOOKS_RAW_DIR

console = Console()
PYTHON = sys.executable


# ----------------------------------------------------------------------------- helpers
def ensure_database() -> None:
    """If DATABASE_PATH doesn't exist yet, initialize the schema there instead of
    aborting — a fresh clone of this repo has no database until the first book is
    added. Uses the idempotent `CREATE TABLE IF NOT EXISTS` DDL in
    src.database.schema.create_database, so it's always safe to call even if a
    partially-initialized DB already exists."""
    if Path(DATABASE_PATH).exists():
        return
    from src.database.schema import create_database
    console.print(f"[yellow]  No database found at {DATABASE_PATH} — initializing new database…[/yellow]")
    create_database(DATABASE_PATH)
    console.print(f"[green]  ✓ initialized new database at {DATABASE_PATH}[/green]")


def validate_epub(epub: Path) -> tuple[bool, str]:
    """Cheap structural check that `epub` is actually a readable EPUB — a valid zip
    containing a `mimetype` entry and/or an OPF package file — BEFORE handing it to
    the pipeline. Without this, an unsupported, corrupt, or otherwise unreadable file dies as an
    opaque zipfile.BadZipFile deep inside src.readers.epub_parser with no actionable
    guidance. Returns (True, "") when valid, or (False, actionable_message)."""
    if not zipfile.is_zipfile(epub):
        return False, (
            f"'{epub.name}' is not a readable EPUB (not a valid zip archive). "
            "add_book requires a readable EPUB — convert your source to a readable "
            "EPUB and see PREPARING-YOUR-LIBRARY.md."
        )
    try:
        with zipfile.ZipFile(epub, "r") as z:
            bad_entry = z.testzip()
            if bad_entry is not None:
                return False, (
                    f"'{epub.name}' is a corrupt EPUB (bad zip entry: {bad_entry}). "
                    "add_book requires a readable EPUB — convert your source to a "
                    "readable EPUB and see PREPARING-YOUR-LIBRARY.md."
                )
            names = z.namelist()
            has_mimetype = "mimetype" in names
            has_opf = any(n.lower().endswith(".opf") for n in names)
            if not (has_mimetype or has_opf):
                return False, (
                    f"'{epub.name}' does not look like an EPUB (no mimetype or .opf "
                    "package file found inside). add_book requires a readable EPUB — "
                    "convert your source to a readable EPUB and see "
                    "PREPARING-YOUR-LIBRARY.md."
                )
    except zipfile.BadZipFile:
        return False, (
            f"'{epub.name}' is not a readable EPUB (bad zip file). "
            "add_book requires a readable EPUB — convert your source to a readable "
            "EPUB and see PREPARING-YOUR-LIBRARY.md."
        )
    return True, ""


def prereqs() -> list:
    """Return a list of prerequisite problems (empty == good). Deliberately does NOT
    call src.config.validate_config(), which falsely fails (it checks GEMINI_API_KEY,
    but the topic extractor authenticates via GOOGLE_API_KEY). Database existence is
    NOT checked here — ensure_database() handles that by initializing the schema
    rather than treating a missing DB as a blocking failure."""
    problems = []
    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        problems.append("No GOOGLE_API_KEY / GEMINI_API_KEY in environment (topic extraction will fail)")
    if shutil.which("ebook-meta") is None:
        problems.append("ebook-meta (Calibre CLI) not on PATH — identity pre-check degraded to filename")
    return problems


def epub_identity(epub: Path) -> dict:
    """Cheap identity read via Calibre's ebook-meta (no full-text parse). Falls back
    to the filename stem for the title if ebook-meta is unavailable."""
    ident = {"title": epub.stem, "isbn": None, "basename": epub.name}
    if shutil.which("ebook-meta") is None:
        return ident
    try:
        out = subprocess.run(["ebook-meta", str(epub)], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ident
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "title" and val:
            ident["title"] = val
        elif key == "identifiers" and "isbn:" in val.lower():
            for tok in val.replace(",", " ").split():
                if tok.lower().startswith("isbn:"):
                    ident["isbn"] = tok.split(":", 1)[1].strip()
    return ident


def _tokens(s: str) -> set:
    """Lowercased alphanumeric word tokens."""
    return {t for t in "".join(ch.lower() if ch.isalnum() else " " for ch in (s or "")).split() if t}


def _title_match(clean_title: str, db_title: str) -> bool:
    """Token-containment match — robust to the DB's mangled 'Title   Author' titles
    and to article/word-order differences. True when (almost) all of the clean
    title's tokens appear in the DB title and at least one distinctive (len>=5)
    token is shared, to avoid short-common-word false positives."""
    ct, dt = _tokens(clean_title), _tokens(db_title)
    if not ct or not dt:
        return False
    shared = ct & dt
    if not shared:
        return False
    coverage = len(shared) / len(ct)
    distinctive = any(len(t) >= 5 for t in shared)
    return coverage >= 0.85 and distinctive


def find_book(cur, ident: dict, target_path: Path):
    """Return existing book_id matching this epub by file_path basename, ISBN, or
    token-containment title match — or None. Biased toward catching existing books
    (a missed match wastes a few cents reprocessing; a false match would silently
    skip a genuinely new book, so the distinctive-token guard keeps false matches rare)."""
    cur.execute("SELECT id, title, isbn, file_path FROM books")
    rows = cur.fetchall()
    for bid, title, isbn, fpath in rows:
        if fpath and Path(fpath).name == target_path.name:
            return bid
        if ident["isbn"] and isbn and ident["isbn"] == isbn:
            return bid
    for bid, title, isbn, fpath in rows:
        if _title_match(ident["title"], title):
            return bid
    return None


def book_state(cur, book_id):
    """Book-level state: 'no_chunks' | 'no_topics' | 'ok'."""
    cur.execute("SELECT COUNT(*) FROM chunks WHERE book_id=?", (book_id,))
    n_chunks = cur.fetchone()[0]
    if n_chunks == 0:
        return "no_chunks", n_chunks, 0
    cur.execute("""SELECT COUNT(*) FROM chunk_topics ct
                   JOIN chunks c ON c.id=ct.chunk_id WHERE c.book_id=?""", (book_id,))
    n_links = cur.fetchone()[0]
    if n_links == 0:
        return "no_topics", n_chunks, 0
    return "ok", n_chunks, n_links


def global_state(cur):
    """Return (unembedded_topics, graph_stale: bool)."""
    cur.execute("SELECT COUNT(*) FROM topics WHERE canonical_id IS NULL AND embedding IS NULL")
    unembedded = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM topics WHERE canonical_id IS NULL")
    canon = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM novelty_scores")
    novelty = cur.fetchone()[0]
    return unembedded, (canon != novelty)


def run_step(label: str, argv: list) -> bool:
    console.print(f"\n[bold]▶ {label}[/bold]")
    rc = subprocess.run(argv, cwd=str(ROOT)).returncode
    if rc != 0:
        console.print(f"[red]  ✗ {label} failed (exit {rc})[/red]")
        return False
    return True


def delete_book(conn, book_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM chunk_topics WHERE chunk_id IN (SELECT id FROM chunks WHERE book_id=?)", (book_id,))
    cur.execute("DELETE FROM chunks WHERE book_id=?", (book_id,))
    cur.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()


# ----------------------------------------------------------------------------- main
def add_book(epub_path: str, defer_graph: bool = False, force: bool = False) -> bool:
    epub = Path(epub_path).expanduser()
    if not epub.exists():
        console.print(f"[red]✗ File not found: {epub}[/red]")
        return False

    console.print(f"\n[bold cyan]═══ add_book: {epub.name} ═══[/bold cyan]")

    epub_ok, epub_err = validate_epub(epub)
    if not epub_ok:
        console.print(f"[red]✗ {epub_err}[/red]")
        return False

    ensure_database()

    problems = prereqs()
    blocking = [p for p in problems if "degraded" not in p]
    for p in problems:
        console.print(f"[yellow]  ⚠ {p}[/yellow]")
    if blocking:
        console.print("[red]✗ Blocking prerequisite(s) unmet — aborting.[/red]")
        return False

    ident = epub_identity(epub)
    target = BOOKS_RAW_DIR / epub.name
    console.print(f"  Identity: title=\"{ident['title']}\"  isbn={ident['isbn']}")

    conn = sqlite3.connect(str(DATABASE_PATH))
    cur = conn.cursor()
    book_id = find_book(cur, ident, target)

    # ---- resolve book-level state and act
    if book_id is not None and force:
        console.print(f"  --force: deleting existing book id={book_id} for clean reprocess")
        delete_book(conn, book_id)
        book_id = None

    if book_id is None:
        state = "absent"
    else:
        state, nch, nlk = book_state(cur, book_id)
        console.print(f"  Found existing book id={book_id} state={state} (chunks={nch}, topic-links={nlk})")

    conn.close()  # process_* functions open their own connections

    from process_with_topics import process_book_with_topics
    from extract_missing_topics import extract_topics_for_book

    if state in ("absent", "no_chunks"):
        if state == "no_chunks":
            console.print("  Previous attempt left 0 chunks (failed parse) — reprocessing from scratch")
            conn = sqlite3.connect(str(DATABASE_PATH)); delete_book(conn, book_id); conn.close()
        # canonicalize location
        if not str(epub.resolve()).startswith(str(BOOKS_RAW_DIR.resolve())):
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(epub, target)
            epub = target
        console.print(f"  Fresh process (chunks + topics): {epub.name}")
        try:
            stats = process_book_with_topics(epub)
            console.print(f"  ✓ book_id={stats['book_id']} chunks={stats['total_chunks']} "
                          f"useful={stats['useful_chunks']} topics={stats['unique_topics']}")
        except Exception as e:
            console.print(f"[red]  ✗ processing failed: {e}[/red]")
            return False
    elif state == "no_topics":
        console.print("  Resuming: extracting topics for already-chunked book")
        try:
            extract_topics_for_book(book_id, ident["title"])
        except Exception as e:
            console.print(f"[red]  ✗ topic extraction failed: {e}[/red]")
            return False
    else:  # ok
        console.print("  Book-level content already complete (chunks + topics present)")

    # ---- global stages (idempotent: only do work if needed)
    conn = sqlite3.connect(str(DATABASE_PATH)); cur = conn.cursor()
    unembedded, graph_stale = global_state(cur)
    conn.close()

    if unembedded > 0:
        if not run_step(f"Embedding {unembedded:,} new/unembedded topics + rebuilding vec store",
                        [PYTHON, "scripts/refresh_embeddings_and_vec.py"]):
            return False
        graph_stale = True  # embeddings changed -> graph must be rebuilt
    else:
        console.print("\n[dim]  All topics already embedded — skipping embed step[/dim]")

    if graph_stale and not defer_graph:
        if not run_step("Rebuilding topic graph (PMI / clustering / novelty)",
                        [PYTHON, "scripts/build_graph.py"]):
            return False
    elif defer_graph and graph_stale:
        console.print("\n[yellow]  --defer-graph: graph left STALE. Run "
                      "`python scripts/build_graph.py` (or doctor --repair) before generating trails.[/yellow]")
    else:
        console.print("[dim]  Graph already fresh — skipping rebuild[/dim]")

    # ---- final verification (this is what makes "done" mean done)
    return verify(epub, ident, defer_graph)


def verify(epub: Path, ident: dict, defer_graph: bool) -> bool:
    conn = sqlite3.connect(str(DATABASE_PATH)); cur = conn.cursor()
    bid = find_book(cur, ident, BOOKS_RAW_DIR / epub.name)
    fails = []
    if bid is None:
        fails.append("book row not found after processing")
    else:
        st, nch, nlk = book_state(cur, bid)
        if nch == 0:
            fails.append("0 chunks")
        if nlk == 0:
            fails.append("0 topic links")
    unembedded, graph_stale = global_state(cur)
    conn.close()
    if unembedded > 0:
        fails.append(f"{unembedded} unembedded topics")
    if graph_stale and not defer_graph:
        fails.append("graph stale")

    console.print("\n" + "=" * 60)
    if not fails:
        tail = " (graph deferred — remember to rebuild)" if defer_graph else ""
        console.print(f"[bold green]✅ DONE — '{ident['title']}' fully processed{tail}[/bold green]")
        return True
    console.print(f"[bold red]❌ INCOMPLETE — {', '.join(fails)}[/bold red]")
    return False


def main():
    ap = argparse.ArgumentParser(description="Add one book to the syntopic library, end-to-end and verified.")
    ap.add_argument("epub", help="Path to a readable EPUB")
    ap.add_argument("--defer-graph", action="store_true", help="Skip the heavy graph rebuild (batch mode)")
    ap.add_argument("--force", action="store_true", help="Reprocess even if already complete")
    args = ap.parse_args()
    ok = add_book(args.epub, defer_graph=args.defer_graph, force=args.force)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
