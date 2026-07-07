#!/usr/bin/env python3
"""bootstrap_demo.py — zero-library-prep demo for a newcomer.

Downloads a small, themed, all-public-domain set of Project Gutenberg EPUBs
(political philosophy / power / ethics — chosen so the topic graph produces
strong cross-book trails and clears the pipeline's own quality gate of >=4
books / <=45% from one author), then optionally runs them through the
existing, already-proven ingestion pipeline so a newcomer sees a real result
without curating their own library first.

    python scripts/bootstrap_demo.py                 # download + ingest
    python scripts/bootstrap_demo.py --download-only  # fetch books only,
                                                       # no API keys required
    python scripts/bootstrap_demo.py --force          # re-download / re-ingest
                                                       # even if already present

This script ORCHESTRATES already-proven units; it does not reimplement them:
  - scripts/add_book.py     (parse -> chunk -> topics -> embed -> vec, per book)
  - scripts/build_graph.py  (PMI / clustering / novelty, run once at the end)

Every (title, gutenberg_id, url) below was verified by hand before being
hardcoded: an HTTP HEAD confirmed a 200 response with
content-type: application/epub+zip, and a ranged GET confirmed the file
starts with the zip magic bytes `PK\\x03\\x04`. See the DEMO_BOOKS list.
"""

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))  # for sibling scripts (matches add_book.py)

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Load .env before preflight_ingestion_keys() checks os.getenv() below — matches
# src/config.py (which add_book.py imports and which calls load_dotenv() itself).
# Without this, the documented quickstart (`cp .env.example .env` then run this
# script) fails preflight with a false "missing API key", since a key set only
# in `.env` (not the shell environment) would never be seen.
load_dotenv()

console = Console()
PYTHON = sys.executable

# ----------------------------------------------------------------------------- data
# Themed set: political philosophy / power / ethics. 6 books, 6 distinct authors
# (~16.7% share each) — comfortably clears the pipeline's diversity gate
# (>=4 books, <=45% from one author). Each entry's `url` was verified live
# (see module docstring) via:
#   curl -sI  https://www.gutenberg.org/cache/epub/{id}/pg{id}.epub   -> HTTP 200,
#       content-type: application/epub+zip
#   curl -s -r 0-3 <same url> | xxd                                  -> "PK.." magic
DEMO_BOOKS = [
    {
        "title": "The Republic",
        "author": "Plato",
        "gutenberg_id": 1497,
        "url": "https://www.gutenberg.org/cache/epub/1497/pg1497.epub",
        "filename": "plato-the-republic.epub",
    },
    {
        "title": "The Nicomachean Ethics",
        "author": "Aristotle",
        "gutenberg_id": 8438,
        "url": "https://www.gutenberg.org/cache/epub/8438/pg8438.epub",
        "filename": "aristotle-nicomachean-ethics.epub",
    },
    {
        # Gutenberg ID 1232 (W. K. Marriott translation) was the original pick here,
        # but that edition bundles a second, unrelated work - "The Life of Castruccio
        # Castracani of Lucca" - appended after The Prince's own ending, which the
        # chunker/topic pipeline has no way to know isn't part of The Prince. ID 57037
        # (Luigi Ricci translation) is The Prince ONLY: its last content file ends on
        # "THE END" straight into the standard Gutenberg license boilerplate, verified
        # by hand (see module docstring's verification method) - no appended work.
        "title": "The Prince",
        "author": "Niccolò Machiavelli",
        "gutenberg_id": 57037,
        "url": "https://www.gutenberg.org/cache/epub/57037/pg57037.epub",
        "filename": "machiavelli-the-prince.epub",
    },
    {
        "title": "On Liberty",
        "author": "John Stuart Mill",
        "gutenberg_id": 34901,
        "url": "https://www.gutenberg.org/cache/epub/34901/pg34901.epub",
        "filename": "mill-on-liberty.epub",
    },
    {
        "title": "Meditations",
        "author": "Marcus Aurelius",
        "gutenberg_id": 2680,
        "url": "https://www.gutenberg.org/cache/epub/2680/pg2680.epub",
        "filename": "marcus-aurelius-meditations.epub",
    },
    {
        "title": "Beyond Good and Evil",
        "author": "Friedrich Nietzsche",
        "gutenberg_id": 4363,
        "url": "https://www.gutenberg.org/cache/epub/4363/pg4363.epub",
        "filename": "nietzsche-beyond-good-and-evil.epub",
    },
]

