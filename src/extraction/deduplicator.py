"""Topic deduplication using embedding similarity to merge near-synonyms

DEPRECATED for large libraries: this module computes a full N x N cosine
similarity matrix (O(N^2) memory - it WILL exhaust RAM at real-library scale,
~218K topics) and re-encodes every label from scratch. Use the scale-safe,
embedding-reusing pass in ``src/graph/canonicalize.py`` instead (it runs by
default in ``scripts/build_graph.py``). Kept for reference and tiny ad-hoc
runs only.
"""

import numpy as np
from typing import List, Tuple, Dict, Set
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer


class TopicDeduplicator:
    """Merge near-synonym topics using embedding similarity"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", similarity_threshold: float = 0.85):
        """
        Initialize deduplicator

        Args:
            model_name: Sentence transformer model for embeddings
            similarity_threshold: Cosine similarity threshold for merging (0.85 = very similar)
        """
        print(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.similarity_threshold = similarity_threshold

    def find_duplicates(self, topics: List[str]) -> List[List[int]]:
        """
        Find duplicate topic groups using embedding similarity

        Args:
            topics: List of topic labels

        Returns:
            List of duplicate groups (each group is list of indices)
        """
        if not topics:
            return []

        print(f"Generating embeddings for {len(topics)} topics...")
        embeddings = self.model.encode(topics, show_progress_bar=True)

        print("Computing similarity matrix...")
        similarity_matrix = cosine_similarity(embeddings)

        # Build duplicate groups
        duplicate_groups = []
        processed = set()

        for i in range(len(topics)):
            if i in processed:
                continue

            # Find all topics similar to this one
            similar_indices = []
            for j in range(len(topics)):
                if i == j:
                    continue
                if similarity_matrix[i][j] >= self.similarity_threshold:
                    similar_indices.append(j)

            if similar_indices:
                # Create group with canonical topic (lowest index = first occurrence)
                group = [i] + similar_indices
                duplicate_groups.append(sorted(group))
                processed.update(group)

        return duplicate_groups

    def select_canonical(self, topic_labels: List[str], topic_frequencies: List[int]) -> str:
        """
        Select canonical topic from duplicate group

        Strategy: Choose most frequent topic, breaking ties by alphabetical order

        Args:
            topic_labels: Labels of duplicate topics
            topic_frequencies: Frequency counts for each topic

        Returns:
            Canonical topic label
        """
        # Find max frequency
        max_freq = max(topic_frequencies)

        # Get all topics with max frequency
        max_freq_topics = [
            label for label, freq in zip(topic_labels, topic_frequencies)
            if freq == max_freq
        ]

        # Break ties alphabetically (shortest first, then alphabetical)
        canonical = min(max_freq_topics, key=lambda x: (len(x), x))

        return canonical

    def deduplicate_topics(
        self,
        topic_data: List[Tuple[int, str, int]]
    ) -> Dict[int, int]:
        """
        Deduplicate topics and return mapping to canonical IDs

        Args:
            topic_data: List of (topic_id, label, frequency) tuples

        Returns:
            Dictionary mapping topic_id -> canonical_topic_id
        """
        if not topic_data:
            return {}

        # Extract data
        topic_ids = [t[0] for t in topic_data]
        topic_labels = [t[1] for t in topic_data]
        topic_frequencies = [t[2] for t in topic_data]

        # Find duplicate groups
        duplicate_groups = self.find_duplicates(topic_labels)

        print(f"\nFound {len(duplicate_groups)} duplicate groups")

        # Build canonical mapping
        canonical_mapping = {}

        for group in duplicate_groups:
            # Get data for this group
            group_labels = [topic_labels[i] for i in group]
            group_frequencies = [topic_frequencies[i] for i in group]
            group_ids = [topic_ids[i] for i in group]

            # Select canonical topic
            canonical_label = self.select_canonical(group_labels, group_frequencies)
            canonical_index = group_labels.index(canonical_label)
            canonical_id = group_ids[canonical_index]

            # Map all topics in group to canonical
            for topic_id in group_ids:
                if topic_id != canonical_id:
                    canonical_mapping[topic_id] = canonical_id

            # Debug output
            if len(group) > 1:
                print(f"\nMerging group of {len(group)} topics:")
                for i, (label, freq, tid) in enumerate(zip(group_labels, group_frequencies, group_ids)):
                    is_canonical = (tid == canonical_id)
                    marker = "★" if is_canonical else " "
                    print(f"  {marker} {label} (freq={freq}, id={tid})")

        return canonical_mapping


def deduplicate_database_topics(db_connection, similarity_threshold: float = 0.85) -> Dict[str, int]:
    """
    Deduplicate topics in database

    Args:
        db_connection: Database connection (from database.operations.get_db())
        similarity_threshold: Cosine similarity threshold for merging

    Returns:
        Statistics dictionary
    """
    deduplicator = TopicDeduplicator(similarity_threshold=similarity_threshold)

    # Get all topics
    cursor = db_connection.conn.cursor()
    cursor.execute("SELECT id, label, frequency FROM topics WHERE canonical_id IS NULL")
    topic_data = cursor.fetchall()

    print(f"\nDeduplicating {len(topic_data)} topics...")

    # Find duplicates
    canonical_mapping = deduplicator.deduplicate_topics(topic_data)

    # Update database
    print(f"\nUpdating {len(canonical_mapping)} topics with canonical IDs...")
    for topic_id, canonical_id in canonical_mapping.items():
        cursor.execute(
            "UPDATE topics SET canonical_id = ? WHERE id = ?",
            (canonical_id, topic_id)
        )

    db_connection.conn.commit()

    # Calculate stats
    stats = {
        'total_topics': len(topic_data),
        'duplicate_topics': len(canonical_mapping),
        'canonical_topics': len(topic_data) - len(canonical_mapping),
        'duplicate_groups': len(set(canonical_mapping.values())),
    }

    return stats


if __name__ == "__main__":
    # Test deduplicator
    from src.database.operations import get_db

    print("\nTesting Topic Deduplicator")
    print("=" * 60)

    with get_db() as db:
        stats = deduplicate_database_topics(db, similarity_threshold=0.85)

        print("\n" + "=" * 60)
        print("Deduplication Complete!")
        print("=" * 60)
        print(f"Total topics: {stats['total_topics']}")
        print(f"Canonical topics: {stats['canonical_topics']}")
        print(f"Duplicate topics: {stats['duplicate_topics']}")
        print(f"Duplicate groups: {stats['duplicate_groups']}")
        print(f"Reduction: {100 * stats['duplicate_topics'] / stats['total_topics']:.1f}%")
