"""Polish Agent - Adds highlights, formatting, and connection notes"""

import re
from typing import List, Dict, Optional

from src.llm.client import get_client

# Sentence-ending characters a dewrapped fragment must end with to plausibly
# close a real paragraph/sentence, rather than a mid-sentence hard-wrap break.
_TERMINAL_CHARS = ('.', '!', '?', '"', '”', '’', ')', ':', ';')


def _dewrap_excerpt(text: str) -> str:
    """Collapse hard-wrapped line breaks in a chunked excerpt back into clean
    paragraphs.

    Source excerpt text comes out of the EPUB parser with a newline inserted at
    every HTML text-node boundary (selectolax's `body.text(separator='\\n', ...)`),
    including inline tags like `<i>`, so a single sentence can be shredded into a
    dozen one-word "lines". The chunker (see src/readers/chunker.py) then further
    re-joins many of those fragments with a blank line (`\\n\\n`) whenever the
    source HTML section was long enough to hit its >1000-word re-split branch. The
    upshot: by the time text reaches here, both single and double newlines can mark
    a mid-sentence hard-wrap rather than a genuine paragraph break, and the two are
    not reliably distinguishable by newline count alone.

    Heuristic used here (deliberately conservative, render-layer only - no upstream
    parser/chunker change): split on ANY run of newlines into fragments, then only
    start a new paragraph where the previous fragment ends on terminal punctuation
    (a real sentence/clause end); otherwise rejoin fragments with a single space.
    This can't perfectly recover the original paragraphing (that information doesn't
    survive the chunker), but it eliminates the blank-line fragmentation that broke
    the rendered blockquote, and it collapses wrapped sentences back into a form
    where a highlight phrase that used to span a line break can match again.
    """
    fragments = [f.strip() for f in re.split(r'\n+', text) if f.strip()]
    if not fragments:
        return text.strip()

    paragraphs = []
    current = fragments[0]
    for frag in fragments[1:]:
        if current.endswith(_TERMINAL_CHARS):
            paragraphs.append(current)
            current = frag
        else:
            current = f"{current} {frag}"
    paragraphs.append(current)

    return '\n\n'.join(paragraphs)


