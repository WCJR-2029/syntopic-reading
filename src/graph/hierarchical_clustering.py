"""Hierarchical topic clustering using Leiden algorithm"""

import igraph as ig
import leidenalg as la
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class HierarchicalClustering:
    """Build hierarchical topic tree using Leiden algorithm"""

    def __init__(self, resolution: float = 1.0):
        """
        Initialize hierarchical clustering

        Args:
            resolution: Leiden resolution parameter (higher = more communities)
        """
        self.resolution = resolution
        self.hierarchy = {}  # level -> {community_id -> [topic_ids]}
        self.topic_hierarchy = {}  # topic_id -> {level, parent_id, community_id}

    def cluster_recursive(
        self,
        graph: ig.Graph,
        parent_community: Optional[int] = None,
        level: int = 0,
        max_levels: int = 5
    ) -> Dict:
        """
        Recursively cluster graph using Leiden algorithm

        Args:
            graph: igraph.Graph instance
            parent_community: Parent community ID
            level: Current hierarchy level
            max_levels: Maximum levels to recurse

        Returns:
            Hierarchy statistics
        """
        if level >= max_levels or graph.vcount() == 0:
            return {}

        print(f"  Level {level}: {graph.vcount()} vertices, {graph.ecount()} edges")

        # Run Leiden clustering
        try:
            partition = la.find_partition(
                graph,
                la.ModularityVertexPartition,
                weights=graph.es["weight"] if "weight" in graph.es.attributes() else None,
                resolution_parameter=self.resolution
            )
        except:
            # If Leiden fails (e.g., disconnected graph), use label propagation
            partition = graph.community_label_propagation(
                weights=graph.es["weight"] if "weight" in graph.es.attributes() else None
            )

        n_communities = len(partition)
        modularity = partition.modularity if hasattr(partition, 'modularity') else 0

        print(f"    Found {n_communities} communities (modularity: {modularity:.4f})")

        # Store hierarchy for this level
        if level not in self.hierarchy:
            self.hierarchy[level] = {}

        # Process each community
        for comm_idx, community in enumerate(partition):
            if len(community) == 0:
                continue

            # Global community ID
            community_id = f"{level}_{comm_idx}"
            self.hierarchy[level][community_id] = []

            # Store topic assignments
            for vertex_idx in community:
                topic_id = graph.vs[vertex_idx]["topic_id"]
                self.hierarchy[level][community_id].append(topic_id)

                # Store topic hierarchy info
                self.topic_hierarchy[topic_id] = {
                    'level': level,
                    'parent_community': parent_community,
                    'community_id': community_id,
                }

            # Recurse on subgraph if community is large enough
            if len(community) > 5 and level < max_levels - 1:
                subgraph = graph.subgraph(community)

                # Only recurse if subgraph has edges
                if subgraph.ecount() > 0:
                    self.cluster_recursive(
                        subgraph,
                        parent_community=community_id,
                        level=level + 1,
                        max_levels=max_levels
                    )

        stats = {
            'level': level,
            'communities': n_communities,
            'modularity': modularity,
        }

        return stats

    def get_hierarchy_summary(self) -> Dict:
        """
        Get summary statistics for hierarchy

        Returns:
            Dictionary of hierarchy statistics
        """
        stats = {
            'total_levels': len(self.hierarchy),
            'level_stats': {},
        }

        for level, communities in sorted(self.hierarchy.items()):
            level_stats = {
                'communities': len(communities),
                'topics': sum(len(topics) for topics in communities.values()),
                'avg_community_size': sum(len(topics) for topics in communities.values()) / len(communities) if communities else 0,
                'max_community_size': max((len(topics) for topics in communities.values()), default=0),
                'min_community_size': min((len(topics) for topics in communities.values()), default=0),
            }
            stats['level_stats'][level] = level_stats

        return stats

    def get_top_communities(self, level: int = 0, k: int = 10) -> List[Tuple[str, int, List[str]]]:
        """
        Get top k largest communities at given level

        Args:
            level: Hierarchy level
            k: Number of communities to return

        Returns:
            List of (community_id, size, sample_topics) tuples
        """
        if level not in self.hierarchy:
            return []

        communities = self.hierarchy[level]

        # Sort by size
        sorted_communities = sorted(
            communities.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )[:k]

        return [(comm_id, len(topics), topics[:5]) for comm_id, topics in sorted_communities]


def build_hierarchical_clustering(db_connection, graph_builder, max_levels: int = 5, resolution: float = 1.0) -> Tuple[HierarchicalClustering, Dict]:
    """
    Build hierarchical clustering from topic graph

    Args:
        db_connection: Database connection
        graph_builder: TopicGraph instance
        max_levels: Maximum hierarchy levels
        resolution: Leiden resolution parameter

    Returns:
        (HierarchicalClustering instance, statistics dictionary)
    """
    clustering = HierarchicalClustering(resolution=resolution)

    print("\nBuilding hierarchical clustering...")

    # Run recursive clustering
    clustering.cluster_recursive(graph_builder.graph, level=0, max_levels=max_levels)

    # Get summary statistics
    stats = clustering.get_hierarchy_summary()

    # Store in database
    print("\nStoring hierarchy in database...")
    cursor = db_connection.conn.cursor()
    cursor.execute("DELETE FROM topic_hierarchy")  # Clear existing

    for topic_id, info in clustering.topic_hierarchy.items():
        cursor.execute("""
            INSERT INTO topic_hierarchy (topic_id, level, parent_id, community_id)
            VALUES (?, ?, ?, ?)
        """, (
            topic_id,
            info['level'],
            None,  # parent_id not implemented yet
            info['community_id']
        ))

    db_connection.conn.commit()
    print(f"  Stored {len(clustering.topic_hierarchy)} topic hierarchy entries")

    return clustering, stats


if __name__ == "__main__":
    # Test hierarchical clustering
    from src.database.operations import get_db
    from src.graph.topic_graph import build_topic_graph

    print("\nTesting Hierarchical Clustering")
    print("=" * 60)

    with get_db() as db:
        # Build graph first
        print("\nBuilding topic graph...")
        graph_builder, graph_stats = build_topic_graph(db)

        # Build clustering
        clustering, stats = build_hierarchical_clustering(db, graph_builder, max_levels=5, resolution=1.0)

        print("\n" + "=" * 60)
        print("Hierarchical Clustering Complete!")
        print("=" * 60)
        print(f"Total levels: {stats['total_levels']}")

        for level, level_stats in sorted(stats['level_stats'].items()):
            print(f"\nLevel {level}:")
            print(f"  Communities: {level_stats['communities']}")
            print(f"  Topics: {level_stats['topics']}")
            print(f"  Avg community size: {level_stats['avg_community_size']:.2f}")
            print(f"  Max community size: {level_stats['max_community_size']}")

        # Show top communities at level 0
        print("\nTop 10 communities at level 0:")
        top_communities = clustering.get_top_communities(level=0, k=10)

        cursor = db.conn.cursor()
        for i, (comm_id, size, topic_ids) in enumerate(top_communities, 1):
            # Get topic labels
            labels = []
            for topic_id in topic_ids:
                cursor.execute("SELECT label FROM topics WHERE id = ?", (topic_id,))
                result = cursor.fetchone()
                if result:
                    labels.append(result[0])

            print(f"\n  {i}. Community {comm_id} ({size} topics):")
            print(f"     Sample topics: {', '.join(labels)}")
