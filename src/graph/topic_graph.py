"""Topic graph construction using igraph"""

import igraph as ig
from typing import Dict, List, Tuple, Optional
import numpy as np


class TopicGraph:
    """Build and analyze topic graph from co-occurrence data"""

    def __init__(self):
        """Initialize topic graph"""
        self.graph = None
        self.topic_id_to_vertex = {}
        self.vertex_to_topic_id = {}

    def build_from_database(self, db_connection) -> ig.Graph:
        """
        Build igraph from database topic_edges table

        Args:
            db_connection: Database connection

        Returns:
            igraph.Graph instance
        """
        cursor = db_connection.conn.cursor()

        print("\nBuilding topic graph...")

        # Step 1: Get all canonical topics
        print("  Loading topics...")
        cursor.execute("""
            SELECT id, label, frequency
            FROM topics
            WHERE canonical_id IS NULL
            ORDER BY id
        """)
        topics = cursor.fetchall()

        # Create vertex mapping
        self.topic_id_to_vertex = {topic_id: i for i, (topic_id, _, _) in enumerate(topics)}
        self.vertex_to_topic_id = {i: topic_id for i, (topic_id, _, _) in enumerate(topics)}

        print(f"  Loaded {len(topics)} topics")

        # Step 2: Create graph with vertices
        self.graph = ig.Graph(n=len(topics), directed=False)

        # Add vertex attributes
        self.graph.vs["topic_id"] = [topic_id for topic_id, _, _ in topics]
        self.graph.vs["label"] = [label for _, label, _ in topics]
        self.graph.vs["frequency"] = [freq for _, _, freq in topics]

        # Step 3: Load edges
        print("  Loading edges...")
        cursor.execute("""
            SELECT topic_a_id, topic_b_id, cooccurrence_count, pmi_score, weight
            FROM topic_edges
        """)
        edges = cursor.fetchall()

        # Convert to vertex indices and add edges
        edge_list = []
        edge_weights = []
        edge_pmi = []
        edge_cooccur = []

        for topic_a, topic_b, cooccur, pmi, weight in edges:
            if topic_a in self.topic_id_to_vertex and topic_b in self.topic_id_to_vertex:
                v_a = self.topic_id_to_vertex[topic_a]
                v_b = self.topic_id_to_vertex[topic_b]

                edge_list.append((v_a, v_b))
                edge_weights.append(weight)
                edge_pmi.append(pmi)
                edge_cooccur.append(cooccur)

        self.graph.add_edges(edge_list)
        self.graph.es["weight"] = edge_weights
        self.graph.es["pmi"] = edge_pmi
        self.graph.es["cooccurrence"] = edge_cooccur

        print(f"  Added {len(edge_list)} edges")

        return self.graph

    def get_statistics(self) -> Dict:
        """
        Calculate graph statistics

        Returns:
            Dictionary of graph statistics
        """
        if self.graph is None:
            return {}

        stats = {
            'vertices': self.graph.vcount(),
            'edges': self.graph.ecount(),
            'density': self.graph.density(),
            'avg_degree': np.mean(self.graph.degree()),
            'max_degree': max(self.graph.degree()),
            'connected_components': len(self.graph.components()),
            'largest_component_size': max(self.graph.components().sizes()),
            'avg_clustering': self.graph.transitivity_avglocal_undirected(mode="zero"),
            'diameter': self.graph.diameter() if self.graph.is_connected() else None,
        }

        return stats

    def get_top_hubs(self, k: int = 10) -> List[Tuple[str, int, float]]:
        """
        Get top k hub topics by degree centrality

        Args:
            k: Number of hubs to return

        Returns:
            List of (topic_label, degree, betweenness_centrality) tuples
        """
        if self.graph is None:
            return []

        degrees = self.graph.degree()
        betweenness = self.graph.betweenness()

        # Get top k by degree
        top_indices = sorted(range(len(degrees)), key=lambda i: degrees[i], reverse=True)[:k]

        results = []
        for i in top_indices:
            label = self.graph.vs[i]["label"]
            degree = degrees[i]
            between = betweenness[i]
            results.append((label, degree, between))

        return results

    def get_neighbors(self, topic_id: int, k: int = 10) -> List[Tuple[str, float]]:
        """
        Get k nearest neighbors of topic by edge weight

        Args:
            topic_id: Topic ID
            k: Number of neighbors to return

        Returns:
            List of (neighbor_label, edge_weight) tuples
        """
        if self.graph is None or topic_id not in self.topic_id_to_vertex:
            return []

        v_idx = self.topic_id_to_vertex[topic_id]

        # Get all neighbors with weights
        neighbors = []
        for edge in self.graph.es.select(_source=v_idx):
            neighbor_idx = edge.target
            weight = edge["weight"]
            label = self.graph.vs[neighbor_idx]["label"]
            neighbors.append((label, weight))

        for edge in self.graph.es.select(_target=v_idx):
            neighbor_idx = edge.source
            weight = edge["weight"]
            label = self.graph.vs[neighbor_idx]["label"]
            neighbors.append((label, weight))

        # Sort by weight descending
        neighbors.sort(key=lambda x: x[1], reverse=True)

        return neighbors[:k]


def build_topic_graph(db_connection) -> Tuple[TopicGraph, Dict]:
    """
    Build topic graph from database

    Args:
        db_connection: Database connection

    Returns:
        (TopicGraph instance, statistics dictionary)
    """
    graph_builder = TopicGraph()
    graph_builder.build_from_database(db_connection)
    stats = graph_builder.get_statistics()

    return graph_builder, stats


if __name__ == "__main__":
    # Test topic graph construction
    from src.database.operations import get_db

    print("\nTesting Topic Graph Construction")
    print("=" * 60)

    with get_db() as db:
        graph_builder, stats = build_topic_graph(db)

        print("\n" + "=" * 60)
        print("Topic Graph Construction Complete!")
        print("=" * 60)
        print(f"Vertices: {stats['vertices']}")
        print(f"Edges: {stats['edges']}")
        print(f"Density: {stats['density']:.6f}")
        print(f"Average degree: {stats['avg_degree']:.2f}")
        print(f"Max degree: {stats['max_degree']}")
        print(f"Connected components: {stats['connected_components']}")
        print(f"Largest component size: {stats['largest_component_size']}")
        print(f"Average clustering: {stats['avg_clustering']:.4f}")
        print(f"Diameter: {stats['diameter']}")

        # Show top hubs
        print("\nTop 10 hub topics (by degree):")
        hubs = graph_builder.get_top_hubs(k=10)
        for i, (label, degree, betweenness) in enumerate(hubs, 1):
            print(f"  {i}. {label}")
            print(f"     Degree: {degree}, Betweenness: {betweenness:.2f}")

        # Show neighbors of most connected topic
        if hubs:
            top_topic_label = hubs[0][0]
            # Find topic_id
            cursor = db.conn.cursor()
            cursor.execute("SELECT id FROM topics WHERE label = ?", (top_topic_label,))
            topic_id = cursor.fetchone()[0]

            print(f"\nNeighbors of '{top_topic_label}':")
            neighbors = graph_builder.get_neighbors(topic_id, k=10)
            for i, (label, weight) in enumerate(neighbors, 1):
                print(f"  {i}. {label} (weight: {weight:.4f})")
