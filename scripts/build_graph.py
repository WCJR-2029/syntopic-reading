"""Build complete topic graph: Canonicalize (default on) → Co-occurrence → Graph → Clustering → Novelty"""

import argparse

from rich.console import Console

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.database.operations import get_db
from src.graph.canonicalize import canonicalize_topics
from src.graph.cooccurrence import build_cooccurrence_edges
from src.graph.topic_graph import build_topic_graph
from src.graph.hierarchical_clustering import build_hierarchical_clustering
from src.graph.novelty_scoring import calculate_novelty_scores

console = Console()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the cross-book topic graph (PMI co-occurrence + Leiden clustering)."
    )
    parser.add_argument(
        "--no-canonicalize", action="store_true",
        help="Skip the topic-canonicalization pass that runs by DEFAULT before "
             "the graph build. By default, semantically-equivalent topic labels "
             "(embedding cosine >= --merge-threshold) merge into one canonical "
             "topic each, so the same idea worded differently across books "
             "becomes ONE graph node - this is what makes real cross-book edges "
             "form. The pass writes only canonical_id values (reversible - no "
             "rows or chunk links are deleted; re-running is safe/idempotent) "
             "and costs seconds on small libraries, roughly 12 minutes on a "
             "very large (~225K-topic) one. Use this flag to opt out, e.g. to "
             "rebuild edges quickly without re-scanning topic similarity.")
    parser.add_argument(
        "--merge-threshold", type=float, default=0.90,
        help="Cosine similarity above which two topic labels merge "
             "(default 0.90: rewordings of the same idea; 0.85 is noticeably "
             "more aggressive and risks merging related-but-distinct topics).")
    parser.add_argument(
        "--merge-knn", type=int, default=10,
        help="Neighbourhood cap for the merge scan: each topic considers at "
             "most this many nearest neighbours (default 10). Bounds the "
             "pass at ~O(N*K) memory regardless of library size.")
    parser.add_argument(
        "--merge-max-cluster", type=int, default=0,
        help="Optional hard cap on merged-cluster size (0 = unlimited). "
             "Guards against transitive over-merge chains at aggressive "
             "thresholds; when set, re-runs may perform deferred merges.")
    parser.add_argument(
        "--min-cooccurrence", type=int, default=2,
        help="Minimum chunk co-occurrence count for a PMI edge (default 2).")
    parser.add_argument(
        "--min-pmi", type=float, default=0.0,
        help="Minimum PMI score for an edge (default 0.0).")
    return parser.parse_args()


def main():
    """Run complete Phase 3 pipeline"""
    args = parse_args()
    console.print("\n[bold cyan]Phase 3: Graph Construction Pipeline[/bold cyan]\n")

    with get_db() as db:
        # Step 0 (default ON, opt out with --no-canonicalize): merge
        # near-duplicate topic labels so cross-book edges can form.
        if not args.no_canonicalize:
            console.print("[bold]Step 0: Canonicalizing near-duplicate topics...[/bold]")
            canon_stats = canonicalize_topics(
                db,
                merge_threshold=args.merge_threshold,
                knn=args.merge_knn,
                max_cluster_size=args.merge_max_cluster or None,
            )
            console.print("\n[green]✓ Topic Canonicalization Complete[/green]")
            console.print(f"  Topics scanned: {canon_stats['topics_scanned']}")
            console.print(f"  Clusters merged: {canon_stats['clusters_merged']}")
            console.print(f"  Aliases created: {canon_stats['aliases_created']}")
            console.print(f"  Canonical topics remaining: {canon_stats['canonical_topics_after']}")
            if canon_stats['sample_clusters']:
                console.print("  Sample merged clusters (canonical first):")
                for cluster in canon_stats['sample_clusters'][:8]:
                    console.print(f"    • {' | '.join(cluster)}")
            console.print()

        # Step 1: Co-occurrence Analysis
        console.print("[bold]Step 1/4: Building co-occurrence edges...[/bold]")
        cooccur_stats = build_cooccurrence_edges(
            db, min_cooccurrence=args.min_cooccurrence, min_pmi=args.min_pmi
        )

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
    console.print("[bold green]✓ Topic graph built[/bold green]")
    console.print("=" * 60)
    console.print(f"Topic Graph: {graph_stats['vertices']} vertices, {graph_stats['edges']} edges")
    console.print(f"Hierarchy: {cluster_stats['total_levels']} levels")
    console.print(f"Novelty: {novelty_stats['total_topics']} topics scored")
    # Honest signal when the graph is sparse (expected on small/varied libraries):
    # topic labels are near-unique per book, so PMI finds little to co-occur. Trail
    # selection falls back to novelty + embeddings and still works well.
    v, e = graph_stats['vertices'], graph_stats['edges']
    if v and e < v * 0.05:
        console.print(
            "\n[yellow]Note:[/yellow] this topic graph is sparse (few cross-book edges) - "
            "expected on a small or highly varied library. Trail selection will fall back to "
            "novelty ranking + embedding similarity, which still produces strong trails. The "
            "graph's role grows as your library does.")
        if args.no_canonicalize:
            console.print(
                "[yellow]Tip:[/yellow] you passed [bold]--no-canonicalize[/bold] - re-run "
                "without it so near-duplicate topic labels (\"nature of happiness\" / "
                "\"nature of true happiness\") merge into single nodes. On small libraries "
                "that merge is usually what unlocks real cross-book edges.")
        else:
            console.print(
                "[yellow]Tip:[/yellow] canonicalization already ran (it's on by default). "
                "If your books genuinely share themes, a slightly more aggressive merge "
                "can help: [bold]--merge-threshold 0.85[/bold] (best paired with "
                "[bold]--merge-max-cluster 6[/bold] to keep transitive chains in check).")
    console.print(
        "\n[bold]Next:[/bold] generate a trail, e.g.\n"
        "  python -m src.agent.trail_generator --idea \"how should power be limited?\"")


if __name__ == "__main__":
    main()
