"""Trail Generator - Orchestrates 3-stage trail generation workflow"""

import sys
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from src.agent.ideation import IdeationAgent
from src.agent.research import ResearchAgent
from src.agent.polish import PolishAgent
from src.agent.trail_constraints import (
    validate_trail,
    format_violations,
    dedupe_and_backfill_excerpts,
)
from src.config import TRAILS_DIR
from src.llm.client import LLMError
from src.agent import anchored_retrieval as ar
from src.database.operations import get_db


class TrailGenerator:
    """
    Orchestrates complete trail generation workflow.

    3-Stage Process:
    1. Ideation → Propose novel trail ideas
    2. Research → Sequence excerpts across books
    3. Polish → Add highlights and formatting

    Output: Markdown file in data/trails/
    """

    def __init__(self, percentile: int = 90, num_ideas: int = 5,
                 enforce_quality_gate: bool = True, include_synthesis: bool = True):
        """
        Initialize trail generator

        Args:
            percentile: Novelty percentile for ideation (default: 90)
            num_ideas: Number of trail ideas to generate (default: 5)
            enforce_quality_gate: If False, skip STAGE 3.5 constraint validation
                (single-author cap, book-diversity minimum) and save the trail
                anyway. Use sparingly to feature a single distinctive-vocabulary
                book the gate would otherwise reject. Default: True.
            include_synthesis: Generate a "## The synthesis (draft)" section (one
                extra LLM call) appended to the trail markdown after the last
                excerpt. Default: True. Wired from --no-synthesis on the CLI.
        """
        self.ideation = IdeationAgent(percentile=percentile, num_ideas=num_ideas)
        self.research = ResearchAgent()
        self.polish = PolishAgent()
        self.enforce_quality_gate = enforce_quality_gate
        self.include_synthesis = include_synthesis
        self._book_count: Optional[int] = None

    def _library_book_count(self) -> int:
        """Total books in the active library, cached. Used to scale the quality
        gate's structural thresholds to library size (library_aware_thresholds):
        a small library should not be held to the large-corpus >=4-book rule."""
        if self._book_count is None:
            with get_db() as db:
                self._book_count = db.conn.cursor().execute(
                    "SELECT COUNT(*) FROM books"
                ).fetchone()[0]
        return self._book_count

    def _anchored_excerpts(self, pack: Dict) -> List[Dict]:
        """Convert an anchored_retrieval pack into research-style excerpt dicts.

        Anchors are sequenced first, then companions. Each excerpt gains the
        ``sequence_order`` + ``rationale`` fields the polish/gate stages expect.
        """
        excerpts: List[Dict] = []
        seq = 1
        for a in pack["anchors"]:
            excerpts.append({
                "chunk_id": a["chunk_id"],
                "book_title": a["book_title"],
                "author": a["author"],
                "chunk_index": a["chunk_index"],
                "text": a["text"],
                "topics": a["topics"],
                "sequence_order": seq,
                "rationale": "Anchor excerpt from the steered book.",
            })
            seq += 1
        for c in pack["companions"]:
            excerpts.append({
                "chunk_id": c["chunk_id"],
                "book_title": c["book_title"],
                "author": c["author"],
                "chunk_index": c["chunk_index"],
                "text": c["text"],
                "topics": c["topics"],
                "sequence_order": seq,
                "rationale": f"Matched on '{c['matched_topic']}' (similarity {c['similarity']}).",
            })
            seq += 1
        return excerpts

    def generate_trail(self, user_idea: Optional[str] = None, trail_index: int = 0,
                       anchor: Optional[Dict] = None) -> Dict:
        """
        Generate single trail (from user idea, auto-generated, or anchored).

        Args:
            user_idea: Optional user-provided trail idea
            trail_index: Index of trail idea to use (if auto-generating multiple)
            anchor: Optional anchored-mode config. When provided (dict with keys
                anchor_book, anchor_chunks, seed_topics, title, desc, k, per_book,
                exclude_books), STAGE-1 topic-invention is bypassed: the anchor
                centroid + shared-topic bridges drive retrieval and ideation only
                authors the title/description (ADR-005). The --idea path is
                untouched when anchor is None.

        Returns:
            Dict with trail_idea, excerpts, markdown, file_path
        """
        print("\n" + "="*80)
        print("SYNTOPIC TRAIL GENERATION - 3-STAGE WORKFLOW")
        print("="*80)

        spare: List[Dict] = []

        if anchor:
            # STAGE 1 (anchored): title/description ONLY — retrieval is steered.
            print("\n📝 STAGE 1: IDEATION (anchored — title/description only)")
            print("-" * 40)
            print(f"Mode: Anchored on \"{anchor.get('anchor_book') or anchor.get('anchor_chunks')}\"")

            # STAGE 2 (anchored): centroid + bridges retrieval.
            print("\n📚 STAGE 2: RESEARCH (anchored retrieval)")
            print("-" * 40)
            try:
                conn = ar.connect()
                try:
                    pack = ar.retrieve_anchored(
                        conn.cursor(),
                        anchor_book=anchor.get("anchor_book"),
                        anchor_chunks=anchor.get("anchor_chunks"),
                        seed_topics=anchor.get("seed_topics"),
                        k=anchor.get("k", 6),
                        per_book=anchor.get("per_book", 1),
                        exclude_books=anchor.get("exclude_books"),
                    )
                finally:
                    conn.close()
            except ValueError as e:
                print(f"\n❌ Anchored retrieval failed: {e}")
                return None

            if not pack["anchors"] and anchor.get("anchor_book"):
                print("\n❌ No anchor excerpts survived retrieval (book too thin / "
                      "all back-matter). Try explicit --anchor-chunks.")
                return None

            if pack["missing_seeds"]:
                print(f"   ! seed topics not found (skipped): {pack['missing_seeds']}")
            print(f"   Seeds: {', '.join(pack['seed_topics'])}")

            # STAGE 1 title/desc, informed by anchor sample text.
            print("\n   Authoring title/description from the anchor...")
            sample_texts = [a["text"] for a in pack["anchors"]] or \
                           [c["text"] for c in pack["companions"][:2]]
            try:
                trail_idea = self.ideation.generate_anchor_title_desc(
                    anchor_book=pack["anchor_book"],
                    seed_topics=pack["seed_topics"],
                    sample_texts=sample_texts,
                    user_hint=anchor.get("title") or user_idea,
                )
            except (LLMError, ValueError, KeyError) as e:
                print(f"\n❌ Ideation (anchored title/description) failed: {e}")
                print("Skipping this trail...")
                return None
            if anchor.get("title"):
                trail_idea["title"] = anchor["title"]
            if anchor.get("desc"):
                trail_idea["description"] = anchor["desc"]

            excerpts = self._anchored_excerpts(pack)
            spare = self._anchored_excerpts({"anchors": [], "companions": pack["spare"]})

            print(f"\n✅ Selected Trail Idea:")
            print(f"   Title: {trail_idea['title']}")
            print(f"   Description: {trail_idea['description']}")
        else:
            # STAGE 1: Ideation
            print("\n📝 STAGE 1: IDEATION")
            print("-" * 40)
            if user_idea:
                print(f"Mode: User-provided idea")
                print(f"Idea: \"{user_idea}\"")
            else:
                print(f"Mode: Auto-generate {self.ideation.num_ideas} ideas using novel topics")

            print("\nCalling the LLM for ideation...")
            try:
                trail_ideas = self.ideation.generate_ideas(user_idea=user_idea)
            except (LLMError, ValueError) as e:
                print(f"\n❌ Ideation failed: {e}")
                print("Skipping this trail...")
                return None

            # Select trail idea
            if trail_index >= len(trail_ideas):
                trail_index = 0

            trail_idea = trail_ideas[trail_index]

            print(f"\n✅ Selected Trail Idea:")
            print(f"   Title: {trail_idea['title']}")
            print(f"   Description: {trail_idea['description']}")
            print(f"   Topics: {', '.join(trail_idea['target_topics'])}")

            # STAGE 2: Research
            print("\n📚 STAGE 2: RESEARCH")
            print("-" * 40)
            print(f"Searching for chunks with topics: {', '.join(trail_idea['target_topics'])}")

            try:
                excerpts = self.research.research_trail(trail_idea)
            except (LLMError, ValueError) as e:
                print(f"\n❌ Research failed: {e}")
                print("Skipping this trail idea...")
                return None

        unique_books = set(e["book_title"] for e in excerpts)
        print(f"\n✅ Research Complete:")
        print(f"   Excerpts: {len(excerpts)}")
        print(f"   Books: {len(unique_books)}")
        print(f"   Books in trail: {', '.join(sorted(unique_books))}")

        # STAGE 2.5: Dedupe verbatim-duplicate excerpts (backfill from spare).
        # Prevents the quality gate's excerpt_repetition check from killing a
        # trail over an accidental duplicate (repro 2026-07-01). No-op when there
        # are no duplicates, so --idea output is unchanged in the normal case.
        excerpts, removed = dedupe_and_backfill_excerpts(excerpts, spare)
        if removed:
            print(f"   🧹 Removed {removed} duplicate excerpt(s); "
                  f"backfilled to {len(excerpts)}.")

        # STAGE 3: Polish
        print("\n✨ STAGE 3: POLISH")
        print("-" * 40)
        print("Adding highlights and connection notes...")

        try:
            polished_data = self.polish.polish_trail(
                trail_idea, excerpts, include_synthesis=self.include_synthesis
            )
        except (LLMError, ValueError) as e:
            print(f"\n❌ Polish failed: {e}")
            print("Skipping this trail (excerpts were selected but could not be polished)...")
            return None

        print(f"\n✅ Polish Complete:")
        print(f"   Highlights added: {sum(len(e.get('highlights', [])) for e in polished_data['polished_excerpts'])}")
        print(f"   Connection notes: {len(polished_data['polished_excerpts'])}")

        # STAGE 3.5: Quality Gate (constraint validation)
        print("\n🛡️  STAGE 3.5: QUALITY GATE")
        print("-" * 40)
        if not self.enforce_quality_gate:
            print("⚠️  Quality gate BYPASSED (enforce_quality_gate=False) — saving without"
                  " single-author / book-diversity validation.")
        else:
            passed, violations = validate_trail(
                trail_idea,
                polished_data["polished_excerpts"],
                trails_dir=TRAILS_DIR,
                library_book_count=self._library_book_count(),
            )
            if not passed:
                print("❌ Trail rejected by quality gate:")
                print(format_violations(violations))
                print("\n   Trail NOT saved. Try regenerating with different parameters.")
                return {
                    "rejected": True,
                    "trail_idea": trail_idea,
                    "violations": [{"name": v.name, "message": v.message} for v in violations],
                }
            print("✅ All constraints satisfied")

        # Save to file
        file_path = self._save_trail(trail_idea, polished_data["markdown"])

        print(f"\n💾 Trail Saved:")
        print(f"   File: {file_path}")

        print("\n" + "="*80)
        print("✅ TRAIL GENERATION COMPLETE")
        print("="*80 + "\n")

        return {
            "trail_idea": trail_idea,
            "excerpts": polished_data["polished_excerpts"],
            "markdown": polished_data["markdown"],
            "file_path": file_path
        }

    def generate_multiple_trails(self, user_idea: Optional[str] = None, count: int = 3,
                                 max_attempts: Optional[int] = None) -> List[Dict]:
        """
        Generate up to ``count`` trails, retrying past quality-gate rejections.

        A single ideated theme clears the gate only ~3 times in 4 on a small
        library (the sequencer occasionally over-weights one book), so a naive
        one-idea-per-slot loop leaves ``--count 1`` failing outright a quarter of
        the time. Here a rejected idea does NOT consume a slot: we keep drawing
        fresh ideas until ``count`` trails are saved or ``max_attempts`` is spent.

        Args:
            user_idea: Optional user-provided idea (generates variations)
            count: Number of trails wanted
            max_attempts: Hard cap on generation attempts (default: count*3 + 2)

        Returns:
            List of saved trail dicts (may be shorter than ``count`` if the cap
            is hit — a warning is printed in that case).
        """
        if max_attempts is None:
            max_attempts = count * 3 + 2
        print(f"\n🎯 Generating up to {count} trail(s) (max {max_attempts} attempts)...\n")

        trails: List[Dict] = []
        rejected_count = 0
        attempt = 0
        while len(trails) < count and attempt < max_attempts:
            trail = self.generate_trail(user_idea=user_idea, trail_index=attempt)
            attempt += 1
            if trail and not trail.get("rejected"):
                trails.append(trail)
            elif trail and trail.get("rejected"):
                rejected_count += 1
                if len(trails) < count and attempt < max_attempts:
                    print(f"   ↻ Gate rejected that idea; trying another "
                          f"({len(trails)}/{count} saved, attempt {attempt}/{max_attempts})…")

        print(f"\n✅ Generated {len(trails)} trail(s)"
              + (f" (after {rejected_count} gate rejection(s))" if rejected_count else ""))
        if len(trails) < count:
            print(f"⚠️  Wanted {count} but only {len(trails)} cleared the gate in "
                  f"{attempt} attempts. Try a broader --idea, add a book that bridges "
                  f"your theme, or raise --num-ideas.")
        return trails

    def _save_trail(self, trail_idea: Dict, markdown: str) -> Path:
        """
        Save trail markdown to file

        Args:
            trail_idea: Trail idea dict
            markdown: Formatted markdown content

        Returns:
            Path to saved file
        """
        # Create safe filename from title
        safe_title = "".join(c if c.isalnum() or c in (' ', '-') else '' for c in trail_idea['title'])
        safe_title = safe_title.replace(' ', '_').lower()

        # Add timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{safe_title}.md"

        # Save
        file_path = TRAILS_DIR / filename
        file_path.write_text(markdown, encoding='utf-8')

        return file_path


