#!/usr/bin/env python3
"""Incrementally embed any canonical topics missing an embedding, then rebuild
the vec_topics vector table. Safe to run after adding a new book.

Why this exists: process_with_topics.py adds chunks + topics but does NOT generate
embeddings. generate_all_embeddings() re-embeds ALL canonical topics (non-incremental,
slow at 220K). This does the same end-state incrementally — only topics WHERE
embedding IS NULL — using the SAME model (all-MiniLM-L6-v2, 384-dim) the library was
built on, so there is no dimension/model drift. Then it repopulates vec_topics
(full DELETE + reinsert from every embedded canonical topic).
"""

from rich.console import Console
import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.database.operations import get_db
from src.database.vector_store import setup_vector_store
from src.embeddings.embedding_model import EmbeddingModel
from src.config import DATABASE_PATH

console = Console()
BATCH = 1000


def main():
    model = EmbeddingModel()  # all-MiniLM-L6-v2, 384-dim (library default)
    dim = model.embedding_dim
    console.print(f"[bold]Embedding model:[/bold] {model.model_name} ({dim}-dim)")

    with get_db() as db:
        cur = db.conn.cursor()
        cur.execute(
            "SELECT id, label FROM topics "
            "WHERE canonical_id IS NULL AND embedding IS NULL ORDER BY id"
        )
        rows = cur.fetchall()
        total = len(rows)
        console.print(f"[bold]Topics missing embeddings:[/bold] {total:,}")

        if total:
            done = 0
            for i in range(0, total, BATCH):
                chunk = rows[i:i + BATCH]
                ids = [r[0] for r in chunk]
                labels = [r[1] for r in chunk]
                embs = model.encode_topics(labels, show_progress=False)
                for tid, emb in zip(ids, embs):
                    cur.execute(
                        "UPDATE topics SET embedding = ? WHERE id = ?",
                        (emb.astype('float32').tobytes(), tid),
                    )
                db.conn.commit()
                done += len(chunk)
                console.print(f"  embedded {done:,}/{total:,}")
        else:
            console.print("  [green]nothing to embed — all canonical topics already have embeddings[/green]")

        cur.execute("SELECT COUNT(*) FROM topics WHERE canonical_id IS NULL AND embedding IS NOT NULL")
        embedded = cur.fetchone()[0]
        console.print(f"[bold]Canonical topics now embedded:[/bold] {embedded:,}")

    console.print("\n[bold]Rebuilding vec_topics vector table...[/bold]")
    vstats = setup_vector_store(DATABASE_PATH, embedding_dim=dim)
    console.print(f"  [green]vec_topics rows:[/green] {vstats.get('vector_count', '?')}")
    console.print("\n[bold green]✅ Embeddings + vector store refreshed[/bold green]")


if __name__ == "__main__":
    main()
