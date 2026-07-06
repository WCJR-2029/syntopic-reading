"""XML formatter for CLI output - Claude-parseable semi-XML format"""

from typing import Dict, List, Any, Optional
import json


class XMLFormatter:
    """Format CLI output as semi-XML for Claude Code parsing"""

    def __init__(self, pretty: bool = True):
        """
        Initialize XML formatter

        Args:
            pretty: Whether to pretty-print output
        """
        self.pretty = pretty
        self.indent_level = 0
        self.indent_str = "  "

    def _indent(self) -> str:
        """Get current indentation string"""
        if not self.pretty:
            return ""
        return self.indent_str * self.indent_level

    def _newline(self) -> str:
        """Get newline (or empty if not pretty)"""
        return "\n" if self.pretty else ""

    def tag(self, name: str, content: Any = None, **attrs) -> str:
        """
        Create XML tag with optional content and attributes

        Args:
            name: Tag name
            content: Tag content (can be None for self-closing tags)
            **attrs: Tag attributes

        Returns:
            XML string
        """
        # Build attributes string
        attr_str = ""
        if attrs:
            attr_str = " " + " ".join(f'{k}="{v}"' for k, v in attrs.items())

        indent = self._indent()

        if content is None:
            # Self-closing tag
            return f"{indent}<{name}{attr_str} />"
        elif isinstance(content, str):
            # Simple content
            return f"{indent}<{name}{attr_str}>{content}</{name}>"
        else:
            # Complex content (will be handled separately)
            return f"{indent}<{name}{attr_str}>"

    def format_topic(self, topic: Dict) -> str:
        """
        Format single topic

        Args:
            topic: Dictionary with id, label, frequency, novelty, etc.

        Returns:
            XML string
        """
        lines = [self.tag("topic", id=topic.get("id", ""))]
        self.indent_level += 1

        lines.append(self.tag("label", topic.get("label", "")))

        if "frequency" in topic:
            lines.append(self.tag("frequency", str(topic["frequency"])))

        if "novelty" in topic and topic["novelty"] is not None:
            lines.append(self.tag("novelty", f"{topic['novelty']:.4f}"))

        if "degree" in topic:
            lines.append(self.tag("degree", str(topic["degree"])))

        if "hierarchy_level" in topic:
            lines.append(self.tag("hierarchy_level", str(topic["hierarchy_level"])))

        if "community_id" in topic:
            lines.append(self.tag("community_id", topic["community_id"]))

        self.indent_level -= 1
        lines.append(self.tag("/topic"))

        return self._newline().join(lines)

    def format_topics(self, topics: List[Dict], total: Optional[int] = None) -> str:
        """
        Format list of topics

        Args:
            topics: List of topic dictionaries
            total: Total count (if different from len(topics))

        Returns:
            XML string
        """
        attrs = {"count": str(len(topics))}
        if total is not None:
            attrs["total"] = str(total)

        lines = [self.tag("topics", **attrs)]
        self.indent_level += 1

        for topic in topics:
            lines.append(self.format_topic(topic))

        self.indent_level -= 1
        lines.append(self._indent() + "</topics>")

        return self._newline().join(lines)

    def format_chunk(self, chunk: Dict) -> str:
        """
        Format single chunk

        Args:
            chunk: Dictionary with id, text, book, topics, etc.

        Returns:
            XML string
        """
        lines = [self.tag("chunk", id=chunk.get("id", ""), book_id=chunk.get("book_id", ""))]
        self.indent_level += 1

        if "book_title" in chunk:
            lines.append(self.tag("book", chunk["book_title"]))

        if "chunk_index" in chunk:
            lines.append(self.tag("index", str(chunk["chunk_index"])))

        if "word_count" in chunk:
            lines.append(self.tag("word_count", str(chunk["word_count"])))

        if "text" in chunk:
            # Truncate long text
            text = chunk["text"]
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(self.tag("text", text))

        if "topics" in chunk and chunk["topics"]:
            lines.append(self._indent() + "<topics>")
            self.indent_level += 1
            for topic_label in chunk["topics"]:
                lines.append(self.tag("topic", topic_label))
            self.indent_level -= 1
            lines.append(self._indent() + "</topics>")

        self.indent_level -= 1
        lines.append(self.tag("/chunk"))

        return self._newline().join(lines)

    def format_chunks(self, chunks: List[Dict]) -> str:
        """
        Format list of chunks

        Args:
            chunks: List of chunk dictionaries

        Returns:
            XML string
        """
        lines = [self.tag("chunks", count=str(len(chunks)))]
        self.indent_level += 1

        for chunk in chunks:
            lines.append(self.format_chunk(chunk))

        self.indent_level -= 1
        lines.append(self._indent() + "</chunks>")

        return self._newline().join(lines)

    def format_tree_node(self, node: Dict) -> str:
        """
        Format hierarchical tree node

        Args:
            node: Dictionary with community_id, level, topics, children

        Returns:
            XML string
        """
        attrs = {
            "id": node.get("community_id", ""),
            "level": str(node.get("level", 0)),
            "size": str(node.get("size", 0))
        }

        lines = [self.tag("node", **attrs)]
        self.indent_level += 1

        # Sample topics
        if "topics" in node and node["topics"]:
            lines.append(self._indent() + "<topics>")
            self.indent_level += 1
            for topic in node["topics"][:5]:  # Show first 5
                lines.append(self.tag("topic", topic))
            if len(node["topics"]) > 5:
                lines.append(self.tag("more", str(len(node["topics"]) - 5)))
            self.indent_level -= 1
            lines.append(self._indent() + "</topics>")

        # Children nodes
        if "children" in node and node["children"]:
            lines.append(self._indent() + "<children>")
            self.indent_level += 1
            for child in node["children"]:
                lines.append(self.format_tree_node(child))
            self.indent_level -= 1
            lines.append(self._indent() + "</children>")

        self.indent_level -= 1
        lines.append(self._indent() + "</node>")

        return self._newline().join(lines)

    def format_error(self, message: str, code: Optional[str] = None) -> str:
        """
        Format error message

        Args:
            message: Error message
            code: Optional error code

        Returns:
            XML string
        """
        attrs = {}
        if code:
            attrs["code"] = code

        return self.tag("error", message, **attrs)

    def format_result(self, data: Any, result_type: str = "result") -> str:
        """
        Format generic result

        Args:
            data: Result data
            result_type: Type of result

        Returns:
            XML string
        """
        lines = [f"<{result_type}>"]
        self.indent_level += 1

        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (list, dict)):
                    lines.append(self._indent() + f"<{key}>")
                    lines.append(json.dumps(value, indent=2))
                    lines.append(self._indent() + f"</{key}>")
                else:
                    lines.append(self.tag(key, str(value)))
        else:
            lines.append(self._indent() + str(data))

        self.indent_level -= 1
        lines.append(f"</{result_type}>")

        return self._newline().join(lines)


