"""Co-occurrence analysis and PMI calculation for topic pairs"""

import math
from typing import Dict, List, Tuple, Set
from collections import defaultdict, Counter

from src.graph.canonical_resolve import (
    CHUNK_TOPICS_CANONICAL_VIEW,
    ensure_canonical_views,
)


class CooccurrenceAnalyzer:
    """Calculate co-occurrence statistics and PMI for topic pairs"""

    def __init__(self, min_cooccurrence: int = 2, min_pmi: float = 0.0):
        """
        Initialize co-occurrence analyzer

        Args:
            min_cooccurrence: Minimum co-occurrence count to consider (filter rare pairs)
            min_pmi: Minimum PMI score to consider (filter weak associations)
        """
        self.min_cooccurrence = min_cooccurrence
        self.min_pmi = min_pmi

    def calculate_cooccurrence(
        self,
        chunk_topics: List[Tuple[int, List[int]]]
    ) -> Dict[Tuple[int, int], int]:
        """
        Calculate co-occurrence counts for topic pairs

        Args:
            chunk_topics: List of (chunk_id, [topic_ids]) tuples

        Returns:
            Dictionary mapping (topic_a, topic_b) -> count
        """
        cooccurrence = defaultdict(int)

        for chunk_id, topic_ids in chunk_topics:
            # Get unique topics in this chunk
            unique_topics = list(set(topic_ids))

            # Count all pairs
            for i, topic_a in enumerate(unique_topics):
                for topic_b in unique_topics[i + 1:]:
                    # Always store in canonical order (smaller ID first)
                    pair = tuple(sorted([topic_a, topic_b]))
                    cooccurrence[pair] += 1

        return dict(cooccurrence)

    def calculate_pmi(
        self,
        cooccurrence: Dict[Tuple[int, int], int],
        topic_counts: Dict[int, int],
        total_chunks: int
    ) -> Dict[Tuple[int, int], float]:
        """
        Calculate Pointwise Mutual Information (PMI) for topic pairs

        PMI(a, b) = log(P(a, b) / (P(a) * P(b)))

        High PMI = topics appear together more often than random chance
        Low PMI = topics appear together less often than random chance

        Args:
            cooccurrence: Co-occurrence counts from calculate_cooccurrence()
            topic_counts: Dictionary mapping topic_id -> total chunk count
            total_chunks: Total number of chunks in corpus

        Returns:
            Dictionary mapping (topic_a, topic_b) -> PMI score
        """
        pmi_scores = {}

        for (topic_a, topic_b), cooccur_count in cooccurrence.items():
            # Filter rare co-occurrences
            if cooccur_count < self.min_cooccurrence:
                continue

            # Calculate probabilities
            p_a = topic_counts[topic_a] / total_chunks
            p_b = topic_counts[topic_b] / total_chunks
            p_ab = cooccur_count / total_chunks

            # Calculate PMI
            # Use log2 for interpretability (PMI=1 means 2x more likely than random)
            if p_a > 0 and p_b > 0 and p_ab > 0:
                pmi = math.log2(p_ab / (p_a * p_b))

                # Filter weak associations
                if pmi >= self.min_pmi:
                    pmi_scores[(topic_a, topic_b)] = pmi

        return pmi_scores

    def calculate_edge_weights(
        self,
        pmi_scores: Dict[Tuple[int, int], float],
        cooccurrence: Dict[Tuple[int, int], int]
    ) -> Dict[Tuple[int, int], float]:
        """
        Calculate edge weights combining PMI and co-occurrence frequency

        Weight = PMI * log(cooccurrence_count + 1)

        This balances:
        - PMI: How statistically significant is the association?
        - Frequency: How often do they actually co-occur?

        Args:
            pmi_scores: PMI scores from calculate_pmi()
            cooccurrence: Co-occurrence counts

        Returns:
            Dictionary mapping (topic_a, topic_b) -> weight
        """
        weights = {}

        for (topic_a, topic_b), pmi in pmi_scores.items():
            cooccur_count = cooccurrence.get((topic_a, topic_b), 0)

            # Combine PMI with log-scaled frequency
            # log(count + 1) prevents zero and dampens very high frequencies
            weight = pmi * math.log2(cooccur_count + 1)

            weights[(topic_a, topic_b)] = weight

        return weights


