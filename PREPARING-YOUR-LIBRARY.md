# Preparing Your Library

The fastest way to see this tool work is `python scripts/bootstrap_demo.py` - it downloads a
handful of public-domain books and skips all of the steps below. Read this file when you're ready
to point the tool at books you actually care about.

## 1. Accepted format: EPUB, and it must be DRM-free

The ingestion pipeline reads **EPUB only**. If your book is a PDF, a MOBI/AZW file, a scanned
image, or anything else, it needs to be converted to EPUB first (see below).

It also needs to be **DRM-free**. DRM (digital rights management) is a technical lock some
retailers put on files to prevent copying and format conversion. This project does not include,
document, or link to any DRM-removal tool, and won't - that's a legal question you need to answer
for yourself, separate from this tool. What follows assumes you're starting from a file you
already have the legal right to use in whatever format you like: something you bought DRM-free,
downloaded from a public-domain source, or wrote yourself.

## 2. Converting formats you already have the right to use

These are general-purpose file-format converters. Using them to convert a PDF or DOCX you own into
EPUB is no different from converting a Word document to PDF - it's a format change, not a rights
change.

**Calibre** (free, Windows / macOS / Linux) - the standard tool for e-book library management.
Use it to:
- Convert PDF, MOBI, AZW3, and most other e-book formats into EPUB (`Convert Books` in the
  toolbar).
- Clean up or correct a book's title/author metadata before or after conversion (`Edit Metadata`).
- Provide the optional `ebook-meta` command-line tool this project uses for better metadata
  extraction (see section 4).

Get it at [calibre-ebook.com](https://calibre-ebook.com) - same installer and workflow on all
three platforms.

**Pandoc** (free, Windows / macOS / Linux) - a general document converter. Useful if the material
you want to add is your own writing, notes, or research in Markdown, DOCX, or HTML - Pandoc will
turn any of those into a valid EPUB:

```bash
pandoc my-notes.md -o my-notes.epub --metadata title="My Notes" --metadata author="Your Name"
```

Get it at [pandoc.org](https://pandoc.org).

## 3. Legal sources for books

- **Project Gutenberg** ([gutenberg.org](https://www.gutenberg.org)) and **Standard Ebooks**
  ([standardebooks.org](https://standardebooks.org)) - public-domain classics, already in clean
  EPUB format. No conversion needed. `scripts/bootstrap_demo.py` pulls from Gutenberg directly.
- **DRM-free purchases** - many independent retailers and some publishers (O'Reilly, many
  university presses, direct-from-author sales) sell EPUB with no DRM at all. If a book you bought
  is DRM-free, it's ready to use as-is.
- **Your own writing** - essays, notes, manuscripts - converted with Pandoc as above.

## 4. Metadata quality matters

The pipeline reads a book's **title and author** straight from its EPUB metadata, and that
metadata drives how trails cite and group excerpts. Clean metadata produces clean trails; a book
with a missing author or a garbled title will still ingest, but the trail that references it will
look worse for it.

If Calibre's `ebook-meta` command-line tool is installed, the pipeline will use it to read more
reliable metadata than the EPUB's raw internal fields; if it's not installed, ingestion still
works, just with slightly weaker metadata inference. Either way, if a book's metadata looks wrong
after adding it, open it in Calibre's `Edit Metadata` dialog, fix it there, and re-export.

## 5. This is a syntopic tool - it needs a corpus, not a book

"Syntopic" means reading *across* books, not one at a time. A single book, no matter how good,
produces nothing useful here - there's nothing to cross-reference it against. The internal quality
gate reflects this directly: it wants **at least 4 books** sharing a theme, with **no single
author providing more than 45%** of that set. Load one book on "resilience" and the tool has
nothing to do; load four or five that approach resilience from different angles - psychology,
history, fiction, systems theory - and it starts finding the bridges between them.

Plan your library additions in themed batches, not one-offs.

## 6. English only, for now

The embedding model (`all-MiniLM-L6-v2`) was trained on English text. Books in other languages
will ingest without erroring, but topic extraction and trail quality degrade - the model simply
doesn't understand the text as well. If your library is multilingual, expect the best results from
the English-language portion.

## Where to start

- **No library yet, just want to see it work**: run `python scripts/bootstrap_demo.py`. It
  downloads a small set of public-domain books from Project Gutenberg and ingests them for you -
  zero prep required.
- **Ready to add your own book**: convert it to DRM-free EPUB per the steps above, then run
  `python scripts/add_book.py <file.epub>`.
