"""Topic search CLI - Search topics by keyword or similarity"""

import click

from src.database.operations import get_db
from src.database.vector_store import VectorStore
from src.embeddings.embedding_model import EmbeddingModel
from src.graph.canonical_resolve import (
    TOPIC_CANONICAL_MAP_VIEW,
    cluster_frequency,
    ensure_canonical_views,
    resolve_canonical,
)
from src.utils.xml_formatter import XMLFormatter
from src.config import DATABASE_PATH


@click.group()
def cli():
    """Topic search commands"""
    pass


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Number of results to return")
@click.option("--min-frequency", default=1, help="Minimum topic frequency")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def keyword(query, limit, min_frequency, format):
    """Search topics by keyword (SQL LIKE)"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Search topics. The LIKE runs against EVERY label - canonical AND
        # alias - and each hit resolves to its canonical topic (via the shared
        # canonical-resolution views), so a topic merged under a different
        # wording is still findable by its alias's words. Frequency is the
        # cluster total (canonical + all aliases), and the same cluster is
        # never listed twice even if several of its wordings match.
        ensure_canonical_views(cursor)
        cursor.execute(f"""
            WITH matched AS (
                SELECT DISTINCT m.canonical_id
                FROM topics t
                JOIN {TOPIC_CANONICAL_MAP_VIEW} m ON m.topic_id = t.id
                WHERE t.label LIKE ?
            )
            SELECT c.id, c.label, SUM(f.frequency) AS cluster_freq
            FROM matched
            JOIN topics c ON c.id = matched.canonical_id
            JOIN {TOPIC_CANONICAL_MAP_VIEW} fm ON fm.canonical_id = matched.canonical_id
            JOIN topics f ON f.id = fm.topic_id
            GROUP BY c.id, c.label
            HAVING SUM(f.frequency) >= ?
            ORDER BY cluster_freq DESC
            LIMIT ?
        """, (f"%{query}%", min_frequency, limit))

        results = cursor.fetchall()

        if not results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"No topics found matching '{query}'", code="NO_RESULTS"))
            else:
                click.echo(f"No topics found matching '{query}'")
            return

        # Get novelty scores
        topics = []
        for topic_id, label, frequency in results:
            cursor.execute("SELECT mean_knn_distance FROM novelty_scores WHERE topic_id = ?", (topic_id,))
            novelty_result = cursor.fetchone()
            novelty = novelty_result[0] if novelty_result else None

            topics.append({
                "id": topic_id,
                "label": label,
                "frequency": frequency,
                "novelty": novelty
            })

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_topics(topics, total=len(results)))
        else:
            click.echo(f"\nFound {len(results)} topics matching '{query}':\n")
            for i, topic in enumerate(topics, 1):
                novelty_str = f"{topic['novelty']:.4f}" if topic['novelty'] else "N/A"
                click.echo(f"{i:2d}. {topic['label']}")
                click.echo(f"    Frequency: {topic['frequency']}, Novelty: {novelty_str}")


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=10, help="Number of results to return")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def similar(query, limit, format):
    """Search topics by semantic similarity (embedding-based)"""

    # Initialize embedding model and vector store
    model = EmbeddingModel()
    store = VectorStore(DATABASE_PATH)

    # Search
    results = store.search_by_text(query, k=limit, model=model)

    if not results:
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_error(f"No topics found similar to '{query}'", code="NO_RESULTS"))
        else:
            click.echo(f"No topics found similar to '{query}'")
        return

    # Get additional topic data. Vector hits may include alias topics (the
    # sqlite-vec index is pruned best-effort after a merge, so it can be
    # stale) - resolve every hit to its canonical topic, drop duplicates
    # (nearest wording wins), and report the cluster-total frequency.
    with get_db() as db:
        cursor = db.conn.cursor()

        topics = []
        seen_canonical = set()
        for topic_id, label, distance in results:
            canonical_id = resolve_canonical(cursor, topic_id)
            if canonical_id is None:
                canonical_id = topic_id
            if canonical_id in seen_canonical:
                continue
            seen_canonical.add(canonical_id)

            if canonical_id != topic_id:
                row = cursor.execute(
                    "SELECT label FROM topics WHERE id = ?", (canonical_id,)
                ).fetchone()
                if row:
                    label = row[0]

            frequency = cluster_frequency(cursor, canonical_id) or 1

            cursor.execute("SELECT mean_knn_distance FROM novelty_scores WHERE topic_id = ?", (canonical_id,))
            novelty_result = cursor.fetchone()
            novelty = novelty_result[0] if novelty_result else None

            topics.append({
                "id": canonical_id,
                "label": label,
                "frequency": frequency,
                "novelty": novelty,
                "similarity_distance": distance
            })

    # Output
    if format == "xml":
        formatter = XMLFormatter()
        click.echo(formatter.format_topics(topics, total=len(topics)))
    else:
        click.echo(f"\nTop {len(topics)} topics similar to '{query}':\n")
        for i, topic in enumerate(topics, 1):
            novelty_str = f"{topic['novelty']:.4f}" if topic['novelty'] else "N/A"
            click.echo(f"{i:2d}. {topic['label']}")
            click.echo(f"    Distance: {topic['similarity_distance']:.4f}, Frequency: {topic['frequency']}, Novelty: {novelty_str}")


@cli.command()
@click.option("--limit", "-n", default=10, help="Number of topics to return")
@click.option("--percentile", "-p", default=90, help="Novelty percentile threshold (0-100)")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def novel(limit, percentile, format):
    """Find most novel/underexplored topics"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get novelty threshold
        cursor.execute("SELECT mean_knn_distance FROM novelty_scores ORDER BY mean_knn_distance")
        all_scores = [row[0] for row in cursor.fetchall()]

        if not all_scores:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error("No novelty scores found", code="NO_DATA"))
            else:
                click.echo("No novelty scores found")
            return

        import numpy as np
        threshold = np.percentile(all_scores, percentile)

        # Get novel topics
        cursor.execute("""
            SELECT t.id, t.label, t.frequency, ns.mean_knn_distance
            FROM topics t
            JOIN novelty_scores ns ON t.id = ns.topic_id
            WHERE t.canonical_id IS NULL
            AND ns.mean_knn_distance >= ?
            ORDER BY ns.mean_knn_distance DESC
            LIMIT ?
        """, (threshold, limit))

        results = cursor.fetchall()

        # frequency reported as the cluster total (canonical + aliases) so a
        # merged idea isn't under-counted by its canonical row alone
        topics = [
            {
                "id": topic_id,
                "label": label,
                "frequency": cluster_frequency(cursor, topic_id) or frequency,
                "novelty": novelty
            }
            for topic_id, label, frequency, novelty in results
        ]

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_topics(topics, total=len(results)))
        else:
            click.echo(f"\nTop {len(results)} novel topics (≥{percentile}th percentile, threshold: {threshold:.4f}):\n")
            for i, topic in enumerate(topics, 1):
                click.echo(f"{i:2d}. {topic['label']}")
                click.echo(f"    Novelty: {topic['novelty']:.4f}, Frequency: {topic['frequency']}")


