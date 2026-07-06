"""Process an EPUB book and store in database"""

import sys
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.readers.epub_parser import parse_epub
from src.readers.chunker import chunk_text
from src.database.operations import get_db
from src.config import TARGET_CHUNK_SIZE, CHUNK_TOLERANCE

console = Console()


def process_book(epub_path: str | Path, verbose: bool = True) -> int:
    """
    Process an EPUB book: parse, chunk, and store in database

    Args:
        epub_path: Path to EPUB file
        verbose: Print progress messages

    Returns:
        book_id: Database ID of processed book
    """
    epub_path = Path(epub_path)

    if not epub_path.exists():
        console.print(f"[red]✗ File not found: {epub_path}[/red]")
        sys.exit(1)

    if verbose:
        console.print(f"\n[bold]Processing:[/bold] {epub_path.name}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:

        # Step 1: Parse EPUB
        task = progress.add_task("Parsing EPUB...", total=None)
        text, metadata = parse_epub(epub_path)
        word_count = len(text.split())
        progress.update(task, completed=True)

        if verbose:
            console.print(f"  ✓ Parsed EPUB: {word_count:,} words")
            console.print(f"    Title: {metadata.get('title', 'Unknown')}")
            console.print(f"    Author: {metadata.get('author', 'Unknown')}")

        # Step 2: Chunk text
        task = progress.add_task("Chunking text...", total=None)
        chunks = chunk_text(text, target_words=TARGET_CHUNK_SIZE, tolerance=CHUNK_TOLERANCE)
        progress.update(task, completed=True)

        if verbose:
            word_counts = [c.word_count for c in chunks]
            in_range = sum(1 for w in word_counts if
                         TARGET_CHUNK_SIZE * (1 - CHUNK_TOLERANCE) <= w <=
                         TARGET_CHUNK_SIZE * (1 + CHUNK_TOLERANCE))
            console.print(f"  ✓ Created {len(chunks)} chunks")
            console.print(f"    Average: {sum(word_counts) / len(word_counts):.0f} words/chunk")
            console.print(f"    In range: {in_range}/{len(chunks)} ({100*in_range/len(chunks):.1f}%)")

        # Step 3: Store in database
        task = progress.add_task("Storing in database...", total=len(chunks))

        with get_db() as db:
            # Add book
            book_id = db.add_book(
                title=metadata.get('title', epub_path.stem),
                author=metadata.get('author'),
                isbn=metadata.get('isbn'),
                file_path=str(epub_path.absolute()),
                metadata=metadata
            )

            # Add chunks
            for chunk in chunks:
                db.add_chunk(
                    book_id=book_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    word_count=chunk.word_count,
                    start_position=chunk.start_position,
                    end_position=chunk.end_position
                )
                progress.advance(task)

            # Update book stats
            db.update_book_stats(book_id, total_chunks=len(chunks), total_topics=0)

        progress.update(task, completed=True)

    if verbose:
        console.print(f"  ✓ Stored in database (book_id={book_id})")
        console.print(f"\n[green]✅ Book processed successfully![/green]\n")

    return book_id


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: python scripts/process_book.py <path_to_epub>[/yellow]")
        console.print("\nExample:")
        console.print("  python scripts/process_book.py data/books/raw/pride_prejudice.epub")
        sys.exit(1)

    epub_file = sys.argv[1]
    book_id = process_book(epub_file, verbose=True)

    # Show database stats
    console.print("[bold]Database Statistics:[/bold]")
    with get_db() as db:
        stats = db.get_stats()
        console.print(f"  Total books: {stats['total_books']}")
        console.print(f"  Total chunks: {stats['total_chunks']}")
        console.print(f"  Total topics: {stats['total_topics']} (Phase 2)")
        console.print(f"  Total trails: {stats['total_trails']} (Phase 5)")


if __name__ == "__main__":
    main()
