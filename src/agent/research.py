"""Research Agent - Browses chunks, sequences excerpts across books"""

from typing import List, Dict, Optional

from src.database.operations import get_db
from src.config import MAX_EXCERPTS_PER_TRAIL, TRAILS_DIR
from src.llm.client import get_client
from src.agent.trail_constraints import (
    build_existing_trail_corpus,
    count_excerpt_appearances,
)


class ResearchAgent:
    """
    Researches and sequences excerpts for a trail idea.

    Uses CLI tools to:
    - Find chunks containing target topics
    - Browse related content via topic graph
    - Sequence excerpts narratively across ≥4 distinct books, no single book dominating
    - Select 5-10 representative excerpts

    Workflow:
    1. Search for chunks with target topics
    2. Use the configured LLM to evaluate relevance and sequencing
    3. Return structured excerpt list with book context
    """

    def __init__(self, model: str = None):
        """
        Initialize research agent

        Args:
            model: Optional LLM model override (defaults to the configured
                LLM_MODEL - see src/llm/client.py)
        """
        self.client = get_client(model=model)
        self.model = self.client.model

    def _get_vector_search(self):
        """Lazily build + cache the topic vector store and embedding model.

        Returns ``(store, model)``, or ``(None, None)`` if the vector store is
        unavailable (e.g. embeddings were never built) so callers transparently
        fall back to exact label matching."""
        if getattr(self, "_vec_store", "unset") == "unset":
            try:
                from src.database.vector_store import VectorStore
                from src.embeddings.embedding_model import EmbeddingModel
                from src.config import DATABASE_PATH
                self._vec_store = VectorStore(DATABASE_PATH)
                self._embed_model = EmbeddingModel()
            except Exception as e:  # noqa: BLE001 - degrade gracefully
                print(f"   Note: semantic topic resolution unavailable ({e}); "
                      f"using exact label match only.")
                self._vec_store = None
                self._embed_model = None
        return self._vec_store, self._embed_model

    def _resolve_topic_ids(self, topic_labels: List[str], semantic_k: int = 3) -> List[int]:
        """Map requested topic labels to concrete canonical topic IDs.

        For each label: keep the exact-label match if one exists, then expand with
        the ``semantic_k`` nearest topics from the vector store. Returns an ordered,
        de-duplicated ID list. This expansion is what lets a trail span multiple
        books: topic labels are per-chunk LLM output and are almost never shared
        verbatim across books (~0.2% of topics appear in >=2 books), so exact-only
        matching collapses a trail onto whichever single book owns the one matching
        label. Exact matches still rank first (semantic distance 0.0)."""
        ids: List[int] = []
        seen = set()

        def _add(tid):
            if tid is not None and tid not in seen:
                seen.add(tid)
                ids.append(tid)

        store, model = self._get_vector_search()
        with get_db() as db:
            cursor = db.conn.cursor()
            for label in topic_labels:
                row = cursor.execute(
                    "SELECT id FROM topics WHERE label = ? AND canonical_id IS NULL LIMIT 1",
                    (label,),
                ).fetchone()
                if row:
                    _add(row[0])
                if store is not None:
                    try:
                        for tid, _lbl, _dist in store.search_by_text(
                            label, k=semantic_k, model=model
                        ):
                            _add(tid)
                    except Exception:  # noqa: BLE001 - keep exact matches on failure
                        pass
        return ids

    def find_chunks_for_topics(self, topic_labels: List[str], limit_per_topic: int = 20) -> List[Dict]:
        """
        Find chunks containing target topics

        Args:
            topic_labels: List of topic labels to search
            limit_per_topic: Max chunks per topic

        Returns:
            List of chunk dicts with id, book_id, book_title, author, chunk_index, text, topics
        """
        chunks = []
        seen_chunk_ids = set()

        # Resolve free-text labels to concrete canonical topic IDs (exact match +
        # nearest-neighbour semantic expansion). Exact-only matching collapses a
        # trail onto a single book because topic labels are almost never shared
        # verbatim across books; see _resolve_topic_ids.
        topic_ids = self._resolve_topic_ids(topic_labels)

        with get_db() as db:
            cursor = db.conn.cursor()

            for topic_id in topic_ids:
                # Get chunks with this topic
                cursor.execute("""
                    SELECT c.id, c.book_id, c.chunk_index, c.text, c.word_count, b.title, b.author
                    FROM chunks c
                    JOIN chunk_topics ct ON c.id = ct.chunk_id
                    JOIN books b ON c.book_id = b.id
                    WHERE ct.topic_id = ?
                    ORDER BY b.title, c.chunk_index
                    LIMIT ?
                """, (topic_id, limit_per_topic))

                for chunk_id, book_id, chunk_index, text, word_count, book_title, author in cursor.fetchall():
                    if chunk_id in seen_chunk_ids:
                        continue

                    seen_chunk_ids.add(chunk_id)

                    # Get all topics for chunk
                    cursor.execute("""
                        SELECT t.label
                        FROM topics t
                        JOIN chunk_topics ct ON t.id = ct.topic_id
                        WHERE ct.chunk_id = ?
                        ORDER BY t.label
                    """, (chunk_id,))

                    chunk_topics = [row[0] for row in cursor.fetchall()]

                    chunks.append({
                        "chunk_id": chunk_id,
                        "book_id": book_id,
                        "book_title": book_title,
                        "author": author,
                        "chunk_index": chunk_index,
                        "text": text,
                        "word_count": word_count,
                        "topics": chunk_topics
                    })

        return chunks

    def filter_overused_chunks(
        self, chunks: List[Dict], max_appearances: int = 2
    ) -> List[Dict]:
        """Drop candidate chunks already at/over the cross-trail reuse cap.

        The quality gate (check_excerpt_overuse) rejects a finished trail if any
        excerpt appears in MORE THAN max_appearances existing trails. Selecting
        such a chunk here guarantees a post-hoc rejection, so we exclude any chunk
        already appearing in >= max_appearances trails (no room left under the cap)
        BEFORE the LLM ever sees it. Uses the same corpus + fingerprint logic as
        the gate so the two never drift.
        """
        corpus = build_existing_trail_corpus(TRAILS_DIR)
        if not corpus:
            return chunks  # fresh library, nothing to exclude

        kept: List[Dict] = []
        dropped = 0
        for chunk in chunks:
            appearances = count_excerpt_appearances(chunk.get("text", ""), corpus)
            if appearances >= max_appearances:
                dropped += 1
                continue
            kept.append(chunk)

        if dropped:
            print(f"   Excluded {dropped} candidate chunk(s) already at the "
                  f"cross-trail reuse cap; {len(kept)} fresh candidates remain.")
        return kept

    def sequence_excerpts(self, trail_idea: Dict, chunks: List[Dict]) -> List[Dict]:
        """
        Use the configured LLM to sequence excerpts narratively

        Args:
            trail_idea: Trail idea dict with title, description, target_topics
            chunks: List of candidate chunks

        Returns:
            List of excerpt dicts with chunk_id, book_title, author, text, sequence_order, rationale
        """
        # Build prompt
        prompt = f"""You are curating a syntopic reading "trail" - a narrative sequence of excerpts across multiple books.

# Trail Concept:
Title: {trail_idea['title']}
Description: {trail_idea['description']}
Target Topics: {', '.join(trail_idea['target_topics'])}

# Available Chunks ({len(chunks)} total):
"""

        for i, chunk in enumerate(chunks[:50], 1):  # Limit to 50 chunks to fit in context
            prompt += f"\n[Chunk {i}] \"{chunk['book_title']}\" by {chunk['author']}\n"
            prompt += f"Topics: {', '.join(chunk['topics'][:5])}\n"
            prompt += f"Text: {chunk['text'][:500]}...\n"

        prompt += f"""

# Task:
Select {MAX_EXCERPTS_PER_TRAIL} excerpts that:
- Best illustrate the trail concept
- Span at least 4 distinct books, with no single book contributing more than
  45% of the excerpts
- Create a coherent narrative arc
- Show thematic evolution or contrast

# Output Format (JSON):
{{
  "excerpts": [
    {{
      "chunk_number": 1,
      "rationale": "Why this excerpt matters (1 sentence)"
    }}
  ]
}}

Return ONLY valid JSON - no markdown, no explanations, just the JSON object."""

        # Call the configured LLM via the structured-JSON path: schema mirrors the
        # Output Format in the prompt; the client guarantees a parsed object
        # (tool-use / JSON mode + truncation detection + retries) and raises a
        # catchable LLMError on terminal failure - never a bare JSONDecodeError
        # from a truncated 2000-token response.
        schema = {
            "type": "object",
            "properties": {
                "excerpts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "chunk_number": {"type": "integer"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["chunk_number", "rationale"],
                    },
                },
            },
            "required": ["excerpts"],
        }
        data = self.client.complete_json("", prompt, schema=schema, max_tokens=4096)
        selected_excerpts = data.get("excerpts", [])

        # Map chunk numbers back to full chunks
        excerpts = []
        for i, selection in enumerate(selected_excerpts, 1):
            chunk_num = selection["chunk_number"]
            if chunk_num < 1 or chunk_num > len(chunks[:50]):
                continue

            chunk = chunks[chunk_num - 1]
            from src.readers.excerpt_repair import repair_excerpt
            excerpts.append({
                "chunk_id": chunk["chunk_id"],
                "book_title": chunk["book_title"],
                "author": chunk["author"],
                "chunk_index": chunk["chunk_index"],
                # Phase 8.1 Fix 2: clean sentence edges + strip structural junk
                "text": repair_excerpt(chunk["text"]),
                "topics": chunk["topics"],
                "sequence_order": i,
                "rationale": selection["rationale"]
            })

        return excerpts

    def research_trail(self, trail_idea: Dict) -> List[Dict]:
        """
        Complete research workflow for trail idea

        Args:
            trail_idea: Trail idea dict from ideation agent

        Returns:
            List of sequenced excerpts with full context
        """
        # Find candidate chunks
        chunks = self.find_chunks_for_topics(
            topic_labels=trail_idea['target_topics'],
            limit_per_topic=20
        )

        if not chunks:
            raise ValueError(f"No chunks found for topics: {trail_idea['target_topics']}")

        # Exclude excerpts already at the cross-trail reuse cap before the LLM
        # selects, so the quality gate's overuse check can't reject us post-hoc.
        chunks = self.filter_overused_chunks(chunks)
        if not chunks:
            raise ValueError(
                "All candidate chunks are already at the cross-trail reuse cap "
                f"for topics: {trail_idea['target_topics']}. Try a different/broader theme."
            )

        # Sequence excerpts
        excerpts = self.sequence_excerpts(trail_idea, chunks)

        if not excerpts:
            raise ValueError("No excerpts selected by the LLM")

        # Verify cross-book coverage (matches the stage-3.5 gate's minimum in
        # src/agent/trail_constraints.py: check_minimum_book_diversity)
        unique_books = set(e["book_title"] for e in excerpts)
        if len(unique_books) < 4:
            print(f"⚠️  Warning: Trail only spans {len(unique_books)} book(s) - target is ≥4")

        return excerpts


