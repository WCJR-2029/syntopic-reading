"""Chunk browser CLI - View chunks with topic context"""

import click

from src.database.operations import get_db
from src.utils.xml_formatter import XMLFormatter


@click.group()
def cli():
    """Chunk browser commands"""
    pass


@cli.command()
@click.argument("topic_id", type=int)
@click.option("--limit", "-n", default=10, help="Number of chunks to return")
@click.option("--context-window", "-c", default=0, help="Number of surrounding chunks to include")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def by_topic(topic_id, limit, context_window, format):
    """Browse chunks containing a specific topic"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get topic info
        cursor.execute("SELECT label FROM topics WHERE id = ?", (topic_id,))
        topic_result = cursor.fetchone()

        if not topic_result:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"Topic ID {topic_id} not found", code="NOT_FOUND"))
            else:
                click.echo(f"Error: Topic ID {topic_id} not found")
            return

        topic_label = topic_result[0]

        # Get chunks with this topic
        cursor.execute("""
            SELECT c.id, c.book_id, c.chunk_index, c.text, c.word_count, b.title
            FROM chunks c
            JOIN chunk_topics ct ON c.id = ct.chunk_id
            JOIN books b ON c.book_id = b.id
            WHERE ct.topic_id = ?
            ORDER BY b.title, c.chunk_index
            LIMIT ?
        """, (topic_id, limit))

        chunk_results = cursor.fetchall()

        if not chunk_results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"No chunks found for topic '{topic_label}'", code="NO_RESULTS"))
            else:
                click.echo(f"No chunks found for topic '{topic_label}'")
            return

        # Get all topics for each chunk
        chunks = []
        for chunk_id, book_id, chunk_index, text, word_count, book_title in chunk_results:
            cursor.execute("""
                SELECT t.label
                FROM topics t
                JOIN chunk_topics ct ON t.id = ct.topic_id
                WHERE ct.chunk_id = ?
                ORDER BY t.label
            """, (chunk_id,))

            topics = [row[0] for row in cursor.fetchall()]

            chunks.append({
                "id": chunk_id,
                "book_id": book_id,
                "book_title": book_title,
                "chunk_index": chunk_index,
                "word_count": word_count,
                "text": text,
                "topics": topics
            })

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_chunks(chunks))
        else:
            click.echo(f"\nShowing {len(chunks)} chunks for topic '{topic_label}':\n")
            for i, chunk in enumerate(chunks, 1):
                click.echo(f"{i}. {chunk['book_title']} - Chunk {chunk['chunk_index']}")
                click.echo(f"   Topics: {', '.join(chunk['topics'])}")
                click.echo(f"   Text: {chunk['text'][:200]}...")
                click.echo()


@cli.command()
@click.argument("chunk_id", type=int)
@click.option("--show-related", "-r", is_flag=True, help="Show related topics via graph edges")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def view(chunk_id, show_related, format):
    """View a specific chunk with full context"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get chunk
        cursor.execute("""
            SELECT c.id, c.book_id, c.chunk_index, c.text, c.word_count, b.title, b.author
            FROM chunks c
            JOIN books b ON c.book_id = b.id
            WHERE c.id = ?
        """, (chunk_id,))

        chunk_result = cursor.fetchone()

        if not chunk_result:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"Chunk ID {chunk_id} not found", code="NOT_FOUND"))
            else:
                click.echo(f"Error: Chunk ID {chunk_id} not found")
            return

        chunk_id, book_id, chunk_index, text, word_count, book_title, author = chunk_result

        # Get topics
        cursor.execute("""
            SELECT t.id, t.label, t.frequency
            FROM topics t
            JOIN chunk_topics ct ON t.id = ct.topic_id
            WHERE ct.chunk_id = ?
            ORDER BY t.frequency DESC
        """, (chunk_id,))

        topics = [{"id": tid, "label": label, "frequency": freq} for tid, label, freq in cursor.fetchall()]

        chunk_data = {
            "id": chunk_id,
            "book_id": book_id,
            "book_title": book_title,
            "author": author,
            "chunk_index": chunk_index,
            "word_count": word_count,
            "text": text,
            "topics": [t["label"] for t in topics]
        }

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_chunk(chunk_data))

            # Show related topics if requested
            if show_related and topics:
                related_topics = []
                for topic in topics:
                    # Get connected topics
                    cursor.execute("""
                        SELECT t.id, t.label, e.pmi_score, e.weight
                        FROM topics t
                        JOIN topic_edges e ON (
                            (e.topic_a_id = ? AND t.id = e.topic_b_id) OR
                            (e.topic_b_id = ? AND t.id = e.topic_a_id)
                        )
                        ORDER BY e.weight DESC
                        LIMIT 5
                    """, (topic["id"], topic["id"]))

                    for tid, label, pmi, weight in cursor.fetchall():
                        related_topics.append({
                            "id": tid,
                            "label": label,
                            "pmi": pmi,
                            "weight": weight,
                            "source_topic": topic["label"]
                        })

                if related_topics:
                    click.echo("\n<related_topics>")
                    for rt in related_topics:
                        click.echo(f'  <topic id="{rt["id"]}" source="{rt["source_topic"]}">')
                        click.echo(f'    <label>{rt["label"]}</label>')
                        click.echo(f'    <pmi>{rt["pmi"]:.4f}</pmi>')
                        click.echo(f'    <weight>{rt["weight"]:.4f}</weight>')
                        click.echo('  </topic>')
                    click.echo("</related_topics>")

        else:
            click.echo(f"\nChunk {chunk_id} from '{book_title}' by {author}")
            click.echo(f"Chunk index: {chunk_index}, Words: {word_count}\n")
            click.echo(f"Topics: {', '.join([t['label'] for t in topics])}\n")
            click.echo("Text:")
            click.echo(text)

            if show_related and topics:
                click.echo("\n--- Related Topics (via graph edges) ---\n")
                for topic in topics:
                    cursor.execute("""
                        SELECT t.label, e.pmi_score, e.weight
                        FROM topics t
                        JOIN topic_edges e ON (
                            (e.topic_a_id = ? AND t.id = e.topic_b_id) OR
                            (e.topic_b_id = ? AND t.id = e.topic_a_id)
                        )
                        ORDER BY e.weight DESC
                        LIMIT 3
                    """, (topic["id"], topic["id"]))

                    related = cursor.fetchall()
                    if related:
                        click.echo(f"Connected to '{topic['label']}':")
                        for label, pmi, weight in related:
                            click.echo(f"  - {label} (PMI: {pmi:.4f}, weight: {weight:.4f})")