def main():
    """CLI for trail generation"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate syntopic reading trails")
    parser.add_argument("--idea", type=str, help="User-provided trail idea (optional)")
    parser.add_argument("--count", type=int, default=1, help="Number of trails to generate (default: 1)")
    parser.add_argument("--percentile", type=int, default=90, help="Novelty percentile (default: 90)")
    parser.add_argument("--num-ideas", type=int, default=5, help="Number of ideas to generate (default: 5)")
    parser.add_argument("--no-quality-gate", action="store_true",
                        help="Bypass STAGE 3.5 constraint validation (single-author cap, "
                             "book-diversity minimum) and save the trail anyway. Use sparingly.")
    parser.add_argument("--no-synthesis", action="store_true",
                        help="Skip the '## The synthesis (draft)' section (one extra LLM "
                             "call) that is otherwise appended after the last excerpt.")

    # --- Anchored / steered mode (Phase 7, ADR-005) ---
    anchor_group = parser.add_argument_group(
        "anchored mode",
        "Steer the trail to feature a specific book or cross-book bridge. When "
        "--anchor-book/--anchor-chunks is set, STAGE-1 topic-invention is bypassed: "
        "the anchor centroid + shared-topic bridges drive retrieval and ideation "
        "only writes the title/description.")
    anchor_group.add_argument("--anchor-book", type=str, default=None,
                              help="Title substring of a book that MUST appear in the trail.")
    anchor_group.add_argument("--anchor-chunks", type=str, default=None,
                              help="CSV chunk ids to force as anchor excerpts.")
    anchor_group.add_argument("--seed-topics", type=str, default=None,
                              help="Semicolon-separated EXISTING topic labels (the steering "
                                   "centroid). Omit to auto-derive from the anchor book.")
    anchor_group.add_argument("--title", type=str, default=None,
                              help="Explicit trail title (skips LLM title authoring).")
    anchor_group.add_argument("--desc", type=str, default=None,
                              help="Explicit trail description.")
    anchor_group.add_argument("--exclude-books", type=str, default=None,
                              help="Semicolon-separated title substrings to drop from companions.")
    anchor_group.add_argument("--k", type=int, default=6, help="Companion excerpts (default: 6).")
    anchor_group.add_argument("--per-book", type=int, default=1,
                              help="Max companion excerpts per book (default: 1).")

    args = parser.parse_args()

    # Initialize generator
    generator = TrailGenerator(
        percentile=args.percentile,
        num_ideas=args.num_ideas,
        enforce_quality_gate=not args.no_quality_gate,
        include_synthesis=not args.no_synthesis
    )

    anchored = bool(args.anchor_book or args.anchor_chunks or args.seed_topics)
    if anchored:
        anchor_cfg = {
            "anchor_book": args.anchor_book,
            "anchor_chunks": [int(x) for x in args.anchor_chunks.split(",")]
                             if args.anchor_chunks else None,
            "seed_topics": [s.strip() for s in args.seed_topics.split(";") if s.strip()]
                           if args.seed_topics else None,
            "title": args.title,
            "desc": args.desc,
            "exclude_books": [s.strip() for s in args.exclude_books.split(";") if s.strip()]
                             if args.exclude_books else None,
            "k": args.k,
            "per_book": args.per_book,
        }
        if args.count > 1:
            print("ℹ️  Anchored mode generates one steered trail per run; ignoring --count.")
        trail = generator.generate_trail(user_idea=args.idea, anchor=anchor_cfg)
        trails = [trail] if trail else []
    # Generate trails. Route ALL counts (including the default 1) through the
    # retrying path so a single gate rejection doesn't leave the user with nothing.
    else:
        trails = generator.generate_multiple_trails(user_idea=args.idea, count=args.count)

    # Summary
    saved_trails = [t for t in trails if t and not t.get("rejected")]
    if saved_trails:
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        for i, trail in enumerate(saved_trails, 1):
            print(f"\n{i}. {trail['trail_idea']['title']}")
            print(f"   Excerpts: {len(trail['excerpts'])}")
            books = set(e['book_title'] for e in trail['excerpts'])
            print(f"   Books: {', '.join(sorted(books))}")
            print(f"   File: {trail['file_path'].name}")
        print(f"\n📁 Saved to: {TRAILS_DIR}/")
    else:
        # No trail cleared the gate. Exit non-zero so scripts/CI see the failure
        # instead of a false success (the process used to always exit 0 here).
        print("\n❌ No trail cleared the quality gate.")
        print("   Try a broader --idea, add a book that bridges your theme, raise "
              "--num-ideas, or steer it with --anchor-book \"<a book in your library>\".")
        sys.exit(1)


if __name__ == "__main__":
    main()
