"""Database operations for syntopic reading system"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import json


class Database:
    """Database operations wrapper"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = None

    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Enable column access by name
        return self.conn

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.close()

    # Books operations

    def add_book(self, title: str, author: Optional[str], isbn: Optional[str],
                 file_path: str, metadata: Optional[Dict] = None) -> int:
        """Add a book to the database"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO books (title, author, isbn, file_path, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (title, author, isbn, file_path, json.dumps(metadata) if metadata else None))

        return cursor.lastrowid

    def get_book(self, book_id: int) -> Optional[Dict]:
        """Get book by ID"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()

        if row:
            return dict(row)
        return None

    def update_book_stats(self, book_id: int, total_chunks: int, total_topics: int):
        """Update book processing statistics"""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE books
            SET total_chunks = ?, total_topics = ?
            WHERE id = ?
        """, (total_chunks, total_topics, book_id))

    # Chunks operations

    def add_chunk(self, book_id: int, chunk_index: int, text: str,
                  word_count: int, start_position: int, end_position: int) -> int:
        """Add a chunk to the database"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO chunks (book_id, chunk_index, text, word_count,
                              start_position, end_position, is_useful)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (book_id, chunk_index, text, word_count, start_position, end_position))

        return cursor.lastrowid

    def get_chunks(self, book_id: int) -> List[Dict]:
        """Get all chunks for a book"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM chunks
            WHERE book_id = ?
            ORDER BY chunk_index
        """, (book_id,))

        return [dict(row) for row in cursor.fetchall()]

    def count_chunks(self, book_id: int) -> int:
        """Count chunks for a book"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE book_id = ?", (book_id,))
        return cursor.fetchone()[0]

    # Topics operations (Phase 2)

    def add_topic(self, label: str, frequency: int = 1) -> int:
        """Add a topic to the database"""
        cursor = self.conn.cursor()

        # Check if topic already exists
        cursor.execute("SELECT id, frequency FROM topics WHERE label = ?", (label,))
        existing = cursor.fetchone()

        if existing:
            # Update frequency
            topic_id, current_freq = existing
            cursor.execute("""
                UPDATE topics SET frequency = ? WHERE id = ?
            """, (current_freq + frequency, topic_id))
            return topic_id
        else:
            # Insert new topic
            cursor.execute("""
                INSERT INTO topics (label, frequency)
                VALUES (?, ?)
            """, (label, frequency))
            return cursor.lastrowid

    def link_chunk_topic(self, chunk_id: int, topic_id: int, confidence: float = 1.0):
        """Link a chunk to a topic"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO chunk_topics (chunk_id, topic_id, extraction_confidence)
            VALUES (?, ?, ?)
        """, (chunk_id, topic_id, confidence))

    # Stats and queries

    def get_stats(self) -> Dict:
        """Get database statistics"""
        cursor = self.conn.cursor()

        stats = {}

        # Books count
        cursor.execute("SELECT COUNT(*) FROM books")
        stats['total_books'] = cursor.fetchone()[0]

        # Chunks count
        cursor.execute("SELECT COUNT(*) FROM chunks")
        stats['total_chunks'] = cursor.fetchone()[0]

        # Topics count
        cursor.execute("SELECT COUNT(*) FROM topics")
        stats['total_topics'] = cursor.fetchone()[0]

        # Trails count
        cursor.execute("SELECT COUNT(*) FROM trails")
        stats['total_trails'] = cursor.fetchone()[0]

        return stats


def get_db() -> Database:
    """Get database instance"""
    from src.config import DATABASE_PATH
    return Database(DATABASE_PATH)


if __name__ == "__main__":
    # Test database operations
    from src.config import DATABASE_PATH

    with Database(DATABASE_PATH) as db:
        stats = db.get_stats()
        print("\nDatabase Statistics:")
        print(f"  Books: {stats['total_books']}")
        print(f"  Chunks: {stats['total_chunks']}")
        print(f"  Topics: {stats['total_topics']}")
        print(f"  Trails: {stats['total_trails']}")
