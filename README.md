# Syntopic Reading

**Read *across* books, not just *through* them.**

Most tools help you read one book at a time. This one is built for the older, harder discipline
Mortimer Adler called **syntopical reading**: holding many books in view at once and reading them
*against a single question* - letting authors who never met argue with each other on a theme you care
about.

Point it at a library of books and it will: parse and chunk them, extract topics, build a cross-book
topic graph, and generate **reading trails** - ordered sequences of excerpts drawn from *different*
books, stitched together by the ideas that connect them.

### See what it produces

→ **[`examples/the-porous-membrane.md`](examples/the-porous-membrane.md)** - a real trail on *"why do
some communities absorb their eccentrics while others expel them?"*, moving across ten books (Greene,
Haidt, Hawthorne, Brontë, Alexievich, Pink, Ferguson, Tergit, Rand, Doyle) from Athenian ostracism to
a Mumbai lunch-delivery network to Nazi Berlin. That range - one question, ten authors - is the whole
point.

### One idea worth internalizing first

**A trail is worthless until your own live question enters the loop.** This is not a machine that
reads *for* you. Fed a vague prompt, it will produce a competent-looking list you'll forget in a day.
Its value shows up only when you arrive with a real question you're actually chasing - an essay you're
writing, a decision you're stuck on, a tension you can't resolve - and use the trail as *evidence and
scaffolding* for your own thinking. The tool surfaces the cross-book connections; the judgment stays
yours. Build the loop with yourself in it, or don't bother.

## What would I use this for?

You bring a real question and a shelf of books that might bear on it; the tool finds where they
connect. For instance:

- **"What do my books actually agree on about building habits that stick?"** - across a few
  psychology / productivity titles.
- **"How should I think about money versus happiness?"** - a personal-finance book, a philosophy
  book, and a memoir.
- **"What actually makes a team work?"** - across the management books you already own.

It's a cross-reference for your own library: instead of re-reading five books, you ask the question
once and get the passages that bear on it - from across all of them, with the connections drawn. The
[example trail](examples/the-porous-membrane.md) shows how far that can reach; most of the time you'll
point it at something far more everyday.

---

## Quickstart (zero library prep)

You'll need Python 3.10+, a [Google Gemini API key](https://aistudio.google.com/apikey) (topic
extraction) and an [Anthropic API key](https://console.anthropic.com/) (trail generation).

```bash
git clone https://github.com/WCJR-2029/syntopic-reading.git && cd syntopic-reading
pip install -e .

cp .env.example .env          # then paste your two API keys into .env

# Fetch ~6 public-domain classics from Project Gutenberg and ingest them:
python scripts/bootstrap_demo.py

# Generate a trail on a theme that spans them:
python -m src.agent.trail_generator --idea "the tension between individual liberty and the demands of virtue"
```

The demo downloads only public-domain books, so you can try the whole pipeline end-to-end without
touching your own library.

## Bring your own library

The tool ingests **DRM-free EPUB** files. To add your own books legally and get good results, see
**[PREPARING-YOUR-LIBRARY.md](PREPARING-YOUR-LIBRARY.md)** - it covers formats, free conversion tools
(Calibre / Pandoc) for every OS, legal sources, metadata hygiene, and the corpus size the tool needs.

```bash
python scripts/add_book.py /path/to/your-book.epub
```

`add_book.py` initializes the database on first run and is idempotent - re-running resumes or repairs
rather than duplicating.

## How it works

```
EPUB → parse & chunk → topic extraction (Gemini) → embeddings (all-MiniLM-L6-v2)
     → cross-book topic graph (PMI co-occurrence, Leiden clustering, novelty scoring)
     → trail generation (Claude): excerpts sequenced across books + connection notes
```

- **Embeddings:** `all-MiniLM-L6-v2` (384-dim, runs locally, English).
- **Metadata:** uses Calibre's `ebook-meta` if present for cleaner title/author; degrades gracefully
  to the filename if not (Calibre is a recommended, not required, dependency).

## What it is / isn't

- **Is:** a personal thinking instrument for reading across a themed set of books; a reference
  implementation of a syntopic-reading pipeline.
- **Isn't:** a summarizer, a replacement for reading, or useful on a single book - trails are
  cross-book by design (the quality gate wants **≥4 books** on a shared theme).

## Status & maintenance

Built for the author's own use and shared as a reference implementation. **Provided as-is and
essentially unmaintained** - issues may go unanswered. Contributions welcome under the rules in
[CONTRIBUTING.md](CONTRIBUTING.md) (notably: **no copyrighted content, no DRM tooling**).

## Using this with a personalized AI setup

Trails are plain markdown files - so if you run a personalized-AI or filesystem-context workflow,
they double as ready-made context artifacts your assistant can read directly, and the pipeline itself
can be wrapped as a skill or scheduled job. Nothing here assumes a particular framework; it's just
files on disk.

## License

MIT - see [LICENSE](LICENSE).
