"""Database schema for Syntopic Reading System"""

import sqlite3
from pathlib import Path

# SQL for creating all tables

CREATE_BOOKS_TABLE = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    isbn TEXT,
    file_path TEXT NOT NULL,
    processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_chunks INTEGER DEFAULT 0,
    total_topics INTEGER DEFAULT 0,
    metadata JSON
);
"""

CREATE_CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    word_count INTEGER,
    is_useful BOOLEAN DEFAULT 1,
    start_position INTEGER,
    end_position INTEGER,
    FOREIGN KEY (book_id) REFERENCES books(id),
    UNIQUE(book_id, chunk_index)
);
"""

CREATE_TOPICS_TABLE = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    canonical_id INTEGER,
    frequency INTEGER DEFAULT 1,
    embedding BLOB,
    embedding_vector_id INTEGER,
    hierarchy_level INTEGER,
    parent_topic_id INTEGER,
    FOREIGN KEY (canonical_id) REFERENCES topics(id),
    FOREIGN KEY (parent_topic_id) REFERENCES topics(id)
);
"""

CREATE_CHUNK_TOPICS_TABLE = """
CREATE TABLE IF NOT EXISTS chunk_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    extraction_confidence REAL,
    FOREIGN KEY (chunk_id) REFERENCES chunks(id),
    FOREIGN KEY (topic_id) REFERENCES topics(id),
    UNIQUE(chunk_id, topic_id)
);
"""

CREATE_TOPIC_EDGES_TABLE = """
CREATE TABLE IF NOT EXISTS topic_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_a_id INTEGER NOT NULL,
    topic_b_id INTEGER NOT NULL,
    cooccurrence_count INTEGER DEFAULT 1,
    pmi_score REAL,
    weight REAL,
    FOREIGN KEY (topic_a_id) REFERENCES topics(id),
    FOREIGN KEY (topic_b_id) REFERENCES topics(id),
    UNIQUE(topic_a_id, topic_b_id)
);
"""

CREATE_TOPIC_HIERARCHY_TABLE = """
CREATE TABLE IF NOT EXISTS topic_hierarchy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    parent_id INTEGER,
    community_id INTEGER,
    quality_score REAL,
    FOREIGN KEY (topic_id) REFERENCES topics(id),
    FOREIGN KEY (parent_id) REFERENCES topic_hierarchy(id)
);
"""

CREATE_TRAILS_TABLE = """
CREATE TABLE IF NOT EXISTS trails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent_model TEXT,
    excerpt_count INTEGER DEFAULT 0,
    book_count INTEGER DEFAULT 0,
    metadata JSON
);
"""

CREATE_TRAIL_EXCERPTS_TABLE = """
CREATE TABLE IF NOT EXISTS trail_excerpts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trail_id INTEGER NOT NULL,
    chunk_id INTEGER NOT NULL,
    sequence_order INTEGER NOT NULL,
    excerpt_text TEXT NOT NULL,
    highlight_text TEXT,
    connection_note TEXT,
    FOREIGN KEY (trail_id) REFERENCES trails(id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id),
    UNIQUE(trail_id, sequence_order)
);
"""

CREATE_NOVELTY_SCORES_TABLE = """
CREATE TABLE IF NOT EXISTS novelty_scores (
    topic_id INTEGER PRIMARY KEY,
    mean_knn_distance REAL,
    k_value INTEGER,
    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);
"""

# Indexes for performance

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);",
    "CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_book ON chunks(book_id);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_useful ON chunks(is_useful);",
    "CREATE INDEX IF NOT EXISTS idx_topics_label ON topics(label);",
    "CREATE INDEX IF NOT EXISTS idx_topics_canonical ON topics(canonical_id);",
    "CREATE INDEX IF NOT EXISTS idx_topics_frequency ON topics(frequency DESC);",
    "CREATE INDEX IF NOT EXISTS idx_topics_hierarchy ON topics(hierarchy_level, parent_topic_id);",
    "CREATE INDEX IF NOT EXISTS idx_chunk_topics_chunk ON chunk_topics(chunk_id);",
    "CREATE INDEX IF NOT EXISTS idx_chunk_topics_topic ON chunk_topics(topic_id);",
    "CREATE INDEX IF NOT EXISTS idx_edges_topic_a ON topic_edges(topic_a_id);",
    "CREATE INDEX IF NOT EXISTS idx_edges_topic_b ON topic_edges(topic_b_id);",
    "CREATE INDEX IF NOT EXISTS idx_edges_pmi ON topic_edges(pmi_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_hierarchy_level ON topic_hierarchy(level);",
    "CREATE INDEX IF NOT EXISTS idx_hierarchy_parent ON topic_hierarchy(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_trails_created ON trails(created_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_trail_excerpts_trail ON trail_excerpts(trail_id);",
    "CREATE INDEX IF NOT EXISTS idx_trail_excerpts_order ON trail_excerpts(trail_id, sequence_order);",
]


def create_database(db_path: str | Path) -> None:
    """Create database with all tables and indexes"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create all tables
    tables = [
        ("books", CREATE_BOOKS_TABLE),
        ("chunks", CREATE_CHUNKS_TABLE),
        ("topics", CREATE_TOPICS_TABLE),
        ("chunk_topics", CREATE_CHUNK_TOPICS_TABLE),
        ("topic_edges", CREATE_TOPIC_EDGES_TABLE),
        ("topic_hierarchy", CREATE_TOPIC_HIERARCHY_TABLE),
        ("trails", CREATE_TRAILS_TABLE),
        ("trail_excerpts", CREATE_TRAIL_EXCERPTS_TABLE),
        ("novelty_scores", CREATE_NOVELTY_SCORES_TABLE),
    ]

    for table_name, create_sql in tables:
        cursor.execute(create_sql)
        print(f"✓ Created table: {table_name}")

    # Create all indexes
    for index_sql in CREATE_INDEXES:
        cursor.execute(index_sql)

    print(f"✓ Created {len(CREATE_INDEXES)} indexes")

    conn.commit()
    conn.close()

    print(f"\n✓ Database initialized at {db_path}")
    print(f"  Total tables: {len(tables)}")
    print(f"  Total indexes: {len(CREATE_INDEXES)}")


if __name__ == "__main__":
    from src.config import DATABASE_PATH

    create_database(DATABASE_PATH)
