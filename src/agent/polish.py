"""Polish Agent - Adds highlights, formatting, and connection notes"""

from typing import List, Dict
import anthropic

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL


class PolishAgent:
    """
    Polishes trail excerpts with highlights and connection notes.

    Workflow:
    1. Identify key phrases to highlight in each excerpt
    2. Write connection notes explaining thematic links
    3. Format trail as publishable markdown
    4. Add metadata (books, topics, excerpt count)
    """

    def __init__(self, model: str = CLAUDE_MODEL):
        """
        Initialize polish agent

        Args:
            model: Claude model to use (default: opus-4-6)
        """
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = model

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

        # Call Claude
        message = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        # Parse response
        import json
        response_text = message.content[0].text

        # Handle markdown code blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()

        data = json.loads(response_text)
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

    def format_trail_markdown(self, trail_idea: Dict, polished_excerpts: List[Dict]) -> str:
        """
        Format trail as publishable markdown

        Args:
            trail_idea: Trail idea dict
            polished_excerpts: List of polished excerpts

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
            md += f"- \"{book['title']}\" by {book['author']} ({book['excerpt_count']} excerpts)\n"

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

            # Apply highlights
            text = excerpt['text']
            for highlight in excerpt['highlights']:
                if highlight in text:
                    text = text.replace(highlight, f"**{highlight}**")

            md += f"> {text}\n\n"

        # Footer
        md += "---\n\n"
        md += "*Generated by Syntopic Reading System*  \n"
        md += f"*Trail: {trail_idea['title']}*\n"

        return md

    def polish_trail(self, trail_idea: Dict, excerpts: List[Dict]) -> Dict:
        """
        Complete polish workflow

        Args:
            trail_idea: Trail idea dict
            excerpts: List of excerpts from research agent

        Returns:
            Dict with polished_excerpts and markdown fields
        """
        # Add highlights and connection notes
        polished_excerpts = self.add_highlights_and_connections(trail_idea, excerpts)

        # Format as markdown
        markdown = self.format_trail_markdown(trail_idea, polished_excerpts)

        return {
            "polished_excerpts": polished_excerpts,
            "markdown": markdown
        }


def main():
    """CLI for testing polish agent"""
    print("Polish agent requires trail data from research agent")
    print("Use trail_generator.py to run full workflow")


if __name__ == "__main__":
    main()