@cli.command()
@click.option("--limit", "-n", default=10, help="Number of topics to return")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def hubs(limit, format):
    """Find hub topics (most connected)"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get topics with most edges
        cursor.execute("""
            SELECT t.id, t.label, t.frequency, COUNT(*) as degree
            FROM topics t
            JOIN (
                SELECT topic_a_id as topic_id FROM topic_edges
                UNION ALL
                SELECT topic_b_id as topic_id FROM topic_edges
            ) e ON t.id = e.topic_id
            WHERE t.canonical_id IS NULL
            GROUP BY t.id
            ORDER BY degree DESC
            LIMIT ?
        """, (limit,))

        results = cursor.fetchall()

        if not results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error("No hub topics found", code="NO_DATA"))
            else:
                click.echo("No hub topics found")
            return

        # Get novelty scores (frequency = cluster total, see `novel`)
        topics = []
        for topic_id, label, frequency, degree in results:
            cursor.execute("SELECT mean_knn_distance FROM novelty_scores WHERE topic_id = ?", (topic_id,))
            novelty_result = cursor.fetchone()
            novelty = novelty_result[0] if novelty_result else None

            topics.append({
                "id": topic_id,
                "label": label,
                "frequency": cluster_frequency(cursor, topic_id) or frequency,
                "novelty": novelty,
                "degree": degree
            })

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(formatter.format_topics(topics, total=len(results)))
        else:
            click.echo(f"\nTop {len(results)} hub topics (most connected):\n")
            for i, topic in enumerate(topics, 1):
                novelty_str = f"{topic['novelty']:.4f}" if topic['novelty'] else "N/A"
                click.echo(f"{i:2d}. {topic['label']}")
                click.echo(f"    Degree: {topic['degree']}, Frequency: {topic['frequency']}, Novelty: {novelty_str}")


if __name__ == "__main__":
    cli()
