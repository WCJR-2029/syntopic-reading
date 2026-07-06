#!/usr/bin/env python3
"""doctor.py — health check (and optional repair) for the syntopic library.

Answers the one question the old pipeline could not: "is my library actually whole,
or do I have books I THINK are finished but aren't?"

    python scripts/doctor.py            # report only
    python scripts/doctor.py --repair   # fix what is safely fixable (asks first)
    python scripts/doctor.py --repair --yes   # no prompt

Checks: books with 0 chunks, books with chunks but 0 topics, unembedded topics,
graph staleness, duplicate book rows, 0-byte EPUBs, the 0-byte decoy database, and
DB free-page bloat. Repair runs: extract missing topics -> embed -> rebuild graph ->
remove cruft -> VACUUM, doing only the stages that are actually needed.
"""

import sys
import argparse
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
# `src.*` is importable without a path hack once the package is `pip install -e .`'d.
# This insert stays: it's for `from extract_missing_topics import ...` below, a
# sibling script, not part of the installed `src` package.
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.config import DATABASE_PATH, DATA_DIR, TRAILS_DIR

console = Console()
PYTHON = sys.executable
DECOY_DB = DATA_DIR / "syntopic.db"


def gather():
    conn = sqlite3.connect(str(DATABASE_PATH))
    cur = conn.cursor()
    r = {}
    cur.execute("SELECT COUNT(*) FROM books"); r["books"] = cur.fetchone()[0]
    cur.execute("""SELECT id,title FROM books b WHERE NOT EXISTS
                   (SELECT 1 FROM chunks c WHERE c.book_id=b.id)""")
    r["no_chunks"] = cur.fetchall()
    cur.execute("""SELECT b.id,b.title FROM books b WHERE EXISTS
                   (SELECT 1 FROM chunks c WHERE c.book_id=b.id)
                   AND NOT EXISTS (SELECT 1 FROM chunks c JOIN chunk_topics ct ON ct.chunk_id=c.id
                                   WHERE c.book_id=b.id)""")
    r["no_topics"] = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM topics WHERE canonical_id IS NULL AND embedding IS NULL")
    r["unembedded"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM topics WHERE canonical_id IS NULL"); r["canonical"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM novelty_scores"); r["novelty"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM topic_hierarchy"); r["hierarchy"] = cur.fetchone()[0]
    cur.execute("SELECT title,COUNT(*) c FROM books GROUP BY title HAVING c>1")
    r["dup_rows"] = cur.fetchall()
    # Trails are persisted as markdown files, not DB rows — count the files.
    r["trails"] = len([p for p in TRAILS_DIR.glob("*.md") if p.name != "TRAIL-RATINGS.md"])
    cur.execute("PRAGMA freelist_count"); free = cur.fetchone()[0]
    cur.execute("PRAGMA page_count"); pages = cur.fetchone()[0]
    cur.execute("PRAGMA page_size"); psize = cur.fetchone()[0]
    r["db_mb"] = pages * psize / 1e6
    r["free_mb"] = free * psize / 1e6
    r["graph_stale"] = (r["canonical"] != r["novelty"]) or (r["canonical"] != r["hierarchy"])
    conn.close()
    # filesystem cruft
    r["zero_epubs"] = [p for p in (DATA_DIR / "books").rglob("*.epub") if p.stat().st_size == 0]
    r["decoy_db"] = DECOY_DB.exists() and DECOY_DB.stat().st_size == 0
    return r


def line(ok, label, detail=""):
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"  {icon} {label}" + (f"  [dim]{detail}[/dim]" if detail else ""))


