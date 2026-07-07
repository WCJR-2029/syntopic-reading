"""Process book with topic extraction (Phase 2 test)"""

import sys
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.readers.epub_parser import parse_epub
from src.readers.chunker import chunk_text
from src.extraction.usefulness_filter import UsefulnessFilter
from src.extraction.topic_extractor import TopicExtractor
from src.database.operations import get_db
from src.config import TARGET_CHUNK_SIZE, CHUNK_TOLERANCE, TOPICS_PER_CHUNK
from src.llm.client import get_client

console = Console()

# How often to print a running LLM-call count during a long ingest (a chunk makes
# up to 2 calls - usefulness check + topic extraction - so a several-hundred-chunk
# book can run for minutes with zero spend feedback otherwise).
CALL_COUNT_PRINT_EVERY = 25


def process_book_with_topics(epub_path: str | Path) -> dict:
    """
    Process book with topic extraction

    Args:
        epub_path: Path to EPUB file

    Returns:
        dict with processing stats
    """
    epub_path = Path(epub_path)

    if not epub_path.exists():
        console.print(f"[red]✗ File not found: {epub_path}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Processing with Topics:[/bold] {epub_path.name}")

    # Initialize filters/extractors
    usefulness_filter = UsefulnessFilter()
    topic_extractor = TopicExtractor(num_topics=TOPICS_PER_CHUNK)

    stats = {
        'book_id': None,
        'total_chunks': 0,
        'useful_chunks': 0,
        'total_topics': 0,
        'unique_topics': 0,
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
    ) as progress:

        # Step 1: Parse EPUB
        task = progress.add_task("Parsing EPUB...", total=None)
        text, metadata = parse_epub(epub_path)
        progress.update(task, completed=True)
        console.print(f"  ✓ Parsed: {len(text.split()):,} words")

        # Step 2: Chunk text
        task = progress.add_task("Chunking text...", total=None)
        chunks = chunk_text(text, target_words=TARGET_CHUNK_SIZE, tolerance=CHUNK_TOLERANCE)
        stats['total_chunks'] = len(chunks)
        progress.update(task, completed=True)
        console.print(f"  ✓ Created {len(chunks)} chunks")

        # Step 3: Filter + Extract Topics
        task = progress.add_task("Extracting topics...", total=len(chunks))

        with get_db() as db:
            # Add book — normalize metadata first so the author is recovered from
            # a "Title   Author" title or a "Title - Author" filename when the OPF
            # has no dc:creator (front-end guard against the NULL-author defect).
            from src.readers.metadata_utils import normalize_metadata
            _title, _author = normalize_metadata(
                metadata.get('title', epub_path.stem),
                metadata.get('author'),
                filename_stem=Path(epub_path).stem,
            )
            book_id = db.add_book(
                title=_title,
                author=_author,
                isbn=metadata.get('isbn'),
                file_path=str(epub_path.absolute()),
                metadata=metadata
            )
            stats['book_id'] = book_id

            all_topics = set()
            topic_count = 0
            llm_client = get_client()  # shared, cached - same instance used below

            for i, chunk in enumerate(chunks, 1):
                # Add chunk to database
                chunk_id = db.add_chunk(
                    book_id=book_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    word_count=chunk.word_count,
                    start_position=chunk.start_position,
                    end_position=chunk.end_position
                )

                # Check usefulness
                is_useful = usefulness_filter.is_useful(chunk.text)

                if is_useful:
                    stats['useful_chunks'] += 1

                    # Extract topics
                    topics = topic_extractor.extract_topics(chunk.text)

                    for topic_label in topics:
                        # Add topic (or increment frequency if exists)
                        topic_id = db.add_topic(topic_label)
                        all_topics.add(topic_label)

                        # Link chunk to topic
                        db.link_chunk_topic(chunk_id, topic_id)

                        topic_count += 1

                progress.advance(task)

                if i % CALL_COUNT_PRINT_EVERY == 0:
                    console.print(f"  [dim]  ...{i}/{len(chunks)} chunks, "
                                  f"{llm_client.call_count} LLM calls so far[/dim]")

            stats['total_topics'] = topic_count
            stats['unique_topics'] = len(all_topics)
            stats['llm_calls'] = llm_client.call_count

            # Update book stats
            db.update_book_stats(book_id, total_chunks=len(chunks), total_topics=len(all_topics))

        console.print(f"  [dim]Total LLM calls for this book: {llm_client.call_count}[/dim]")

    return stats


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        console.print("[yellow]Usage: python scripts/process_with_topics.py <path_to_epub>[/yellow]")
        sys.exit(1)

    epub_file = sys.argv[1]
    stats = process_book_with_topics(epub_file)

    # Show results
    console.print("\n[bold green]✅ Processing Complete![/bold green]\n")
    console.print("[bold]Results:[/bold]")
    console.print(f"  Book ID: {stats['book_id']}")
    console.print(f"  Total chunks: {stats['total_chunks']}")
    console.print(f"  Useful chunks: {stats['useful_chunks']} ({100*stats['useful_chunks']/stats['total_chunks']:.1f}%)")
    console.print(f"  Total topics extracted: {stats['total_topics']}")
    console.print(f"  Unique topics: {stats['unique_topics']}")
    console.print(f"  Avg topics/useful chunk: {stats['total_topics']/stats['useful_chunks']:.1f}" if stats['useful_chunks'] > 0 else "")
    console.print(f"  LLM calls made: {stats.get('llm_calls', '?')}")

    # Show database stats
    console.print("\n[bold]Database Statistics:[/bold]")
    with get_db() as db:
        db_stats = db.get_stats()
        console.print(f"  Total books: {db_stats['total_books']}")
        console.print(f"  Total chunks: {db_stats['total_chunks']}")
        console.print(f"  Total topics: {db_stats['total_topics']}")


if __name__ == "__main__":
    main()
