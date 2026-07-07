"""Ideation Agent - Proposes novel trail ideas using underexplored topics"""

from typing import List, Dict, Optional

from src.database.operations import get_db
from src.config import TRAILS_DIR
from src.llm.client import get_client
from src.graph.canonical_resolve import (
    CHUNK_TOPICS_CANONICAL_VIEW,
    canonical_cluster_ids,
    ensure_canonical_views,
)
from src.agent.trail_constraints import (
    build_existing_trail_corpus,
    count_excerpt_appearances,
)


class IdeationAgent:
    """
    Proposes diverse trail ideas biased toward underexplored topics.

    Uses novelty scores from Phase 3 to identify topics that are:
    - Underrepresented across the book collection
    - Semantically distant from common themes
    - High-value for cross-book synthesis

    Workflow:
    1. Query novel topics (90th percentile novelty threshold)
    2. Get book metadata for context
    3. Use the configured LLM to propose 5+ diverse trail ideas
    4. Return structured trail proposals
    """

    def __init__(self, model: str = None, percentile: int = 90, num_ideas: int = 5):
        """
        Initialize ideation agent

        Args:
            model: Optional LLM model override (defaults to the configured
                LLM_MODEL - see src/llm/client.py)
            percentile: Novelty percentile threshold (default: 90th)
            num_ideas: Number of trail ideas to generate (default: 5)
        """
        self.client = get_client(model=model)
        self.model = self.client.model
        self.percentile = percentile
        self.num_ideas = num_ideas

    def get_novel_topics(self, limit: int = 20) -> List[Dict]:
        """
        Query novel topics from database, biased toward topics that still have
        FRESH excerpts (not already mined to the cross-trail reuse cap).

        Raw novelty alone is misleading on a mature library: the highest-novelty
        topics (rare, vivid proper nouns and niche jargon) are precisely the ones
        existing trails have already exhausted, so proposing them guarantees a
        downstream reuse-cap rejection. We over-fetch the novel pool, then keep
        only topics that still have at least one under-cap chunk, scanning the
        same trail-markdown corpus the research filter and quality gate use.

        Args:
            limit: Maximum topics to return (fresh ones)

        Returns:
            List of topic dicts with id, label, frequency, novelty
        """
        with get_db() as db:
            cursor = db.conn.cursor()

            # Get novelty threshold
            cursor.execute("SELECT mean_knn_distance FROM novelty_scores ORDER BY mean_knn_distance")
            all_scores = [row[0] for row in cursor.fetchall()]

            if not all_scores:
                raise ValueError("No novelty scores found - run Phase 3 first")

            import numpy as np
            threshold = np.percentile(all_scores, self.percentile)

            # A trail needs >= MIN_BOOK_SPAN distinct books (the quality gate's
            # minimum_book_diversity check). Topics that occur in fewer books can
            # never anchor a passing trail, no matter how novel — extreme novelty
            # is dominated by frequency-1 hapax noise (a rare term that only ever
            # appears in a single book). So we require book-span at the SQL level and
            # rank by novelty within the trail-able set.
            MIN_BOOK_SPAN = 4

            # Over-fetch: we further drop reuse-exhausted topics below, so ask for
            # well more than `limit`.
            #
            # book_span counts books over the CANONICAL chunk mapping (the
            # shared chunk_topics_canonical view), so a topic merged from
            # several wordings gets credit for every book any of its aliases
            # appears in - without this, a merged topic's span is undercounted
            # and it's wrongly dropped below MIN_BOOK_SPAN.
            fetch_n = max(limit * 8, 80)
            ensure_canonical_views(cursor)
            cursor.execute(f"""
                SELECT t.id, t.label, t.frequency, ns.mean_knn_distance,
                       bs.book_span
                FROM topics t
                JOIN novelty_scores ns ON t.id = ns.topic_id
                JOIN (
                    SELECT ctc.topic_id AS topic_id,
                           COUNT(DISTINCT ch.book_id) AS book_span
                    FROM {CHUNK_TOPICS_CANONICAL_VIEW} ctc
                    JOIN chunks ch ON ctc.chunk_id = ch.id
                    GROUP BY ctc.topic_id
                ) bs ON bs.topic_id = t.id
                WHERE t.canonical_id IS NULL
                AND ns.mean_knn_distance >= ?
                AND bs.book_span >= ?
                ORDER BY ns.mean_knn_distance DESC
                LIMIT ?
            """, (threshold, MIN_BOOK_SPAN, fetch_n))
            candidates = [(r[0], r[1], r[2], r[3]) for r in cursor.fetchall()]

            corpus = build_existing_trail_corpus(TRAILS_DIR)

            fresh: List[Dict] = []
            exhausted = 0
            for topic_id, label, frequency, novelty in candidates:
                if len(fresh) >= limit:
                    break
                # If the library has no trails yet, every topic is fresh.
                if corpus and not self._topic_has_fresh_chunk(cursor, topic_id, corpus):
                    exhausted += 1
                    continue
                fresh.append({
                    "id": topic_id,
                    "label": label,
                    "frequency": frequency,
                    "novelty": novelty,
                })

            if exhausted:
                print(f"   Ideation: skipped {exhausted} novel topic(s) already mined "
                      f"to the reuse cap; surfacing {len(fresh)} with fresh excerpts.")

            # Fallback: if freshness filtering emptied the pool (heavily-mined
            # library), fall back to raw novelty so ideation still has material.
            if not fresh:
                print("   Ideation: no fully-fresh novel topics found; falling back to "
                      "raw novelty ranking.")
                for topic_id, label, frequency, novelty in candidates[:limit]:
                    fresh.append({
                        "id": topic_id,
                        "label": label,
                        "frequency": frequency,
                        "novelty": novelty,
                    })

            return fresh

    def _topic_has_fresh_chunk(self, cursor, topic_id: int, corpus: Dict[str, str],
                               max_appearances: int = 2) -> bool:
        """True if this topic has at least one chunk still under the reuse cap.

        A chunk is "fresh" if it appears in fewer than max_appearances existing
        trails — i.e., there's still room for it under the gate's overuse cap.
        Uses the shared corpus + fingerprint logic so ideation, research, and the
        quality gate all agree on what "used up" means. Chunk lookup expands over
        the topic's whole alias cluster (canonical_cluster_ids), so chunks tagged
        under a merged wording still count toward freshness.
        """
        cluster_ids = canonical_cluster_ids(cursor, topic_id) or [topic_id]
        placeholders = ",".join("?" * len(cluster_ids))
        cursor.execute(f"""
            SELECT c.text
            FROM chunks c
            JOIN chunk_topics ct ON c.id = ct.chunk_id
            WHERE ct.topic_id IN ({placeholders})
            ORDER BY c.book_id, c.chunk_index
            LIMIT 40
        """, cluster_ids)
        for (text,) in cursor.fetchall():
            if count_excerpt_appearances(text, corpus) < max_appearances:
                return True
        return False

    def get_book_metadata(self) -> List[Dict]:
        """
        Get book titles and authors for context

        Returns:
            List of book dicts with id, title, author, total_topics
        """
        with get_db() as db:
            cursor = db.conn.cursor()

            cursor.execute("""
                SELECT id, title, author, total_topics
                FROM books
                ORDER BY title
            """)

            books = []
            for book_id, title, author, total_topics in cursor.fetchall():
                books.append({
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "total_topics": total_topics
                })

            return books

    def generate_ideas(self, user_idea: Optional[str] = None) -> List[Dict]:
        """
        Generate trail ideas using the configured LLM

        Args:
            user_idea: Optional user-provided trail idea (if None, auto-generate)

        Returns:
            List of trail idea dicts with title, description, target_topics
        """
        # Get novel topics and book metadata
        novel_topics = self.get_novel_topics(limit=20)
        books = self.get_book_metadata()

        # Build prompt
        if user_idea:
            prompt_mode = "user-provided"
            idea_context = f"\n\nUser's trail idea: \"{user_idea}\"\n\nUse this as inspiration but also propose variations and alternatives."
        else:
            prompt_mode = "auto-generate"
            idea_context = "\n\nGenerate diverse trail ideas from scratch, biased toward underexplored topics."

        prompt = f"""You are a syntopic reading curator creating "trails" - thematic sequences of excerpts across multiple books.

# Available Books ({len(books)} total):
{self._format_books(books)}

# Novel/Underexplored Topics (90th percentile novelty):
These topics are semantically distant from common themes - high value for synthesis:
{self._format_topics(novel_topics)}

# Task:
Propose {self.num_ideas} diverse trail ideas. Each trail should:
- Connect insights across ≥2 books
- Focus on underexplored topics (use novel topics list)
- Explore non-obvious thematic connections
- Provide unique cross-book synthesis

# Constraints on framing:
- Avoid leaning on any single recurring metaphor across all proposals. Each
  trail should reach for a distinct organizing image.
- Prefer specific, vivid framings over generic abstractions ("architecture of X",
  "anatomy of Y") which have been overused.

# Output Format (JSON):
{{
  "trails": [
    {{
      "title": "Trail title (concise, engaging)",
      "description": "1-2 sentence description of thematic focus",
      "target_topics": ["topic 1", "topic 2", "topic 3"],
      "rationale": "Why this trail matters (1 sentence)"
    }}
  ]
}}

{idea_context}

Return ONLY valid JSON - no markdown, no explanations, just the JSON object."""

        # Call the configured LLM via the structured-JSON path: the schema below
        # mirrors the Output Format in the prompt, the client guarantees a parsed
        # object (tool-use / JSON mode + truncation detection + retries), and any
        # terminal failure surfaces as a catchable LLMError - never a bare
        # json.JSONDecodeError from a truncated 2000-token response.
        schema = {
            "type": "object",
            "properties": {
                "trails": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "target_topics": {"type": "array", "items": {"type": "string"}},
                            "rationale": {"type": "string"},
                        },
                        "required": ["title", "description", "target_topics", "rationale"],
                    },
                },
            },
            "required": ["trails"],
        }
        data = self.client.complete_json("", prompt, schema=schema, max_tokens=4096)
        trails = data.get("trails", [])

        if not trails:
            raise ValueError("No trail ideas returned from the LLM")

        # Defensive shape check: downstream indexes trail_idea['title'], so a
        # malformed payload the client could not repair must fail cleanly here
        # (the caller catches ValueError and skips the trail) rather than
        # crashing later with an opaque "string indices must be integers".
        if not isinstance(trails, list) or not all(
            isinstance(t, dict) and "title" in t for t in trails
        ):
            first = type(trails[0]).__name__ if isinstance(trails, list) and trails else "n/a"
            raise ValueError(
                f"ideation returned malformed trail ideas "
                f"(got {type(trails).__name__}, first item {first})"
            )

        return trails

    def generate_anchor_title_desc(
        self,
        anchor_book: Optional[str],
        seed_topics: List[str],
        sample_texts: Optional[List[str]] = None,
        user_hint: Optional[str] = None,
    ) -> Dict:
        """STAGE-1 for anchored trails: author ONLY a title + description.

        In anchored mode the centroid + shared-topic bridges (not ideation) drive
        retrieval (ADR-005). Ideation's sole remaining job is framing: a title and
        description for the trail steered at ``anchor_book`` / ``seed_topics``. No
        target topics are invented here — retrieval already has them.

        Returns a trail_idea dict compatible with the research/polish/gate shape:
        ``{title, description, target_topics, rationale}`` where target_topics is
        the resolved seeds (metadata/context only, not a retrieval input).
        """
        anchor_line = f'anchored on the book "{anchor_book}"' if anchor_book else "topic-steered"
        samples = ""
        if sample_texts:
            samples = "\n\n# Sample passages from the anchor:\n" + "\n".join(
                f"- {t[:280]}..." for t in sample_texts[:3]
            )
        hint = f'\n\nUser framing hint: "{user_hint}"' if user_hint else ""

        prompt = f"""You are titling a syntopic reading "trail" - a curated sequence of \
excerpts across multiple books, {anchor_line}.

# Steering topics (the trail's conceptual center):
{', '.join(seed_topics)}{samples}{hint}

# Task:
Write a concise, vivid TITLE and a 1-2 sentence DESCRIPTION for this trail. The
title should evoke the cross-book theme, not name the anchor book directly.
Prefer specific, concrete framings over generic abstractions ("architecture of X",
"anatomy of Y").

# Output Format (JSON):
{{"title": "...", "description": "..."}}

Return ONLY valid JSON - no markdown, no explanations, just the JSON object."""

        # Structured-JSON path (see generate_ideas): tiny payload, but the same
        # guarantee - parsed object or a clear LLMError, never a JSON traceback.
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title"],
        }
        data = self.client.complete_json("", prompt, schema=schema)

        return {
            "title": data["title"],
            "description": data.get("description", ""),
            "target_topics": list(seed_topics),
            "rationale": f"Anchored trail steered by: {', '.join(seed_topics[:4])}",
        }

    def _format_books(self, books: List[Dict]) -> str:
        """Format book list for prompt"""
        lines = []
        for book in books:
            lines.append(f"- \"{book['title']}\" by {book['author']} ({book['total_topics']} topics)")
        return "\n".join(lines)

    def _format_topics(self, topics: List[Dict]) -> str:
        """Format topic list for prompt"""
        lines = []
        for topic in topics[:15]:  # Show top 15 novel topics
            lines.append(f"- {topic['label']} (novelty: {topic['novelty']:.4f}, freq: {topic['frequency']})")
        return "\n".join(lines)


def main():
    """CLI for testing ideation agent"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate trail ideas")
    parser.add_argument("--idea", type=str, help="User-provided trail idea (optional)")
    parser.add_argument("--num-ideas", type=int, default=5, help="Number of ideas to generate")
    parser.add_argument("--percentile", type=int, default=90, help="Novelty percentile threshold")

    args = parser.parse_args()

    # Initialize agent
    agent = IdeationAgent(
        percentile=args.percentile,
        num_ideas=args.num_ideas
    )

    # Generate ideas
    print(f"\n🎯 Generating {args.num_ideas} trail ideas...")
    if args.idea:
        print(f"📝 User idea: \"{args.idea}\"")
    print(f"🔬 Using {args.percentile}th percentile novel topics\n")

    trails = agent.generate_ideas(user_idea=args.idea)

    # Display results
    print(f"\n✅ Generated {len(trails)} trail ideas:\n")
    for i, trail in enumerate(trails, 1):
        print(f"{i}. {trail['title']}")
        print(f"   Description: {trail['description']}")
        print(f"   Topics: {', '.join(trail['target_topics'])}")
        print(f"   Rationale: {trail['rationale']}")
        print()


if __name__ == "__main__":
    main()
