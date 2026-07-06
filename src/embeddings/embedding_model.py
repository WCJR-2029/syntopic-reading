"""Embedding model for generating topic vectors"""

import numpy as np
from typing import List, Union
from sentence_transformers import SentenceTransformer


class EmbeddingModel:
    """Generate embeddings for topics using sentence transformers"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize embedding model

        Args:
            model_name: Sentence transformer model name
                - all-MiniLM-L6-v2: 384 dimensions, fast, good quality (default)
                - all-mpnet-base-v2: 768 dimensions, slower, better quality
        """
        print(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.embedding_dim = self.model.get_sentence_embedding_dimension()

        print(f"  Model loaded: {model_name}")
        print(f"  Embedding dimension: {self.embedding_dim}")

    def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: int = 32,
        show_progress: bool = False
    ) -> np.ndarray:
        """
        Generate embeddings for text(s)

        Args:
            texts: Single text string or list of texts
            batch_size: Batch size for encoding
            show_progress: Show progress bar

        Returns:
            Numpy array of embeddings (shape: [n, embedding_dim])
        """
        if isinstance(texts, str):
            texts = [texts]

        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            convert_to_numpy=True
        )

        return embeddings

    def encode_topics(self, topics: List[str], show_progress: bool = True) -> np.ndarray:
        """
        Generate embeddings for topic labels

        Args:
            topics: List of topic labels
            show_progress: Show progress bar

        Returns:
            Numpy array of embeddings (shape: [n_topics, embedding_dim])
        """
        print(f"Generating embeddings for {len(topics)} topics...")
        embeddings = self.encode(topics, show_progress=show_progress)
        print(f"  Generated {embeddings.shape[0]} embeddings ({embeddings.shape[1]} dimensions)")
        return embeddings


def generate_all_embeddings(db_connection) -> dict:
    """
    Generate embeddings for all canonical topics in database

    Args:
        db_connection: Database connection (from database.operations.get_db())

    Returns:
        Statistics dictionary
    """
    model = EmbeddingModel()
    cursor = db_connection.conn.cursor()

    # Get all canonical topics (topics that are not duplicates)
    cursor.execute("""
        SELECT id, label
        FROM topics
        WHERE canonical_id IS NULL
        ORDER BY id
    """)
    topics = cursor.fetchall()

    if not topics:
        print("No topics found in database")
        return {'total_topics': 0, 'embeddings_generated': 0}

    topic_ids = [t[0] for t in topics]
    topic_labels = [t[1] for t in topics]

    print(f"\nGenerating embeddings for {len(topics)} canonical topics...")

    # Generate embeddings
    embeddings = model.encode_topics(topic_labels, show_progress=True)

    # Store embeddings in database
    print("Storing embeddings in database...")
    for topic_id, embedding in zip(topic_ids, embeddings):
        # Convert to bytes for storage
        embedding_bytes = embedding.tobytes()

        cursor.execute(
            "UPDATE topics SET embedding = ? WHERE id = ?",
            (embedding_bytes, topic_id)
        )

    db_connection.conn.commit()

    stats = {
        'total_topics': len(topics),
        'embeddings_generated': len(embeddings),
        'embedding_dimension': embeddings.shape[1],
        'model_name': model.model_name,
    }

    return stats


if __name__ == "__main__":
    # Test embedding model
    from src.database.operations import get_db

    print("\nTesting Embedding Model")
    print("=" * 60)

    with get_db() as db:
        stats = generate_all_embeddings(db)

        print("\n" + "=" * 60)
        print("Embedding Generation Complete!")
        print("=" * 60)
        print(f"Total topics: {stats['total_topics']}")
        print(f"Embeddings generated: {stats['embeddings_generated']}")
        print(f"Embedding dimension: {stats['embedding_dimension']}")
        print(f"Model: {stats['model_name']}")
