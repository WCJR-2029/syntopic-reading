"""EPUB parser using selectolax for fast HTML parsing"""

import zipfile
from pathlib import Path
from typing import Dict, List, Optional
from selectolax.parser import HTMLParser


class EPUBParser:
    """Parse EPUB files and extract text content"""

    def __init__(self, epub_path: str | Path):
        self.epub_path = Path(epub_path)
        if not self.epub_path.exists():
            raise FileNotFoundError(f"EPUB file not found: {self.epub_path}")

    def extract_text(self) -> str:
        """Extract all text content from EPUB"""
        text_parts = []

        with zipfile.ZipFile(self.epub_path, 'r') as epub:
            # Get list of HTML/XHTML content files
            content_files = self._find_content_files(epub)

            # Parse each content file
            for file_path in content_files:
                try:
                    html_content = epub.read(file_path).decode('utf-8', errors='ignore')
                    text = self._extract_text_from_html(html_content)
                    if text.strip():
                        text_parts.append(text)
                except Exception as e:
                    print(f"Warning: Could not parse {file_path}: {e}")
                    continue

        return '\n\n'.join(text_parts)

    def extract_metadata(self) -> Dict[str, Optional[str]]:
        """Extract metadata (title, author, etc.) from EPUB"""
        metadata = {
            'title': None,
            'author': None,
            'isbn': None,
        }

        try:
            with zipfile.ZipFile(self.epub_path, 'r') as epub:
                # Try to find content.opf or similar metadata file
                metadata_file = None
                for name in epub.namelist():
                    if name.endswith('.opf') or 'content.opf' in name.lower():
                        metadata_file = name
                        break

                if metadata_file:
                    opf_content = epub.read(metadata_file).decode('utf-8', errors='ignore')
                    metadata = self._parse_opf_metadata(opf_content)

        except Exception as e:
            print(f"Warning: Could not extract metadata: {e}")

        # If no metadata found, use filename as title
        if not metadata['title']:
            metadata['title'] = self.epub_path.stem.replace('_', ' ').replace('-', ' ')

        return metadata

    def _find_content_files(self, epub: zipfile.ZipFile) -> List[str]:
        """Find HTML/XHTML content files in EPUB"""
        content_files = []

        for name in epub.namelist():
            # Skip common non-content files
            if any(skip in name.lower() for skip in ['toc.', 'nav.', 'cover.']):
                continue

            # Include HTML/XHTML files
            if name.endswith(('.html', '.xhtml', '.htm')):
                content_files.append(name)

        # Sort to maintain reading order (usually numbered)
        return sorted(content_files)

    def _extract_text_from_html(self, html: str) -> str:
        """Extract clean text from HTML using selectolax"""
        # Strip XHTML namespace declarations that confuse selectolax
        html = html.replace(' xmlns="http://www.w3.org/1999/xhtml"', '')
        # Convert XHTML self-closing non-void tags (e.g. <title/>) to paired tags
        # so selectolax doesn't treat them as open tags that swallow subsequent content
        import re as _re
        html = _re.sub(r'<(title)\s*/>', r'<\1></\1>', html)
        tree = HTMLParser(html)

        # Remove script and style elements
        for tag in tree.css('script, style'):
            tag.decompose()

        # Get text from body if available, otherwise entire doc
        body = tree.css_first('body')
        if body:
            text = body.text(separator='\n', strip=True)
        else:
            text = tree.text(separator='\n', strip=True)

        # Clean up excessive whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)

        return text

    def _parse_opf_metadata(self, opf_content: str) -> Dict[str, Optional[str]]:
        """Parse OPF metadata file.

        OPF uses namespaced XML tags (<dc:title>, <dc:creator>). selectolax's
        HTML parser keeps the colon in the tag name ('dc:creator'), which no
        CSS selector can reach — css_first('dc\\:creator') and the bare
        fallbacks ('creator') both return None. Match on tag names via
        traversal instead.
        """
        tree = HTMLParser(opf_content)

        metadata = {
            'title': None,
            'author': None,
            'isbn': None,
        }

        identifiers = []
        for node in tree.root.traverse():
            tag = node.tag
            if tag in ('dc:title', 'title') and not metadata['title']:
                metadata['title'] = node.text(strip=True) or None
            elif tag in ('dc:creator', 'creator') and not metadata['author']:
                # First creator only — later ones are translators/editors
                metadata['author'] = node.text(strip=True) or None
            elif tag in ('dc:identifier', 'identifier'):
                identifiers.append(node)

        # ISBN: prefer an identifier explicitly marked as ISBN (opf:scheme
        # attr or id like "EbookISBN"); otherwise accept an unmarked one only
        # if its digits look like an ISBN (10/13), so UUIDs don't leak in.
        def _clean_isbn(text: str) -> str:
            return ''.join(c for c in text if c.isdigit() or c in ('-', 'X')).strip('-')

        marked = next(
            (n for n in identifiers
             if 'isbn' in ' '.join(f"{k} {v or ''}" for k, v in n.attributes.items()).lower()),
            None,
        )
        if marked is not None:
            metadata['isbn'] = _clean_isbn(marked.text(strip=True)) or None
        else:
            for node in identifiers:
                cleaned = _clean_isbn(node.text(strip=True))
                if len(cleaned.replace('-', '')) in (10, 13):
                    metadata['isbn'] = cleaned
                    break

        return metadata


def parse_epub(epub_path: str | Path) -> tuple[str, Dict[str, Optional[str]]]:
    """
    Convenience function to parse EPUB and return text + metadata

    Returns:
        tuple: (full_text, metadata_dict)
    """
    parser = EPUBParser(epub_path)
    text = parser.extract_text()
    metadata = parser.extract_metadata()

    return text, metadata


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.readers.epub_parser <path_to_epub>")
        sys.exit(1)

    epub_file = sys.argv[1]
    text, metadata = parse_epub(epub_file)

    print(f"\n✓ Parsed EPUB: {epub_file}")
    print(f"\nMetadata:")
    print(f"  Title: {metadata.get('title', 'Unknown')}")
    print(f"  Author: {metadata.get('author', 'Unknown')}")
    print(f"  ISBN: {metadata.get('isbn', 'N/A')}")
    print(f"\nText extracted: {len(text)} characters, {len(text.split())} words")
    print(f"\nFirst 500 characters:")
    print(text[:500])