DEMO_DIR = ROOT / "data" / "books" / "demo"
ZIP_MAGIC = b"PK\x03\x04"
MIN_VALID_SIZE = 20_000  # bytes — well under the smallest verified EPUB (~197KB);
# catches truncated/partial downloads without needing an exact expected size.
USER_AGENT = "syntopic-reading-bootstrap-demo/1.0 (+https://www.gutenberg.org)"


# ----------------------------------------------------------------------------- helpers
def _is_valid_epub(path: Path) -> bool:
    """Cheap sanity check: file exists, is big enough to be real, and starts
    with the local-file-header zip magic bytes. Not a full zip integrity
    check (add_book.py's validate_epub does that, more thoroughly, at
    ingestion time) — this just guards against a truncated/HTML-error-page
    download being mistaken for a valid EPUB."""
    if not path.exists() or path.stat().st_size < MIN_VALID_SIZE:
        return False
    try:
        with open(path, "rb") as f:
            return f.read(4) == ZIP_MAGIC
    except OSError:
        return False


def download_book(book: dict, force: bool = False) -> Path | None:
    """Download one book's EPUB into DEMO_DIR, idempotently. Returns the final
    path on success, None on failure. Downloads to a `.part` temp file first
    and only renames to the final name once the zip-magic/size check passes,
    so a crashed/interrupted download can never be mistaken for a complete one
    on the next run."""
    dest = DEMO_DIR / book["filename"]
    if dest.exists() and not force:
        if _is_valid_epub(dest):
            console.print(f"  [dim]already present, skipping:[/dim] {book['filename']}")
            return dest
        console.print(f"  [yellow]existing file looks corrupt/truncated — re-downloading:[/yellow] {book['filename']}")

    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(book["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
            out.write(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        console.print(f"  [red]✗ download failed for '{book['title']}' ({book['url']}): {e}[/red]")
        tmp.unlink(missing_ok=True)
        return None

    if not _is_valid_epub(tmp):
        console.print(
            f"  [red]✗ downloaded file for '{book['title']}' failed the EPUB sanity check "
            f"(size or zip-magic mismatch) — discarding[/red]"
        )
        tmp.unlink(missing_ok=True)
        return None

    tmp.rename(dest)
    size_kb = dest.stat().st_size // 1024
    console.print(f"  [green]✓ downloaded[/green] {book['filename']} ({size_kb:,} KB)")
    return dest


def preflight_ingestion_keys() -> list[str]:
    """Return a list of missing-key problems (empty == good). Mirrors
    add_book.py's own key check: topic extraction and trail generation now
    share one provider-agnostic client (src/llm/client.py), so this checks the
    API key for whichever LLM_PROVIDER is configured (Anthropic by default,
    or OpenAI if LLM_PROVIDER=openai) — failing fast, before spending any API
    calls, instead of partway through all of them."""
    problems = []
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            problems.append("OPENAI_API_KEY")
    else:
        if not os.getenv("ANTHROPIC_API_KEY"):
            problems.append("ANTHROPIC_API_KEY")
    return problems


def print_book_table() -> None:
    table = Table(title="Demo library (political philosophy / power / ethics)")
    table.add_column("Title")
    table.add_column("Author")
    table.add_column("Gutenberg ID", justify="right")
    for b in DEMO_BOOKS:
        table.add_row(b["title"], b["author"], str(b["gutenberg_id"]))
    console.print(table)


# ----------------------------------------------------------------------------- main
def run_step(label: str, argv: list) -> bool:
    console.print(f"\n[bold]▶ {label}[/bold]")
    rc = subprocess.run(argv, cwd=str(ROOT)).returncode
    if rc != 0:
        console.print(f"[red]  ✗ {label} failed (exit {rc})[/red]")
        return False
    return True


def bootstrap(download_only: bool, force: bool) -> bool:
    console.print("\n[bold cyan]═══ bootstrap_demo: syntopic reading, zero library prep ═══[/bold cyan]\n")
    print_book_table()

    if not download_only:
        missing = preflight_ingestion_keys()
        if missing:
            console.print(
                f"\n[red]✗ Missing required API key(s): {', '.join(missing)}.[/red]\n"
                "  Set them in your environment (or a `.env` file — see .env.example) "
                "and re-run, or run with [bold]--download-only[/bold] to just fetch the "
                "books without ingesting them. See README.md for setup.\n"
            )
            return False

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"\n[bold]Downloading {len(DEMO_BOOKS)} books to {DEMO_DIR}[/bold]\n")

    downloaded = []
    failed_downloads = []
    for book in DEMO_BOOKS:
        path = download_book(book, force=force)
        if path is not None:
            downloaded.append((book, path))
        else:
            failed_downloads.append(book["title"])

    if failed_downloads:
        console.print(f"\n[yellow]⚠ {len(failed_downloads)} download(s) failed: {', '.join(failed_downloads)}[/yellow]")

    if download_only:
        console.print(
            f"\n[bold green]✅ Download-only complete — {len(downloaded)}/{len(DEMO_BOOKS)} books in {DEMO_DIR}[/bold green]\n"
            "  Run again without --download-only to ingest them into the library."
        )
        return len(downloaded) > 0

    if not downloaded:
        console.print("[red]✗ No books downloaded — nothing to ingest.[/red]")
        return False

    console.print(f"\n[bold]Ingesting {len(downloaded)} book(s) via scripts/add_book.py[/bold]")
    failed_ingests = []
    for book, path in downloaded:
        argv = [PYTHON, "scripts/add_book.py", str(path), "--defer-graph"]
        if force:
            argv.append("--force")
        if not run_step(f"add_book: {book['title']}", argv):
            failed_ingests.append(book["title"])

    ok = run_step("build_graph: rebuilding topic graph (PMI / clustering / novelty)",
                  [PYTHON, "scripts/build_graph.py"])

    console.print("\n" + "=" * 60)
    if failed_ingests or not ok:
        if failed_ingests:
            console.print(f"[red]  ✗ ingestion failed for: {', '.join(failed_ingests)}[/red]")
        if not ok:
            console.print("[red]  ✗ graph rebuild failed[/red]")
        console.print("[bold red]❌ INCOMPLETE — see errors above[/bold red]")
        return False

    console.print(
        f"[bold green]✅ DONE — {len(downloaded)} books downloaded, ingested, and graph rebuilt.[/bold green]\n"
        "  Try: python -m src.agent.trail_generator --idea \"power and legitimacy\" --count 3"
    )
    return True


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Zero-library-prep demo: download a themed ~6-book public-domain "
            "Project Gutenberg set (political philosophy / power / ethics) and, "
            "unless --download-only is given, ingest it end-to-end via the "
            "existing add_book.py / build_graph.py pipeline."
        )
    )
    ap.add_argument(
        "--download-only",
        action="store_true",
        help="Fetch the demo EPUBs into data/books/demo/ but skip ingestion "
             "(no API keys required — useful for testing or if you don't yet "
             "have your configured LLM provider's API key set — ANTHROPIC_API_KEY "
             "by default).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if already present, and reprocess books "
             "even if already fully ingested (passes --force to add_book.py).",
    )
    args = ap.parse_args()
    ok = bootstrap(download_only=args.download_only, force=args.force)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
