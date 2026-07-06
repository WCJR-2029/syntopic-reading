"""Build complete topic graph: Co-occurrence → Graph → Clustering → Novelty"""

from rich.console import Console

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.database.operations import get_db
from src.graph.cooccurrence import build_cooccurrence_edges
from src.graph.topic_graph import build_topic_graph
from src.graph.hierarchical_clustering import build_hierarchical_clustering
from src.graph.novelty_scoring import calculate_novelty_scores

console = Console()


def main():
    """Run complete Phase 3 pipeline"""
    console.print("\n[bold cyan]Phase 3: Graph Construction Pipeline[/bold cyan]\n")

    with get_db() as db:
        # Step 1: Co-occurrence Analysis
        console.print("[bold]Step 1/4: Building co-occurrence edges...[/bold]")
        cooccur_stats = build_cooccurrence_edges(db, min_cooccurrence=2, min_pmi=0.0)

        console.print("\n[green]✓ Co-occurrence Analysis Complete[/green]")
        console.print(f"  Total topic pairs: {cooccur_stats['total_topic_pairs']}")
        console.print(f"  Filtered pairs: {cooccur_stats['filtered_pairs']}")
        console.print(f"  Average PMI: {cooccur_stats['avg_pmi']:.4f}")
        console.print(f"  Edges stored: {cooccur_stats['edges_stored']}\n")

        # Step 2: Topic Graph Construction
        console.print("[bold]Step 2/4: Building topic graph...[/bold]")
        graph_builder, graph_stats = build_topic_graph(db)

        console.print("\n[green]✓ Topic Graph Construction Complete[/green]")
        console.print(f"  Vertices: {graph_stats['vertices']}")
        console.print(f"  Edges: {graph_stats['edges']}")
        console.print(f"  Density: {graph_stats['density']:.6f}")
        console.print(f"  Average degree: {graph_stats['avg_degree']:.2f}")
        console.print(f"  Connected components: {graph_stats['connected_components']}\n")

        # Step 3: Hierarchical Clustering
        console.print("[bold]Step 3/4: Building hierarchical clustering...[/bold]")
        clustering, cluster_stats = build_hierarchical_clustering(
            db, graph_builder, max_levels=5, resolution=1.0
        )

        console.print("\n[green]✓ Hierarchical Clustering Complete[/green]")
        console.print(f"  Total levels: {cluster_stats['total_levels']}")
        for level, stats in sorted(cluster_stats['level_stats'].items()):
            console.print(f"  Level {level}: {stats['communities']} communities, {stats['topics']} topics")

        # Step 4: Novelty Scoring
        console.print("\n[bold]Step 4/4: Calculating novelty scores...[/bold]")
        novelty_stats = calculate_novelty_scores(db, k=10)

        console.print("\n[green]✓ Novelty Scoring Complete[/green]")
        console.print(f"  Topics scored: {novelty_stats['total_topics']}")
        if novelty_stats['total_topics'] > 0:
            console.print(f"  Mean novelty: {novelty_stats['mean_novelty']:.4f}")
            console.print(f"  Novelty range: [{novelty_stats['min_novelty']:.4f}, {novelty_stats['max_novelty']:.4f}]\n")
        else:
            console.print("  [yellow]No embeddings found - run embedding generation first[/yellow]\n")

    # Final Summary
    console.print("=" * 60)
    console.print("[bold green]Phase 3 Complete![/bold green]")
    console.print("=" * 60)
    console.print(f"Topic Graph: {graph_stats['vertices']} vertices, {graph_stats['edges']} edges")
    console.print(f"Hierarchy: {cluster_stats['total_levels']} levels")
    console.print(f"Novelty: {novelty_stats['total_topics']} topics scored")
    console.print("\n[bold]Next: Phase 4 - CLI Tools for Claude Code Agents[/bold]")


if __name__ == "__main__":
    main()
