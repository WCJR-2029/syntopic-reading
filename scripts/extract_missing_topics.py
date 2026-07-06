"""Extract topics for books that are missing topic data"""

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.extraction.topic_extractor import TopicExtractor
from src.extraction.usefulness_filter import UsefulnessFilter
from src.database.operations import get_db
from src.config import TOPICS_PER_CHUNK

console = Console()


def get_books_without_topics():
    """Find all books that have no topics extracted"""
    with get_db() as db:
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT DISTINCT b.id, b.title, b.author,
                   (SELECT COUNT(*) FROM chunks WHERE book_id = b.id) as chunk_count
            FROM books b
            WHERE NOT EXISTS (
                SELECT 1 FROM chunks c
                JOIN chunk_topics ct ON c.id = ct.chunk_id
                WHERE c.book_id = b.id
            )
            ORDER BY b.title
        ''')
        books = cursor.fetchall()

        return [
            {
                'id': row[0],
                'title': row[1],
                'author': row[2],
                'chunk_count': row[3]
            }
            for row in books
        ]


def extract_topics_for_book(book_id: int, book_title: str, progress_task=None, progress=None):
    """Extract topics for all chunks of a specific book"""

    topic_extractor = TopicExtractor(num_topics=TOPICS_PER_CHUNK)
    usefulness_filter = UsefulnessFilter()

    stats = {
        'total_chunks': 0,
        'useful_chunks': 0,
        'topics_extracted': 0,
        'unique_topics': 0
    }

    with get_db() as db:
        # Get all chunks for this book
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT id, text
            FROM chunks
            WHERE book_id = ?
            ORDER BY chunk_index
        ''', (book_id,))
        chunks = cursor.fetchall()

        stats['total_chunks'] = len(chunks)

        if progress and progress_task:
            progress.update(progress_task, total=len(chunks))

        all_topics = set()

        for chunk_id, chunk_text in chunks:
            # DISABLED: Skip usefulness filter - extract from all chunks
            # is_useful = usefulness_filter.is_useful(chunk_text)
            is_useful = True  # Always process all chunks

            if is_useful:
                stats['useful_chunks'] += 1

                # Extract topics
                topics = topic_extractor.extract_topics(chunk_text)

                for topic_label in topics:
                    # Add topic (or get existing)
                    topic_id = db.add_topic(topic_label)
                    all_topics.add(topic_label)

                    # Link chunk to topic
                    db.link_chunk_topic(chunk_id, topic_id)

                    stats['topics_extracted'] += 1

            if progress and progress_task:
                progress.advance(progress_task)

        stats['unique_topics'] = len(all_topics)

        # Update book stats
        db.update_book_stats(
            book_id,
            total_chunks=stats['total_chunks'],
            total_topics=stats['unique_topics']
        )

    return stats


def main():
    """Main entry point"""
    console.print("\n[bold cyan]🔍 Finding books without topics...[/bold cyan]\n")

    books_without_topics = get_books_without_topics()

    if not books_without_topics:
        console.print("[green]✅ All books already have topics extracted![/green]")
        return

    console.print(f"[yellow]Found {len(books_without_topics)} books without topics:[/yellow]\n")

    # Show first 10 books
    for book in books_without_topics[:10]:
        console.print(f"  • {book['title']} ({book['chunk_count']} chunks)")

    if len(books_without_topics) > 10:
        console.print(f"  ... and {len(books_without_topics) - 10} more")

    # Confirm
    console.print(f"\n[bold]Ready to extract topics for {len(books_without_topics)} books[/bold]")
    console.print(f"[dim]Estimated cost: ~${len(books_without_topics) * 0.01:.2f} (configured LLM)[/dim]")
    console.print(f"[dim]Estimated time: ~{len(books_without_topics) * 1.5:.0f} minutes[/dim]\n")

    response = input("Continue? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        console.print("[yellow]Cancelled[/yellow]")
        return

    # Process all books
    console.print("\n[bold green]🚀 Processing books...[/bold green]\n")

    overall_stats = {
        'books_processed': 0,
        'total_chunks': 0,
        'total_topics': 0,
        'total_unique_topics': 0
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        overall_task = progress.add_task(
            f"Processing {len(books_without_topics)} books...",
            total=len(books_without_topics)
        )

        for i, book in enumerate(books_without_topics, 1):
            book_task = progress.add_task(
                f"  [{i}/{len(books_without_topics)}] {book['title'][:40]}...",
                total=None
            )

            try:
                stats = extract_topics_for_book(
                    book['id'],
                    book['title'],
                    progress_task=book_task,
                    progress=progress
                )

                overall_stats['books_processed'] += 1
                overall_stats['total_chunks'] += stats['total_chunks']
                overall_stats['total_topics'] += stats['topics_extracted']
                overall_stats['total_unique_topics'] += stats['unique_topics']

                progress.update(
                    book_task,
                    description=f"  ✅ {book['title'][:40]} ({stats['unique_topics']} topics)",
                    completed=True
                )

            except Exception as e:
                progress.update(
                    book_task,
                    description=f"  ❌ {book['title'][:40]} - Error: {str(e)[:30]}",
                    completed=True
                )
                console.print(f"[red]Error processing {book['title']}: {e}[/red]")

            progress.advance(overall_task)

    # Show results
    console.print("\n[bold green]✅ TOPIC EXTRACTION COMPLETE![/bold green]\n")
    console.print("[bold]Overall Statistics:[/bold]")
    console.print(f"  Books processed: {overall_stats['books_processed']}/{len(books_without_topics)}")
    console.print(f"  Total chunks: {overall_stats['total_chunks']:,}")
    console.print(f"  Topics extracted: {overall_stats['total_topics']:,}")
    console.print(f"  Average topics per book: {overall_stats['total_unique_topics'] / overall_stats['books_processed']:.1f}")

    # Show database stats
    console.print("\n[bold]Updated Database Statistics:[/bold]")
    with get_db() as db:
        db_stats = db.get_stats()
        console.print(f"  Total books: {db_stats['total_books']}")
        console.print(f"  Total chunks: {db_stats['total_chunks']:,}")
        console.print(f"  Total topics: {db_stats['total_topics']:,}")

        # Books with topics
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT book_id)
            FROM chunks c
            WHERE EXISTS (SELECT 1 FROM chunk_topics ct WHERE ct.chunk_id = c.id)
        ''')
        books_with_topics = cursor.fetchone()[0]

        console.print(f"  Books with topics: {books_with_topics}/{db_stats['total_books']}")

    console.print("\n[bold cyan]📊 Next step: Rebuild the graph[/bold cyan]")
    console.print("[dim]Run: python scripts/build_graph.py[/dim]\n")


if __name__ == "__main__":
    main()
