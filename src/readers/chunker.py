"""Text chunking with paragraph alignment"""

from typing import List
from dataclasses import dataclass


@dataclass
class Chunk:
    """Represents a text chunk"""
    text: str
    word_count: int
    chunk_index: int
    start_position: int
    end_position: int


class TextChunker:
    """Chunk text into ~500-word segments aligned to paragraph boundaries"""

    def __init__(self, target_words: int = 500, tolerance: float = 0.2):
        """
        Initialize chunker

        Args:
            target_words: Target chunk size in words (default 500)
            tolerance: Acceptable deviation as percentage (default 0.2 = 20%)
        """
        self.target_words = target_words
        self.tolerance = tolerance
        self.min_words = int(target_words * (1 - tolerance))
        self.max_words = int(target_words * (1 + tolerance))

    def chunk_text(self, text: str) -> List[Chunk]:
        """
        Chunk text into ~500-word segments aligned to paragraphs

        Args:
            text: Full text to chunk

        Returns:
            List of Chunk objects
        """
        # Split into paragraphs (double newline or single newline for simpler texts)
        paragraphs = self._split_paragraphs(text)

        chunks = []
        current_chunk_parts = []
        current_word_count = 0
        current_start = 0
        chunk_index = 0

        for para in paragraphs:
            para_words = len(para.split())

            # Check if adding this paragraph exceeds maximum
            if current_word_count + para_words <= self.max_words:
                # Within tolerance, add paragraph
                current_chunk_parts.append(para)
                current_word_count += para_words

            else:
                # Would exceed maximum
                if current_word_count >= self.min_words:
                    # Current chunk is valid, save it and start new
                    chunk_text = '\n\n'.join(current_chunk_parts)
                    chunks.append(Chunk(
                        text=chunk_text,
                        word_count=current_word_count,
                        chunk_index=chunk_index,
                        start_position=current_start,
                        end_position=current_start + len(chunk_text)
                    ))

                    chunk_index += 1
                    current_start += len(chunk_text) + 2  # +2 for \n\n
                    current_chunk_parts = [para]
                    current_word_count = para_words

                else:
                    # Current chunk too small, add paragraph anyway
                    current_chunk_parts.append(para)
                    current_word_count += para_words

        # Don't forget last chunk
        if current_chunk_parts:
            chunk_text = '\n\n'.join(current_chunk_parts)
            chunks.append(Chunk(
                text=chunk_text,
                word_count=current_word_count,
                chunk_index=chunk_index,
                start_position=current_start,
                end_position=current_start + len(chunk_text)
            ))

        return chunks

    def _split_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs"""
        # Try double newline first (standard paragraph break)
        paragraphs = text.split('\n\n')

        # If paragraphs are too large, further split on single newline
        final_paragraphs = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # If paragraph is very large (>1000 words), split on single newline
            if len(para.split()) > 1000:
                sub_paras = para.split('\n')
                final_paragraphs.extend([p.strip() for p in sub_paras if p.strip()])
            else:
                final_paragraphs.append(para)

        return final_paragraphs


def chunk_text(text: str, target_words: int = 500, tolerance: float = 0.2) -> List[Chunk]:
    """
    Convenience function to chunk text

    Args:
        text: Full text to chunk
        target_words: Target chunk size (default 500)
        tolerance: Acceptable deviation (default 0.2 = 20%)

    Returns:
        List of Chunk objects
    """
    chunker = TextChunker(target_words=target_words, tolerance=tolerance)
    return chunker.chunk_text(text)


if __name__ == "__main__":
    # Test chunker
    sample_text = """This is the first paragraph.
It has multiple sentences.
This is important content.

This is the second paragraph.
It continues the story.

Third paragraph here.
Even more content.
Lots of words to create a longer text for testing.

Fourth paragraph with different content.
We need enough text to create multiple chunks.
This helps us test the chunking algorithm.

Fifth paragraph continuing the test.
More sentences here.
Testing paragraph alignment.

Sixth paragraph with additional content.
The chunker should handle this well.
Creating realistic test data.

Seventh paragraph to increase length.
We want multiple chunks in output.
This helps validate the algorithm.

Eighth paragraph for good measure.
More realistic text patterns.
Ensuring proper paragraph boundaries.

Ninth paragraph continues.
Testing edge cases.
Paragraph alignment is critical.

Tenth paragraph wraps up.
Final test content.
Validating chunk sizes."""

    chunks = chunk_text(sample_text, target_words=50, tolerance=0.3)

    print(f"\n✓ Chunked text into {len(chunks)} chunks")
    print(f"Target: 50 words ±30% (35-65 words)\n")

    for chunk in chunks:
        print(f"Chunk {chunk.chunk_index}:")
        print(f"  Words: {chunk.word_count}")
        print(f"  Position: {chunk.start_position}-{chunk.end_position}")
        print(f"  Text preview: {chunk.text[:100]}...")
        print()
