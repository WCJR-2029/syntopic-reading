"""Vector store using sqlite-vec for topic similarity search"""

import sqlite3
import sqlite_vec
import numpy as np
from typing import List, Tuple, Optional
from pathlib import Path


class VectorStore:
    """Vector similarity search using sqlite-vec"""

    def __init__(self, db_path: str | Path):
        """
        Initialize vector store

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.enable_load_extension(True)

        # Load sqlite-vec extension from Python package
        sqlite_vec.load(self.conn)

        self.embedding_dim = None
        print("✓ sqlite-vec extension loaded")

    def create_vector_table(self, embedding_dim: int = 384):
        """
        Create virtual table for vector similarity search

        Args:
            embedding_dim: Dimension of embeddings (384 for all-MiniLM-L6-v2)
        """
        self.embedding_dim = embedding_dim

        cursor = self.conn.cursor()

        # Create virtual table for vector search
        cursor.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_topics
            USING vec0(
                topic_id INTEGER PRIMARY KEY,
                embedding FLOAT[{embedding_dim}]
            )
        """)

        self.conn.commit()
        print(f"✓ Created vector table (dimension: {embedding_dim})")

    def populate_from_topics(self):
        """Populate vector table from topics.embedding column"""
        cursor = self.conn.cursor()

        # Get all canonical topics with embeddings
        cursor.execute("""
            SELECT id, embedding
            FROM topics
            WHERE canonical_id IS NULL
            AND embedding IS NOT NULL
        """)

        topics = cursor.fetchall()

        if not topics:
            print("No topics with embeddings found")
            return 0

        # Clear existing vectors
        cursor.execute("DELETE FROM vec_topics")

        # Insert vectors
        for topic_id, embedding_bytes in topics:
            # Convert bytes to numpy array
            embedding = np.frombuffer(embedding_bytes, dtype=np.float32)

            # Ensure correct dimension
            if self.embedding_dim is None:
                self.embedding_dim = len(embedding)

            # Insert into vector table
            cursor.execute(
                "INSERT INTO vec_topics (topic_id, embedding) VALUES (?, ?)",
                (topic_id, embedding.tobytes())
            )

        self.conn.commit()

        print(f"✓ Populated vector table with {len(topics)} topic embeddings")
        return len(topics)

    def similar_topics(
        self,
        topic_id: int,
        k: int = 10,
        include_self: bool = False
    ) -> List[Tuple[int, str, float]]:
        """
        Find k most similar topics to given topic

        Args:
            topic_id: Source topic ID
            k: Number of similar topics to return
            include_self: Include source topic in results

        Returns:
            List of (topic_id, label, distance) tuples
        """
        cursor = self.conn.cursor()

        # Get source embedding
        cursor.execute(
            "SELECT embedding FROM vec_topics WHERE topic_id = ?",
            (topic_id,)
        )
        result = cursor.fetchone()

        if not result:
            return []

        source_embedding = result[0]

        # Find similar vectors
        # Note: sqlite-vec uses L2 distance by default
        cursor.execute(f"""
            SELECT
                v.topic_id,
                t.label,
                vec_distance_L2(v.embedding, ?) as distance
            FROM vec_topics v
            JOIN topics t ON v.topic_id = t.id
            {"WHERE v.topic_id != ?" if not include_self else ""}
            ORDER BY distance ASC
            LIMIT ?
        """, (source_embedding, topic_id, k) if not include_self else (source_embedding, k))

        results = cursor.fetchall()
        return results

    def search_by_text(
        self,
        query_text: str,
        k: int = 10,
        model = None
    ) -> List[Tuple[int, str, float]]:
        """
        Find k most similar topics to query text

        Args:
            query_text: Query text to search for
            k: Number of results to return
            model: EmbeddingModel instance (if None, will create new one)

        Returns:
            List of (topic_id, label, distance) tuples
        """
        # Generate embedding for query
        if model is None:
            from src.embeddings.embedding_model import EmbeddingModel
            model = EmbeddingModel()

        query_embedding = model.encode(query_text)

        # Search vectors
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT
                v.topic_id,
                t.label,
                vec_distance_L2(v.embedding, ?) as distance
            FROM vec_topics v
            JOIN topics t ON v.topic_id = t.id
            ORDER BY distance ASC
            LIMIT ?
        """, (query_embedding.tobytes(), k))

        results = cursor.fetchall()
        return results


def setup_vector_store(db_path: str | Path, embedding_dim: int = 384) -> dict:
    """
    Set up vector store and populate from existing embeddings

    Args:
        db_path: Path to SQLite database
        embedding_dim: Dimension of embeddings

    Returns:
        Statistics dictionary
    """
    store = VectorStore(db_path)

    print("\nSetting up vector store...")
    store.create_vector_table(embedding_dim=embedding_dim)

    print("Populating from topics table...")
    count = store.populate_from_topics()

    stats = {
        'vector_count': count,
        'embedding_dimension': embedding_dim,
    }

    return stats


if __name__ == "__main__":
    # Test vector store
    from src.config import DATABASE_PATH
    from src.database.operations import get_db

    print("\nTesting Vector Store")
    print("=" * 60)

    # Set up vector store
    stats = setup_vector_store(DATABASE_PATH, embedding_dim=384)

    print("\n" + "=" * 60)
    print("Vector Store Setup Complete!")
    print("=" * 60)
    print(f"Vector count: {stats['vector_count']}")
    print(f"Embedding dimension: {stats['embedding_dimension']}")

    # Test similarity search
    if stats['vector_count'] > 0:
        print("\n" + "=" * 60)
        print("Testing Similarity Search")
        print("=" * 60)

        store = VectorStore(DATABASE_PATH)

        # Get a sample topic
        with get_db() as db:
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT id, label
                FROM topics
                WHERE canonical_id IS NULL
                LIMIT 1
            """)
            sample_topic = cursor.fetchone()

            if sample_topic:
                topic_id, label = sample_topic
                print(f"\nFinding topics similar to: '{label}' (ID: {topic_id})")

                similar = store.similar_topics(topic_id, k=5, include_self=False)

                print(f"\nTop 5 similar topics:")
                for i, (tid, tlabel, distance) in enumerate(similar, 1):
                    print(f"  {i}. {tlabel} (distance: {distance:.4f})")