@cli.command()
@click.argument("book_id", type=int)
@click.option("--start", "-s", default=0, help="Starting chunk index")
@click.option("--limit", "-n", default=10, help="Number of chunks to return")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def by_book(book_id, start, limit, format):
    """Browse chunks from a specific book"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get book info
        cursor.execute("SELECT title, author FROM books WHERE id = ?", (book_id,))
        book_result = cursor.fetchone()

        if not book_result:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"Book ID {book_id} not found", code="NOT_FOUND"))
            else:
                click.echo(f"Error: Book ID {book_id} not found")
            return

        book_title, author = book_result

        # Get chunks
        cursor.execute("""
            SELECT c.id, c.chunk_index, c.text, c.word_count
            FROM chunks c
            WHERE c.book_id = ?
            AND c.chunk_index >= ?
            ORDER BY c.chunk_index
            LIMIT ?
        """, (book_id, start, limit))

        chunk_results = cursor.fetchall()

        if not chunk_results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"No chunks found for book '{book_title}'", code="NO_RESULTS"))
            else:
                click.echo(f"No chunks found for book '{book_title}' starting at index {start}")
            return

        # Get topics for each chunk
        chunks = []
        for chunk_id, chunk_index, text, word_count in chunk_results:
            cursor.execute("""
                SELECT t.label
                FROM topics t
                JOIN chunk_topics ct ON t.id = ct.topic_id
                WHERE ct.chunk_id = ?
                ORDER BY t.label
            """, (chunk_id,))

            topics = [row[0] for row in cursor.fetchall()]

            chunks.append({
                "id": chunk_id,
                "book_id": book_id,
                "book_title": book_title,
                "chunk_index": chunk_index,
                "word_count": word_count,
                "text": text,
                "topics": topics
            })

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_chunks(chunks))
        else:
            click.echo(f"\nShowing chunks {start}-{start+len(chunks)-1} from '{book_title}' by {author}:\n")
            for chunk in chunks:
                click.echo(f"Chunk {chunk['chunk_index']} (ID: {chunk['id']})")
                click.echo(f"Topics: {', '.join(chunk['topics']) if chunk['topics'] else 'None'}")
                click.echo(f"Text: {chunk['text'][:200]}...")
                click.echo()


if __name__ == "__main__":
    cli()