def build_cooccurrence_edges(db_connection, min_cooccurrence: int = 2, min_pmi: float = 0.0) -> dict:
    """
    Build co-occurrence edges and populate topic_edges table

    Args:
        db_connection: Database connection (from database.operations.get_db())
        min_cooccurrence: Minimum co-occurrence count threshold
        min_pmi: Minimum PMI threshold

    Returns:
        Statistics dictionary
    """
    analyzer = CooccurrenceAnalyzer(min_cooccurrence=min_cooccurrence, min_pmi=min_pmi)
    cursor = db_connection.conn.cursor()

    print("\nBuilding co-occurrence graph...")

    # Step 1: Get chunk-topic relationships, resolved to CANONICAL topics via
    # the shared resolution view (src/graph/canonical_resolve.py). A topic that
    # was merged by the canonicalization pass contributes its chunk links to
    # its canonical topic - this is what lets the same idea, worded differently
    # across books, co-occur as ONE node. With no merged topics the view is the
    # identity mapping and this is identical to the old canonical-only query.
    print("  Loading chunk-topic data (resolving aliases to canonical topics)...")
    ensure_canonical_views(cursor)
    cursor.execute(f"""
        SELECT chunk_id, topic_id
        FROM {CHUNK_TOPICS_CANONICAL_VIEW}
        ORDER BY chunk_id
    """)

    # Group by chunk_id
    chunk_topics = defaultdict(list)
    for chunk_id, topic_id in cursor.fetchall():
        chunk_topics[chunk_id].append(topic_id)

    chunk_topics_list = [(chunk_id, topics) for chunk_id, topics in chunk_topics.items()]
    print(f"  Loaded {len(chunk_topics_list)} chunks with topics")

    # Step 2: Calculate co-occurrence
    print("  Calculating co-occurrence counts...")
    cooccurrence = analyzer.calculate_cooccurrence(chunk_topics_list)
    print(f"  Found {len(cooccurrence)} topic pairs")

    # Step 3: Get topic frequencies (also resolved through the view so
    # P(topic) counts every chunk the merged idea appears in)
    print("  Loading topic frequencies...")
    cursor.execute(f"""
        SELECT topic_id, COUNT(DISTINCT chunk_id) as chunk_count
        FROM {CHUNK_TOPICS_CANONICAL_VIEW}
        GROUP BY topic_id
    """)
    topic_counts = {topic_id: count for topic_id, count in cursor.fetchall()}

    # Get total chunks
    cursor.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_topics")
    total_chunks = cursor.fetchone()[0]

    # Step 4: Calculate PMI
    print("  Calculating PMI scores...")
    pmi_scores = analyzer.calculate_pmi(cooccurrence, topic_counts, total_chunks)
    print(f"  Retained {len(pmi_scores)} pairs (min_pmi={min_pmi})")

    # Step 5: Calculate edge weights
    print("  Calculating edge weights...")
    weights = analyzer.calculate_edge_weights(pmi_scores, cooccurrence)

    # Step 6: Store in database
    print("  Storing edges in database...")
    cursor.execute("DELETE FROM topic_edges")  # Clear existing

    for (topic_a, topic_b), pmi in pmi_scores.items():
        cooccur_count = cooccurrence[(topic_a, topic_b)]
        weight = weights[(topic_a, topic_b)]

        cursor.execute("""
            INSERT INTO topic_edges (topic_a_id, topic_b_id, cooccurrence_count, pmi_score, weight)
            VALUES (?, ?, ?, ?, ?)
        """, (topic_a, topic_b, cooccur_count, pmi, weight))

    db_connection.conn.commit()

    # Calculate statistics
    stats = {
        'total_topic_pairs': len(cooccurrence),
        'filtered_pairs': len(pmi_scores),
        'avg_pmi': sum(pmi_scores.values()) / len(pmi_scores) if pmi_scores else 0,
        'avg_weight': sum(weights.values()) / len(weights) if weights else 0,
        'edges_stored': len(pmi_scores),
    }

    return stats


if __name__ == "__main__":
    # Test co-occurrence analysis
    from src.database.operations import get_db

    print("\nTesting Co-occurrence Analysis")
    print("=" * 60)

    with get_db() as db:
        stats = build_cooccurrence_edges(db, min_cooccurrence=2, min_pmi=0.0)

        print("\n" + "=" * 60)
        print("Co-occurrence Analysis Complete!")
        print("=" * 60)
        print(f"Total topic pairs: {stats['total_topic_pairs']}")
        print(f"Filtered pairs (min_cooccurrence=2, min_pmi=0.0): {stats['filtered_pairs']}")
        print(f"Average PMI: {stats['avg_pmi']:.4f}")
        print(f"Average weight: {stats['avg_weight']:.4f}")
        print(f"Edges stored: {stats['edges_stored']}")

        # Show top 10 edges by PMI
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT
                t1.label as topic_a,
                t2.label as topic_b,
                e.cooccurrence_count,
                e.pmi_score,
                e.weight
            FROM topic_edges e
            JOIN topics t1 ON e.topic_a_id = t1.id
            JOIN topics t2 ON e.topic_b_id = t2.id
            ORDER BY e.pmi_score DESC
            LIMIT 10
        """)

        print("\nTop 10 topic pairs by PMI:")
        for i, (topic_a, topic_b, count, pmi, weight) in enumerate(cursor.fetchall(), 1):
            print(f"  {i}. {topic_a} + {topic_b}")
            print(f"     Co-occur: {count}, PMI: {pmi:.4f}, Weight: {weight:.4f}")
