"""Usefulness filter using a provider-agnostic LLM client to classify chunks
as useful for topic extraction. See src/llm/client.py for provider config
("bring your own LLM" - defaults to Anthropic; also supports OpenAI-compatible
endpoints)."""

from src.llm.client import get_client


class UsefulnessFilter:
    """Binary classification: is chunk useful for topic extraction?"""

    def __init__(self, model_name: str = None):
        """
        Initialize usefulness filter

        Args:
            model_name: Optional LLM model override (defaults to the
                configured LLM_MODEL for low-cost, high-throughput
                classification - see src/llm/client.py)
        """
        self.client = get_client(model=model_name)

    def is_useful(self, chunk_text: str) -> bool:
        """
        Determine if chunk is useful for topic extraction

        Args:
            chunk_text: Text chunk to evaluate

        Returns:
            True if useful, False otherwise
        """
        prompt = f"""Is this text chunk useful for extracting meaningful topics?

Vote YES if:
- Contains substantive content discussing concepts/ideas
- Has 100+ words of actual content
- Discusses themes, arguments, or information

Vote NO if:
- Index entries or table of contents
- Copyright notices or legal text
- Orphaned headers without content
- Publisher information or acknowledgments
- Page numbers or navigation elements
- Less than 100 words of meaningful content

Text chunk:
{chunk_text[:1000]}

Answer with only: YES or NO"""

        try:
            answer = self.client.complete("", prompt, max_tokens=20).strip().upper()

            # Handle various response formats
            if "YES" in answer:
                return True
            elif "NO" in answer:
                return False
            else:
                # If unclear, default to useful (conservative)
                return True

        except Exception as e:
            print(f"Warning: LLM API error - {e}")
            # Default to useful if API fails
            return True


def is_chunk_useful(chunk_text: str) -> bool:
    """
    Convenience function to check if chunk is useful

    Args:
        chunk_text: Text chunk to evaluate

    Returns:
        True if useful, False otherwise
    """
    filter = UsefulnessFilter()
    return filter.is_useful(chunk_text)


if __name__ == "__main__":
    # Test usefulness filter
    test_chunks = [
        # Should be USEFUL
        """This is a substantive paragraph discussing the nature of artificial intelligence
        and its implications for society. The development of large language models has
        fundamentally changed how we think about machine cognition. These systems demonstrate
        capabilities that were previously thought to require human-level intelligence, yet
        they operate on principles that are distinctly different from biological neural networks.
        The philosophical implications are profound, raising questions about consciousness,
        understanding, and the nature of intelligence itself.""",

        # Should be NOT USEFUL
        """INDEX
        Chapter 1 ................... 1
        Chapter 2 ................... 15
        Chapter 3 ................... 32
        Appendix A .................. 95""",

        # Should be NOT USEFUL
        """Copyright © 2024 Publisher Name
        All rights reserved.
        ISBN: 978-1-234567-89-0""",

        # Should be USEFUL
        """The five dysfunctions of a team begin with an absence of trust among team members.
        This occurs when team members are unwilling to be vulnerable within the group. Team
        members who are not genuinely open with one another about their mistakes and weaknesses
        make it impossible to build a foundation for trust. Without trust, teams cannot engage
        in unfiltered, passionate debate about key issues, which leads to the second dysfunction.""",
    ]

    print("\nTesting Usefulness Filter:")
    print("=" * 60)

    filter = UsefulnessFilter()

    for i, chunk in enumerate(test_chunks, 1):
        is_useful = filter.is_useful(chunk)
        print(f"\nChunk {i}: {'USEFUL' if is_useful else 'NOT USEFUL'}")
        print(f"Preview: {chunk[:100]}...")