# Convenience function
def format_xml(data: Any, formatter_type: str = "result", **kwargs) -> str:
    """
    Format data as XML

    Args:
        data: Data to format
        formatter_type: Type of formatter to use
        **kwargs: Additional arguments for formatter

    Returns:
        XML string
    """
    formatter = XMLFormatter(**kwargs)

    if formatter_type == "topics":
        return formatter.format_topics(data)
    elif formatter_type == "chunks":
        return formatter.format_chunks(data)
    elif formatter_type == "error":
        return formatter.format_error(data)
    else:
        return formatter.format_result(data, result_type=formatter_type)


if __name__ == "__main__":
    # Test XML formatter
    formatter = XMLFormatter(pretty=True)

    # Test topics
    topics = [
        {"id": 1, "label": "social dynamics", "frequency": 35, "novelty": 0.3, "degree": 35},
        {"id": 2, "label": "romantic relationships", "frequency": 34, "novelty": 0.25, "degree": 34},
    ]

    print("Topics XML:")
    print(formatter.format_topics(topics, total=1851))
    print()

    # Test chunks
    chunks = [
        {
            "id": 1,
            "book_id": 1,
            "book_title": "Pride & Prejudice",
            "chunk_index": 0,
            "word_count": 500,
            "text": "It is a truth universally acknowledged...",
            "topics": ["social class", "marriage prospects", "first impressions"]
        }
    ]

    print("Chunks XML:")
    print(formatter.format_chunks(chunks))
    print()

    # Test error
    print("Error XML:")
    print(formatter.format_error("Topic not found", code="NOT_FOUND"))
