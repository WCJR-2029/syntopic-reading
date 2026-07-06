"""Configuration management for Syntopic Reading System"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Database
DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/database/syntopic.db")

# Models
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

# Processing Parameters
TARGET_CHUNK_SIZE = int(os.getenv("TARGET_CHUNK_SIZE", "500"))
CHUNK_TOLERANCE = float(os.getenv("CHUNK_TOLERANCE", "0.2"))
TOPICS_PER_CHUNK = int(os.getenv("TOPICS_PER_CHUNK", "5"))
MIN_PMI_SCORE = float(os.getenv("MIN_PMI_SCORE", "0.5"))
NOVELTY_K_NEIGHBORS = int(os.getenv("NOVELTY_K_NEIGHBORS", "10"))

# Agent Parameters
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
MAX_EXCERPTS_PER_TRAIL = int(os.getenv("MAX_EXCERPTS_PER_TRAIL", "10"))

# Paths
DATA_DIR = PROJECT_ROOT / "data"
BOOKS_RAW_DIR = DATA_DIR / "books" / "raw"
BOOKS_PROCESSED_DIR = DATA_DIR / "books" / "processed"
TRAILS_DIR = DATA_DIR / "trails"
DATABASE_DIR = DATA_DIR / "database"

# Create directories if they don't exist
for dir_path in [BOOKS_RAW_DIR, BOOKS_PROCESSED_DIR, TRAILS_DIR, DATABASE_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)


def validate_config():
    """Validate that required configuration is present"""
    errors = []

    if not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY not found in environment")

    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY not found in environment")

    if errors:
        raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))

    return True
