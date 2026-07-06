"""Tests for OPF metadata parsing — namespaced dc: tags via traversal.

Regression guard for the 2026-07-04 fix: selectolax's HTML parser keeps the
colon in namespaced tag names ('dc:creator'), so the old CSS-selector approach
(css_first('dc\\:creator, creator')) matched nothing on any standard epub —
every book landed with author=None/isbn=None from the OPF.
"""

from src.readers.epub_parser import EPUBParser


def _parse(opf: str):
    parser = EPUBParser.__new__(EPUBParser)  # no file needed for OPF parsing
    return parser._parse_opf_metadata(opf)


EPUB3_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title id="title">Reality Is Not What It Seems</dc:title>
    <dc:title id="subtitle">The Journey to Quantum Gravity</dc:title>
    <dc:creator id="creator1">Carlo Rovelli</dc:creator>
    <dc:creator id="creator2">Simon Carnell</dc:creator>
    <dc:identifier id="EbookISBN">9780735213944</dc:identifier>
  </metadata>
</package>"""


def test_epub3_namespaced_tags():
    m = _parse(EPUB3_OPF)
    assert m['title'] == 'Reality Is Not What It Seems'   # first dc:title only
    assert m['author'] == 'Carlo Rovelli'                 # first creator, not translator
    assert m['isbn'] == '9780735213944'


def test_epub2_opf_scheme_isbn():
    m = _parse("""<package><metadata>
      <dc:title>Some Book</dc:title>
      <dc:creator opf:role="aut">Jane Doe</dc:creator>
      <dc:identifier id="uuid_id">urn:uuid:12345678-1234-1234-1234-123456789012</dc:identifier>
      <dc:identifier opf:scheme="ISBN">978-0-316-45138-3</dc:identifier>
    </metadata></package>""")
    assert m['author'] == 'Jane Doe'
    assert m['isbn'] == '978-0-316-45138-3'


def test_unmarked_identifier_must_look_like_isbn():
    # A UUID identifier with no ISBN marking must not leak into the isbn field
    m = _parse("""<package><metadata>
      <dc:title>T</dc:title>
      <dc:identifier>urn:uuid:12345678-1234-1234-1234-123456789012</dc:identifier>
    </metadata></package>""")
    assert m['isbn'] is None


def test_urn_isbn_strips_stray_hyphens():
    m = _parse("""<package><metadata>
      <dc:identifier id="isbn">urn:isbn:1495227328</dc:identifier>
    </metadata></package>""")
    assert m['isbn'] == '1495227328'


def test_missing_metadata_stays_none():
    m = _parse("<package><metadata></metadata></package>")
    assert m == {'title': None, 'author': None, 'isbn': None}
