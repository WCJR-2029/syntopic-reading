"""Novelty scoring for topics using k-NN distance in embedding space"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from sklearn.neighbors import NearestNeighbors


class NoveltyScorer:
    """Calculate novelty scores for topics based on embedding distances"""

    def __init__(self, k: int = 10):
        """
        Initialize novelty scorer

        Args:
            k: Number of nearest neighbors to consider
        """
        self.k = k
        self.knn_model = None

    def calculate_novelty_scores(
        self,
        topic_embeddings: np.ndarray,
        topic_ids: List[int]
    ) -> Dict[int, float]:
        """
        Calculate novelty scores using mean k-NN distance

        Higher distance = more novel/underexplored topic
        Lower distance = more common/well-connected topic

        Args:
            topic_embeddings: Array of embeddings (shape: [n_topics, embedding_dim])
            topic_ids: List of topic IDs corresponding to embeddings

        Returns:
            Dictionary mapping topic_id -> novelty_score (mean k-NN distance)
        """
        n_topics = len(topic_ids)

        if n_topics == 0:
            return {}

        # Adjust k if fewer topics than k
        k = min(self.k, n_topics - 1)

        if k == 0:
            return {topic_id: 0.0 for topic_id in topic_ids}

        print(f"  Calculating novelty scores (k={k})...")

        # Fit k-NN model
        self.knn_model = NearestNeighbors(n_neighbors=k + 1, metric='cosine')
        self.knn_model.fit(topic_embeddings)

        # Find k nearest neighbors for each topic
        distances, indices = self.knn_model.kneighbors(topic_embeddings)

        # Calculate mean distance to k nearest neighbors (excluding self)
        novelty_scores = {}
        for i, topic_id in enumerate(topic_ids):
            # distances[i][0] is self (distance=0), so take mean of [1:]
            mean_distance = np.mean(distances[i][1:])
            novelty_scores[topic_id] = float(mean_distance)

        return novelty_scores

    def get_novel_topics(
        self,
        novelty_scores: Dict[int, float],
        k: int = 10,
        percentile: Optional[float] = None
    ) -> List[Tuple[int, float]]:
        """
        Get top k most novel topics

        Args:
            novelty_scores: Dictionary from calculate_novelty_scores()
            k: Number of topics to return
            percentile: If provided, return topics above this percentile (0-100)

        Returns:
            List of (topic_id, novelty_score) tuples sorted by novelty descending
        """
        sorted_topics = sorted(
            novelty_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        if percentile is not None:
            threshold = np.percentile(list(novelty_scores.values()), percentile)
            sorted_topics = [(tid, score) for tid, score in sorted_topics if score >= threshold]

        return sorted_topics[:k]

    def get_common_topics(
        self,
        novelty_scores: Dict[int, float],
        k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        Get top k most common/well-connected topics

        Args:
            novelty_scores: Dictionary from calculate_novelty_scores()
            k: Number of topics to return

        Returns:
            List of (topic_id, novelty_score) tuples sorted by novelty ascending
        """
        sorted_topics = sorted(
            novelty_scores.items(),
            key=lambda x: x[1]
        )

        return sorted_topics[:k]


def calculate_novelty_scores(db_connection, k: int = 10) -> Dict:
    """
    Calculate novelty scores for all topics and store in database

    Args:
        db_connection: Database connection
        k: Number of nearest neighbors

    Returns:
        Statistics dictionary
    """
    scorer = NoveltyScorer(k=k)
    cursor = db_connection.conn.cursor()

    print("\nCalculating novelty scores...")

    # Load topic embeddings
    print("  Loading topic embeddings...")
    cursor.execute("""
        SELECT id, embedding
        FROM topics
        WHERE canonical_id IS NULL
        AND embedding IS NOT NULL
        ORDER BY id
    """)

    topics = cursor.fetchall()

    if not topics:
        print("  No topics with embeddings found")
        return {'total_topics': 0}

    topic_ids = [t[0] for t in topics]
    embeddings = []

    for topic_id, embedding_bytes in topics:
        embedding = np.frombuffer(embedding_bytes, dtype=np.float32)
        embeddings.append(embedding)

    embeddings = np.array(embeddings)
    print(f"  Loaded {len(topic_ids)} topic embeddings")

    # Calculate novelty scores
    novelty_scores = scorer.calculate_novelty_scores(embeddings, topic_ids)

    # Store in database
    print("  Storing novelty scores...")
    cursor.execute("DELETE FROM novelty_scores")  # Clear existing

    for topic_id, score in novelty_scores.items():
        cursor.execute("""
            INSERT INTO novelty_scores (topic_id, mean_knn_distance, k_value)
            VALUES (?, ?, ?)
        """, (topic_id, score, k))

    db_connection.conn.commit()

    # Calculate statistics
    scores = list(novelty_scores.values())
    stats = {
        'total_topics': len(novelty_scores),
        'k_value': k,
        'mean_novelty': np.mean(scores),
        'std_novelty': np.std(scores),
        'min_novelty': np.min(scores),
        'max_novelty': np.max(scores),
        'median_novelty': np.median(scores),
        'percentile_75': np.percentile(scores, 75),
        'percentile_90': np.percentile(scores, 90),
    }

    return stats


if __name__ == "__main__":
    # Test novelty scoring
    from src.database.operations import get_db

    print("\nTesting Novelty Scoring")
    print("=" * 60)

    with get_db() as db:
        stats = calculate_novelty_scores(db, k=10)

        print("\n" + "=" * 60)
        print("Novelty Scoring Complete!")
        print("=" * 60)
        print(f"Total topics: {stats['total_topics']}")
        print(f"K-value: {stats['k_value']}")
        print(f"Mean novelty: {stats['mean_novelty']:.4f}")
        print(f"Std novelty: {stats['std_novelty']:.4f}")
        print(f"Min novelty: {stats['min_novelty']:.4f}")
        print(f"Max novelty: {stats['max_novelty']:.4f}")
        print(f"Median novelty: {stats['median_novelty']:.4f}")
        print(f"75th percentile: {stats['percentile_75']:.4f}")
        print(f"90th percentile: {stats['percentile_90']:.4f}")

        # Show most novel topics
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.label, ns.mean_knn_distance
            FROM novelty_scores ns
            JOIN topics t ON ns.topic_id = t.id
            ORDER BY ns.mean_knn_distance DESC
            LIMIT 10
        """)

        print("\nTop 10 most novel topics:")
        for i, (label, score) in enumerate(cursor.fetchall(), 1):
            print(f"  {i}. {label} (novelty: {score:.4f})")

        # Show most common topics
        cursor.execute("""
            SELECT t.label, ns.mean_knn_distance
            FROM novelty_scores ns
            JOIN topics t ON ns.topic_id = t.id
            ORDER BY ns.mean_knn_distance ASC
            LIMIT 10
        """)

        print("\nTop 10 most common/well-connected topics:")
        for i, (label, score) in enumerate(cursor.fetchall(), 1):
            print(f"  {i}. {label} (novelty: {score:.4f})")
