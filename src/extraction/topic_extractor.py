"""Topic extraction using Gemini to extract 3-5 topics per chunk"""

import os
import json
import time
from google import genai
from dotenv import load_dotenv
from typing import List

load_dotenv()

# Configure Gemini with new API
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


class TopicExtractor:
    """Extract 3-5 topic labels from text chunks"""

    def __init__(self, model_name: str = None, num_topics: int = 5):
        """
        Initialize topic extractor

        Args:
            model_name: Gemini model to use (default: gemini-2.5-flash-lite)
            num_topics: Target number of topics to extract (default 5)
        """
        self.model_name = model_name or os.getenv(
            "SYNTOPIC_TOPIC_MODEL", "models/gemini-2.5-flash-lite"
        )
        self.num_topics = num_topics

    _TRANSIENT_MARKERS = (
        "503", "unavailable", "overloaded", "429",
        "resource_exhausted", "rate limit", "deadline",
    )

    def _generate_with_retry(self, prompt, max_attempts: int = 5):
        """Call Gemini, retrying transient errors (503/overload/429) with
        exponential backoff. Returns the response, or None if all attempts fail."""
        delay = 2.0
        for attempt in range(1, max_attempts + 1):
            try:
                return client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                )
            except Exception as e:
                msg = str(e).lower()
                transient = any(m in msg for m in self._TRANSIENT_MARKERS)
                if transient and attempt < max_attempts:
                    print(f"  Gemini transient error (attempt {attempt}/{max_attempts}), "
                          f"retrying in {delay:.0f}s…")
                    time.sleep(delay)
                    delay *= 2
                    continue
                print(f"Warning: Gemini API error - {e}")
                return None

    def extract_topics(self, chunk_text: str) -> List[str]:
        """
        Extract topic labels from chunk

        Args:
            chunk_text: Text chunk to analyze

        Returns:
            List of topic labels (strings)
        """
        prompt = f"""Extract {self.num_topics} topic labels from this text.

Requirements:
- Specific noun phrases (e.g., "startup founder challenges", "team trust dynamics")
- Searchable across books (not overly specific to one example)
- NOT generic labels (e.g., "introduction", "chapter overview")
- Focus on concepts, themes, and ideas discussed

Text:
{chunk_text}

Return ONLY a JSON array of topic strings, nothing else.
Example: ["leadership challenges", "organizational trust", "team dysfunction patterns", "conflict resolution", "accountability frameworks"]
"""

        try:
            response = self._generate_with_retry(prompt)
            if response is None:
                return []
            text = response.text.strip()

            # Remove markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:].strip()

            # Parse JSON
            topics = json.loads(text)

            # Validate it's a list
            if not isinstance(topics, list):
                print(f"Warning: Expected list, got {type(topics)}")
                return []

            # Clean and normalize topics
            topics = [str(t).lower().strip() for t in topics if t]

            # Limit to requested number
            return topics[:self.num_topics]

        except json.JSONDecodeError as e:
            print(f"Warning: JSON parse error - {e}")
            print(f"Response: {response.text[:200]}")
            return []
        except Exception as e:
            print(f"Warning: Gemini API error - {e}")
            return []


def extract_topics(chunk_text: str, num_topics: int = 5) -> List[str]:
    """
    Convenience function to extract topics

    Args:
        chunk_text: Text chunk to analyze
        num_topics: Number of topics to extract

    Returns:
        List of topic labels
    """
    extractor = TopicExtractor(num_topics=num_topics)
    return extractor.extract_topics(chunk_text)


if __name__ == "__main__":
    # Test topic extractor
    test_chunk = """The five dysfunctions of a team begin with an absence of trust among team members.
    This occurs when team members are unwilling to be vulnerable within the group. Team
    members who are not genuinely open with one another about their mistakes and weaknesses
    make it impossible to build a foundation for trust. Without trust, teams cannot engage
    in unfiltered, passionate debate about key issues, which leads to the second dysfunction:
    fear of conflict. When teams fail to engage in productive conflict, they resort to
    artificial harmony, which prevents them from making the best possible decisions. This
    leads to a lack of commitment, as team members who don't participate in the debate are
    unlikely to buy into the decisions that are made. Without commitment, accountability
    suffers, because team members are hesitant to call their peers on actions and behaviors
    that seem counterproductive to the good of the team."""

    print("\nTesting Topic Extractor:")
    print("=" * 60)
    print(f"Text: {test_chunk[:200]}...")
    print()

    extractor = TopicExtractor(num_topics=5)
    topics = extractor.extract_topics(test_chunk)

    print(f"\nExtracted {len(topics)} topics:")
    for i, topic in enumerate(topics, 1):
        print(f"  {i}. {topic}")