def report(r) -> bool:
    console.print("\n[bold cyan]═══ Syntopic Library Doctor ═══[/bold cyan]\n")
    console.print(f"  Books: {r['books']}   Canonical topics: {r['canonical']:,}   Trails: {r['trails']}")
    console.print(f"  DB: {r['db_mb']:.0f} MB  (free pages: {r['free_mb']:.0f} MB)\n")
    healthy = True

    line(not r["no_chunks"], f"Books with 0 chunks: {len(r['no_chunks'])}",
         ", ".join(t for _, t in r["no_chunks"][:5]))
    line(not r["no_topics"], f"Books with chunks but 0 topics: {len(r['no_topics'])}",
         ", ".join(t for _, t in r["no_topics"][:5]))
    line(r["unembedded"] == 0, f"Unembedded topics: {r['unembedded']:,}")
    line(not r["graph_stale"], "Topic graph fresh",
         f"canonical={r['canonical']:,} novelty={r['novelty']:,} hierarchy={r['hierarchy']:,}")
    line(not r["dup_rows"], f"Duplicate book rows: {len(r['dup_rows'])}",
         ", ".join(t for t, _ in r["dup_rows"][:5]))
    line(not r["zero_epubs"], f"Zero-byte EPUB files: {len(r['zero_epubs'])}",
         ", ".join(p.name for p in r["zero_epubs"][:5]))
    line(not r["decoy_db"], "Decoy 0-byte database absent" if not r["decoy_db"] else "Decoy DB present",
         str(DECOY_DB) if r["decoy_db"] else "")
    line(r["free_mb"] < 100, f"DB free-page bloat: {r['free_mb']:.0f} MB",
         "consider VACUUM" if r["free_mb"] >= 100 else "")

    for k in ("no_chunks", "no_topics", "dup_rows", "zero_epubs"):
        if r[k]:
            healthy = False
    if r["unembedded"] or r["graph_stale"] or r["decoy_db"]:
        healthy = False

    console.print()
    console.print("[bold green]  ✅ Library is whole.[/bold green]" if healthy
                  else "[bold yellow]  ⚠ Issues found — run with --repair to fix.[/bold yellow]")
    return healthy


def repair(r):
    from extract_missing_topics import extract_topics_for_book
    console.print("\n[bold]Repairing…[/bold]")

    # 1) topics for chunked-but-untopiced books
    if r["no_topics"]:
        for bid, title in r["no_topics"]:
            console.print(f"  • extracting topics: {title[:50]}")
            try:
                extract_topics_for_book(bid, title)
            except Exception as e:
                console.print(f"[red]    ✗ {e}[/red]")
        r["unembedded"] = 1  # force embed pass below

    # 2) embeddings + vec
    if r["unembedded"]:
        subprocess.run([PYTHON, "scripts/refresh_embeddings_and_vec.py"], cwd=str(ROOT))
        r["graph_stale"] = True

    # 3) graph
    if r["graph_stale"]:
        subprocess.run([PYTHON, "scripts/build_graph.py"], cwd=str(ROOT))

    # 4) cruft
    for p in r["zero_epubs"]:
        try:
            p.unlink(); console.print(f"  • removed 0-byte EPUB: {p.name}")
        except Exception as e:
            console.print(f"[red]    ✗ {p}: {e}[/red]")
    if r["decoy_db"]:
        try:
            DECOY_DB.unlink(); console.print(f"  • removed decoy DB: {DECOY_DB}")
        except Exception as e:
            console.print(f"[red]    ✗ {e}[/red]")

    # 5) VACUUM (reclaim free pages)
    if r["free_mb"] >= 100:
        console.print("  • VACUUM (reclaiming free pages — may take a minute)…")
        conn = sqlite3.connect(str(DATABASE_PATH)); conn.execute("VACUUM"); conn.close()

    console.print("\n[bold]Re-checking…[/bold]")
    report(gather())


def main():
    ap = argparse.ArgumentParser(description="Health check / repair for the syntopic library.")
    ap.add_argument("--repair", action="store_true", help="Fix what is safely fixable")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation for --repair")
    args = ap.parse_args()

    r = gather()
    healthy = report(r)
    if not args.repair or healthy:
        sys.exit(0 if healthy else 1)

    if not args.yes:
        console.print("\n[bold]Repair will extract topics, regenerate embeddings, rebuild the graph, "
                      "remove cruft, and VACUUM as needed.[/bold]")
        if input("Proceed? (yes/no): ").strip().lower() not in ("yes", "y"):
            console.print("[yellow]Cancelled[/yellow]"); sys.exit(1)
    repair(r)


if __name__ == "__main__":
    main()