class PolishAgent:
    """
    Polishes trail excerpts with highlights and connection notes.

    Workflow:
    1. Identify key phrases to highlight in each excerpt
    2. Write connection notes explaining thematic links
    3. Format trail as publishable markdown
    4. Add metadata (books, topics, excerpt count)
    """

    def __init__(self, model: str = None):
        """
        Initialize polish agent

        Args:
            model: Optional LLM model override (defaults to the configured
                LLM_MODEL - see src/llm/client.py)
        """
        self.client = get_client(model=model)
        self.model = self.client.model

    def add_highlights_and_connections(self, trail_idea: Dict, excerpts: List[Dict]) -> List[Dict]:
        """
        Add highlights and connection notes to excerpts

        Args:
            trail_idea: Trail idea dict with title, description
            excerpts: List of excerpt dicts from research agent

        Returns:
            List of polished excerpts with highlight_ranges and connection_note
        """
        # Build prompt
        prompt = f"""You are polishing a syntopic reading trail by adding highlights and connection notes.

# Trail Concept:
Title: {trail_idea['title']}
Description: {trail_idea['description']}

# Excerpts to Polish:
"""

        for excerpt in excerpts:
            prompt += f"\n[Excerpt {excerpt['sequence_order']}] \"{excerpt['book_title']}\" by {excerpt['author']}\n"
            prompt += f"Text: {excerpt['text']}\n"
            prompt += f"Rationale: {excerpt['rationale']}\n"

        prompt += """

# Task:
For each excerpt:
1. Identify 1-3 KEY PHRASES to highlight (exact quotes from text)
2. Write a SETTING note (1-2 sentences) that orients a reader who has NOT read this book:
   who the author is / what the book's project is, and what is happening in this specific
   passage (who is speaking, what "they/it" refers to, what came just before). No jargon,
   no assuming familiarity. This is the on-ramp, not the argument.
3. Write a connection note (1-2 sentences) explaining how this excerpt relates to the trail theme

# Output Format (JSON):
{
  "polished_excerpts": [
    {
      "sequence_order": 1,
      "highlights": ["exact phrase 1", "exact phrase 2"],
      "setting": "Orientation for a cold reader: the book's project and what's underway in this passage",
      "connection_note": "How this excerpt connects to the trail theme"
    }
  ]
}

Return ONLY valid JSON - no markdown, no explanations, just the JSON object."""

        # Call the configured LLM via the structured-JSON path: schema mirrors the
        # Output Format in the prompt; the client guarantees a parsed object
        # (tool-use / JSON mode + truncation detection + retries with escalating
        # max_tokens) and raises a catchable LLMError on terminal failure. This
        # replaces the earlier partial hardening (max_tokens=4096 + a wrapped
        # json.loads) at this, the final and most expensive stage of trail
        # generation. max_tokens=8192 gives a full 10-excerpt trail's highlights/
        # setting/connection JSON generous headroom on the first attempt.
        schema = {
            "type": "object",
            "properties": {
                "polished_excerpts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sequence_order": {"type": "integer"},
                            "highlights": {"type": "array", "items": {"type": "string"}},
                            "setting": {"type": "string"},
                            "connection_note": {"type": "string"},
                        },
                        "required": ["sequence_order", "highlights", "setting",
                                     "connection_note"],
                    },
                },
            },
            "required": ["polished_excerpts"],
        }
        data = self.client.complete_json("", prompt, schema=schema, max_tokens=8192)
        polished = data.get("polished_excerpts", [])

        # Merge polish data back into excerpts
        polished_excerpts = []
        for excerpt in excerpts:
            # Find matching polish data
            polish_data = next((p for p in polished if p["sequence_order"] == excerpt["sequence_order"]), None)

            if polish_data:
                excerpt["highlights"] = polish_data.get("highlights", [])
                excerpt["setting"] = polish_data.get("setting", "")
                excerpt["connection_note"] = polish_data.get("connection_note", "")
            else:
                excerpt["highlights"] = []
                excerpt["setting"] = ""
                excerpt["connection_note"] = ""

            polished_excerpts.append(excerpt)

        return polished_excerpts

    def generate_synthesis(self, trail_idea: Dict, polished_excerpts: List[Dict]) -> str:
        """
        Generate a DRAFT synthesis of what the trail's books, read together,
        conclude about the trail's theme/question — a single LLM call made
        AFTER the excerpts + connection notes are assembled.

        This is deliberately provisional scaffolding, not a finished verdict:
        the tool's philosophy is that a trail is worthless until the reader's
        own live question enters the loop, so the synthesis must read as a
        draft argument to argue with, not a conclusion to accept.

        Args:
            trail_idea: Trail idea dict with title, description
            polished_excerpts: List of polished excerpts (post add_highlights_and_connections),
                each with book_title, author, text, connection_note

        Returns:
            2-3 paragraph draft synthesis (plain prose, no headers)
        """
        prompt = f"""You are drafting a PROVISIONAL synthesis for a syntopic reading trail —
a sequence of excerpts from different books, sequenced to trace one theme/question.

# Trail Theme / Question:
Title: {trail_idea['title']}
Description: {trail_idea['description']}

# Ordered Excerpts and Their Connection Notes:
"""

        for excerpt in polished_excerpts:
            prompt += (
                f"\n[Excerpt {excerpt['sequence_order']}] \"{excerpt['book_title']}\" "
                f"by {excerpt['author']}\n"
            )
            prompt += f"Text: {excerpt['text']}\n"
            prompt += f"Connection note: {excerpt.get('connection_note', '')}\n"

        prompt += """

# Task:
Write a DRAFT synthesis, 2-3 paragraphs, of what these books — READ TOGETHER, in this
order — actually argue about the theme/question above.

Hard requirements:
1. This is a PROVISIONAL ARGUMENT to be refined, NOT a final verdict. Use hedged,
   working-draft language ("one reading here is...", "taken together these suggest...",
   "a case emerges that...") rather than declarative, settled claims.
2. Argue from how the books CONNECT to each other — where they agree, extend, tension
   against, or contradict one another — not a book-by-book summary. A reader should not
   be able to reconstruct this from reading each connection note in isolation; it must
   synthesize across them.
3. Do not resolve every tension you raise. Leave at least one open thread the reader
   would need to work out for themselves — the synthesis is a starting point for the
   reader's own thinking, not a closed loop.
4. No headers, no bullet points, no meta-commentary about being an AI or about this
   being a draft — just the prose itself; its provisional character should come through
   in the language, not a disclaimer bolted onto it.

Return ONLY the synthesis prose (2-3 paragraphs) — nothing else."""

        # NOTE: no explicit `temperature=` override — this codebase's convention (every
        # other call site) is to rely on the client's default (1.0), since at least one
        # configured model (claude-sonnet-5) rejects any other value as a 400 (extended
        # thinking requires temperature=1). max_tokens has headroom above the requested
        # 2-3 paragraphs for models that lead with a thinking block.
        synthesis = self.client.complete("", prompt, max_tokens=1500)
        return synthesis.strip()

    def format_trail_markdown(self, trail_idea: Dict, polished_excerpts: List[Dict],
                               synthesis: Optional[str] = None) -> str:
        """
        Format trail as publishable markdown

        Args:
            trail_idea: Trail idea dict
            polished_excerpts: List of polished excerpts
            synthesis: Optional draft synthesis text (see generate_synthesis). When
                provided, rendered as a "## The synthesis (draft)" section after the
                last excerpt and before the generated-by footer. Omitted entirely when
                None (e.g. --no-synthesis), preserving the pre-synthesis markdown shape.

        Returns:
            Formatted markdown string
        """
        # Extract metadata
        unique_books = {}
        all_topics = set()

        for excerpt in polished_excerpts:
            book_key = (excerpt["book_title"], excerpt["author"])
            if book_key not in unique_books:
                unique_books[book_key] = {
                    "title": excerpt["book_title"],
                    "author": excerpt["author"],
                    "excerpt_count": 0
                }
            unique_books[book_key]["excerpt_count"] += 1

            for topic in excerpt["topics"]:
                all_topics.add(topic)

        # Build markdown
        md = f"# {trail_idea['title']}\n\n"
        md += f"**Description**: {trail_idea['description']}\n\n"
        md += f"**Rationale**: {trail_idea['rationale']}\n\n"

        # Metadata
        md += "---\n\n"
        md += "## Trail Metadata\n\n"
        md += f"- **Excerpts**: {len(polished_excerpts)}\n"
        md += f"- **Books**: {len(unique_books)}\n"
        md += f"- **Topics**: {len(all_topics)}\n\n"

        # Books
        md += "### Books in Trail:\n\n"
        for book in sorted(unique_books.values(), key=lambda x: x["title"]):
            _n = book['excerpt_count']
            md += f"- \"{book['title']}\" by {book['author']} ({_n} excerpt{'s' if _n != 1 else ''})\n"

        # Topics (show top 10)
        md += "\n### Key Topics:\n\n"
        for topic in sorted(list(all_topics))[:10]:
            md += f"- {topic}\n"

        # Excerpts
        md += "\n---\n\n## Trail Excerpts\n\n"

        for excerpt in polished_excerpts:
            md += f"### Excerpt {excerpt['sequence_order']}: {excerpt['book_title']}\n\n"
            md += f"**Author**: {excerpt['author']}  \n"
            if excerpt.get("setting"):
                md += f"**Setting**: {excerpt['setting']}  \n"
            md += f"**Connection**: {excerpt['connection_note']}\n\n"

            # Dewrap BEFORE applying highlights, not after: some highlight phrases
            # span what used to be a hard-wrap line break in the raw excerpt text,
            # and only match once that break is collapsed back to a plain space.
            text = _dewrap_excerpt(excerpt['text'])
            for highlight in excerpt['highlights']:
                if highlight in text:
                    text = text.replace(highlight, f"**{highlight}**")

            # Quote EVERY line (including the blank lines between paragraphs, as a
            # bare ">") so the excerpt renders as one continuous multi-line
            # blockquote instead of dropping out of the blockquote at each blank line.
            quoted = '\n'.join(f"> {line}" if line else ">" for line in text.split('\n'))
            md += f"{quoted}\n\n"

        # Synthesis (draft) — optional, sits after the last excerpt and before the
        # generated-by footer. Omitted entirely when synthesis is None.
        if synthesis:
            md += "---\n\n"
            md += "## The synthesis (draft)\n\n"
            md += ("*A starting point to argue with and refine - the tool surfaces the "
                   "connections; the conclusion is yours.*\n\n")
            md += f"{synthesis}\n\n"

        # Footer
        md += "---\n\n"
        md += "*Generated by Syntopic Reading System*  \n"
        md += f"*Trail: {trail_idea['title']}*\n"

        return md

    def polish_trail(self, trail_idea: Dict, excerpts: List[Dict],
                      include_synthesis: bool = True) -> Dict:
        """
        Complete polish workflow

        Args:
            trail_idea: Trail idea dict
            excerpts: List of excerpts from research agent
            include_synthesis: Generate the draft synthesis section (default: True).
                Pass False (wired from --no-synthesis) to skip the extra LLM call and
                omit the section entirely, reproducing the pre-synthesis markdown.

        Returns:
            Dict with polished_excerpts, markdown, and synthesis (None when skipped) fields
        """
        # Add highlights and connection notes
        polished_excerpts = self.add_highlights_and_connections(trail_idea, excerpts)

        # Draft synthesis — one LLM call over the assembled excerpts + connection notes
        synthesis = None
        if include_synthesis:
            synthesis = self.generate_synthesis(trail_idea, polished_excerpts)

        # Format as markdown
        markdown = self.format_trail_markdown(trail_idea, polished_excerpts, synthesis=synthesis)

        return {
            "polished_excerpts": polished_excerpts,
            "markdown": markdown,
            "synthesis": synthesis,
        }


def main():
    """CLI for testing polish agent"""
    print("Polish agent requires trail data from research agent")
    print("Use trail_generator.py to run full workflow")


if __name__ == "__main__":
    main()
