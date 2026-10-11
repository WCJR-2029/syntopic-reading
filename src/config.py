"""Configuration management for Syntopic Reading System"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

# LLM Provider ("bring your own LLM" - see src/llm/client.py). Default is
# Anthropic (Claude) - the whole pipeline (topic extraction, usefulness
# filtering, trail generation) runs on this ONE key. Set LLM_PROVIDER=openai
# to use OpenAI or any OpenAI-compatible endpoint instead (OpenAI, OpenRouter,
# local Ollama / LM Studio, ...).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

# Database
DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/database/syntopic.db")

# Models
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# Processing Parameters
TARGET_CHUNK_SIZE = int(os.getenv("TARGET_CHUNK_SIZE", "500"))
CHUNK_TOLERANCE = float(os.getenv("CHUNK_TOLERANCE", "0.2"))
TOPICS_PER_CHUNK = int(os.getenv("TOPICS_PER_CHUNK", "5"))
NOVELTY_K_NEIGHBORS = int(os.getenv("NOVELTY_K_NEIGHBORS", "10"))
# Note: graph edge thresholds (min PMI, min co-occurrence) are NOT env vars - they
# are build_graph.py CLI flags (--min-pmi / --min-cooccurrence). A former unused
# MIN_PMI_SCORE env var was removed to avoid shipping config that never took effect.

# Agent Parameters
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

    provider = (LLM_PROVIDER or "anthropic").strip().lower()
    if provider == "openai":
        if not OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY not found in environment (required for LLM_PROVIDER=openai)")
    elif provider == "anthropic":
        if not ANTHROPIC_API_KEY:
            errors.append("ANTHROPIC_API_KEY not found in environment (required for LLM_PROVIDER=anthropic)")
    else:
        errors.append(f"Unsupported LLM_PROVIDER '{provider}' - expected 'anthropic' or 'openai'")

    if errors:
        raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))

    return True