def main():
    """CLI for testing research agent"""
    import argparse

    parser = argparse.ArgumentParser(description="Research trail excerpts")
    parser.add_argument("--title", required=True, help="Trail title")
    parser.add_argument("--description", required=True, help="Trail description")
    parser.add_argument("--topics", required=True, help="Comma-separated topic labels")

    args = parser.parse_args()

    # Build trail idea
    trail_idea = {
        "title": args.title,
        "description": args.description,
        "target_topics": [t.strip() for t in args.topics.split(",")],
        "rationale": "User-provided trail idea"
    }

    # Initialize agent
    agent = ResearchAgent()

    # Research excerpts
    print(f"\n📚 Researching excerpts for: \"{trail_idea['title']}\"")
    print(f"🎯 Topics: {', '.join(trail_idea['target_topics'])}\n")

    excerpts = agent.research_trail(trail_idea)

    # Display results
    unique_books = set(e["book_title"] for e in excerpts)
    print(f"\n✅ Selected {len(excerpts)} excerpts across {len(unique_books)} books:\n")

    for excerpt in excerpts:
        print(f"{excerpt['sequence_order']}. \"{excerpt['book_title']}\" by {excerpt['author']}")
        print(f"   Rationale: {excerpt['rationale']}")
        print(f"   Topics: {', '.join(excerpt['topics'][:5])}")
        print(f"   Text: {excerpt['text'][:200]}...")
        print()


if __name__ == "__main__":
    main()
