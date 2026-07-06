"""Tree navigator CLI - Browse hierarchical topic tree"""

import click
from collections import defaultdict

from src.database.operations import get_db
from src.utils.xml_formatter import XMLFormatter


@click.group()
def cli():
    """Hierarchical topic tree navigation commands"""
    pass


@cli.command()
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def levels(format):
    """Show hierarchy level statistics"""

    with get_db() as db:
        cursor = db.conn.cursor()

        cursor.execute("""
            SELECT level, COUNT(*) as topic_count, COUNT(DISTINCT community_id) as communities
            FROM topic_hierarchy
            GROUP BY level
            ORDER BY level
        """)

        results = cursor.fetchall()

        if not results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error("No hierarchy data found", code="NO_DATA"))
            else:
                click.echo("No hierarchy data found")
            return

        if format == "xml":
            click.echo("<hierarchy_levels>")
            for level, topic_count, communities in results:
                click.echo(f'  <level id="{level}">')
                click.echo(f'    <topics>{topic_count}</topics>')
                click.echo(f'    <communities>{communities}</communities>')
                click.echo(f'    <avg_size>{topic_count/communities:.2f}</avg_size>')
                click.echo('  </level>')
            click.echo("</hierarchy_levels>")
        else:
            click.echo("\nHierarchy Level Statistics:\n")
            for level, topic_count, communities in results:
                avg_size = topic_count / communities if communities > 0 else 0
                click.echo(f"Level {level}:")
                click.echo(f"  Topics: {topic_count}")
                click.echo(f"  Communities: {communities}")
                click.echo(f"  Avg size: {avg_size:.2f}")


@cli.command()
@click.argument("level", type=int, default=0)
@click.option("--limit", "-n", default=10, help="Number of communities to show")
@click.option("--min-size", "-s", default=1, help="Minimum community size")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def communities(level, limit, min_size, format):
    """List communities at a specific hierarchy level"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get communities at this level
        cursor.execute("""
            SELECT community_id, COUNT(*) as size
            FROM topic_hierarchy
            WHERE level = ?
            GROUP BY community_id
            HAVING COUNT(*) >= ?
            ORDER BY size DESC
            LIMIT ?
        """, (level, min_size, limit))

        community_results = cursor.fetchall()

        if not community_results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"No communities found at level {level}", code="NO_RESULTS"))
            else:
                click.echo(f"No communities found at level {level} with size >= {min_size}")
            return

        # Get sample topics for each community
        communities_data = []
        for community_id, size in community_results:
            cursor.execute("""
                SELECT t.id, t.label, t.frequency
                FROM topics t
                JOIN topic_hierarchy th ON t.id = th.topic_id
                WHERE th.community_id = ?
                ORDER BY t.frequency DESC
                LIMIT 5
            """, (community_id,))

            sample_topics = [{"id": tid, "label": label, "frequency": freq} for tid, label, freq in cursor.fetchall()]

            communities_data.append({
                "community_id": community_id,
                "level": level,
                "size": size,
                "topics": [t["label"] for t in sample_topics]
            })

        # Output
        if format == "xml":
            formatter = XMLFormatter()
            click.echo(f'<communities level="{level}" count="{len(communities_data)}">')
            for comm in communities_data:
                click.echo(formatter.format_tree_node(comm))
            click.echo('</communities>')
        else:
            click.echo(f"\nTop {len(communities_data)} communities at level {level}:\n")
            for i, comm in enumerate(communities_data, 1):
                click.echo(f"{i}. Community {comm['community_id']} ({comm['size']} topics)")
                click.echo(f"   Sample topics: {', '.join(comm['topics'][:5])}")
                if len(comm['topics']) > 5:
                    click.echo(f"   ... and {comm['size'] - 5} more")
                click.echo()


@cli.command()
@click.argument("community_id")
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def expand(community_id, format):
    """Expand a community to show all topics and sub-communities"""

    with get_db() as db:
        cursor = db.conn.cursor()

        # Get community level
        cursor.execute("""
            SELECT level, COUNT(*) as size
            FROM topic_hierarchy
            WHERE community_id = ?
            GROUP BY level
        """, (community_id,))

        comm_result = cursor.fetchone()

        if not comm_result:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"Community '{community_id}' not found", code="NOT_FOUND"))
            else:
                click.echo(f"Error: Community '{community_id}' not found")
            return

        level, size = comm_result

        # Get all topics in this community
        cursor.execute("""
            SELECT t.id, t.label, t.frequency
            FROM topics t
            JOIN topic_hierarchy th ON t.id = th.topic_id
            WHERE th.community_id = ?
            ORDER BY t.frequency DESC
        """, (community_id,))

        all_topics = [{"id": tid, "label": label, "frequency": freq} for tid, label, freq in cursor.fetchall()]

        # Check for child communities (next level down)
        cursor.execute("""
            SELECT DISTINCT community_id, COUNT(*) as size
            FROM topic_hierarchy
            WHERE level = ?
            AND topic_id IN (
                SELECT topic_id
                FROM topic_hierarchy
                WHERE community_id = ?
            )
            GROUP BY community_id
        """, (level + 1, community_id))

        child_communities = cursor.fetchall()

        # Output
        if format == "xml":
            click.echo(f'<community id="{community_id}" level="{level}" size="{size}">')
            click.echo('  <topics>')
            for topic in all_topics:
                click.echo(f'    <topic id="{topic["id"]}" frequency="{topic["frequency"]}">{topic["label"]}</topic>')
            click.echo('  </topics>')

            if child_communities:
                click.echo('  <children>')
                for child_id, child_size in child_communities:
                    click.echo(f'    <community id="{child_id}" size="{child_size}" />')
                click.echo('  </children>')

            click.echo('</community>')
        else:
            click.echo(f"\nCommunity {community_id} (Level {level}, {size} topics):\n")
            click.echo("Topics:")
            for topic in all_topics:
                click.echo(f"  - {topic['label']} (freq: {topic['frequency']})")

            if child_communities:
                click.echo(f"\nChild communities ({len(child_communities)}):")
                for child_id, child_size in child_communities:
                    click.echo(f"  - {child_id} ({child_size} topics)")


@cli.command()
@click.argument("topic_id", type=int)
@click.option("--format", "-f", type=click.Choice(["xml", "plain"]), default="xml", help="Output format")
def path(topic_id, format):
    """Show hierarchical path for a topic (all levels it appears in)"""

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

        # Get hierarchy path
        cursor.execute("""
            SELECT level, community_id
            FROM topic_hierarchy
            WHERE topic_id = ?
            ORDER BY level
        """, (topic_id,))

        path_results = cursor.fetchall()

        if not path_results:
            if format == "xml":
                formatter = XMLFormatter()
                click.echo(formatter.format_error(f"No hierarchy found for topic '{topic_label}'", code="NO_DATA"))
            else:
                click.echo(f"No hierarchy data found for topic '{topic_label}'")
            return

        # Output
        if format == "xml":
            click.echo(f'<topic_path id="{topic_id}" label="{topic_label}">')
            for level, community_id in path_results:
                click.echo(f'  <level id="{level}" community="{community_id}" />')
            click.echo('</topic_path>')
        else:
            click.echo(f"\nHierarchical path for '{topic_label}' (ID: {topic_id}):\n")
            for level, community_id in path_results:
                click.echo(f"Level {level}: Community {community_id}")


if __name__ == "__main__":
    cli()
