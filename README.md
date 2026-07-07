# Syntopic Reading

**Read *across* books, not just *through* them.**

Most tools help you read one book at a time. This one is built for the older, harder discipline
Mortimer Adler called **syntopical reading**: holding many books in view at once and reading them
*against a single question* - letting authors who never met argue with each other on a theme you care
about.

Point it at a library of books and it will: parse and chunk them, extract topics, build a cross-book
topic graph, and generate **reading trails** - ordered sequences of excerpts drawn from *different*
books, stitched together by the ideas that connect them.

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

## See what it produces

→ **[`examples/the-porous-membrane.md`](examples/the-porous-membrane.md)** - a real trail on *"why do
some communities absorb their eccentrics while others expel them?"*, moving across ten books (Greene,
Haidt, Hawthorne, Brontë, Alexievich, Pink, Ferguson, Tergit, Rand, Doyle) from Athenian ostracism to
a Mumbai lunch-delivery network to Nazi Berlin. That range - one question, ten authors - is the whole
point.

## One idea worth internalizing first

**A trail is worthless until your own live question enters the loop.** This is not a machine that
reads *for* you. Fed a vague prompt, it will produce a competent-looking list you'll forget in a day.
Its value shows up only when you arrive with a real question you're actually chasing - an essay you're
writing, a decision you're stuck on, a tension you can't resolve - and use the trail as *evidence and
scaffolding* for your own thinking. The tool surfaces the cross-book connections; the judgment stays
yours. Build the loop with yourself in it, or don't bother.

*The longer version of this argument: [WHY-SYNTOPIC-READING.md](WHY-SYNTOPIC-READING.md).*

---

## Do I need to be a developer? (What you actually need)

Not really - but you do need to be comfortable running a few commands in a terminal. Here's the honest bar:

- **This is a command-line tool (Python), not an app or a website.** You clone it, install it, and run it from a terminal. If you can follow copy-paste steps in a terminal, you can run it.
- **It needs an LLM API key** - Anthropic (Claude), OpenAI, an OpenAI-compatible endpoint like OpenRouter, or a **free local model** via Ollama / LM Studio. An API key is **not** the same thing as a ChatGPT Plus or Claude.ai *subscription* - it's separate, pay-as-you-go programmatic access (the demo costs a few dollars on a paid key, or nothing at all on a local model). The tool makes ~2 calls per text chunk in a loop, which is why it needs an API, not a chat window.
- **You do NOT need any special AI coding tool** (Claude Code, Cursor, etc.) to *run* it - just Python and that key.
- **Not comfortable in a terminal?** Paste this README into an AI assistant (ChatGPT, Claude, Gemini) and ask it to walk you through the install step by step for your operating system. It will happily co-pilot the whole thing.

## Quickstart (zero library prep)

You'll need Python 3.10+ and **one LLM API key**. It runs on [Anthropic (Claude)](https://console.anthropic.com/)
by default; set `LLM_PROVIDER=openai` to use OpenAI or **any OpenAI-compatible endpoint** - OpenRouter
(which fronts Claude, Gemini, GPT, Llama and more) or a local runner like Ollama / LM Studio. No Google
account required.

```bash
git clone https://github.com/WCJR-2029/syntopic-reading.git && cd syntopic-reading

# Create and activate a virtual environment first - a bare `pip install -e .` into
# system Python fails on modern Debian/Ubuntu/Fedora with a PEP 668
# "externally-managed-environment" error:
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e .
```

**Heads-up on that install:** it pulls in `sentence-transformers`, which drags along PyTorch and,
on most Linux machines, several GB of NVIDIA CUDA wheels - even if you're running CPU-only. Expect
this step to take a few minutes and use a couple of GB of disk, not finish instantly.

```bash
cp .env.example .env          # then paste your LLM API key into .env

# Fetch ~6 public-domain classics from Project Gutenberg and ingest them:
python scripts/bootstrap_demo.py

# Generate a trail on a theme that spans them:
python -m src.agent.trail_generator --idea "the tension between individual liberty and the demands of virtue"
```

The demo downloads only public-domain books, so you can try the whole pipeline end-to-end without
touching your own library. Ingesting them makes many LLM calls - roughly two per text chunk across
the six books (a usefulness check plus topic extraction) - so it costs real money and takes a while
on a paid model. To fetch the books and try the flow with no API key and no cost first, run
`python scripts/bootstrap_demo.py --download-only`.

## Bring your own library

The tool ingests **DRM-free EPUB** files. To add your own books legally and get good results, see
**[PREPARING-YOUR-LIBRARY.md](PREPARING-YOUR-LIBRARY.md)** - it covers formats, free conversion tools
(Calibre / Pandoc) for every OS, legal sources, metadata hygiene, and the corpus size the tool needs.

```bash
# 1. Add each book (repeat for every EPUB you're adding):
python scripts/add_book.py /path/to/your-book.epub

# 2. After a batch of adds, rebuild the cross-book topic graph once
#    (this also merges near-duplicate topic labels by default - see below):
python scripts/build_graph.py

# 3. Generate a trail across your library:
python -m src.agent.trail_generator --idea "your question here"
```

`add_book.py` initializes the database on first run, is idempotent - re-running resumes or repairs
rather than duplicating - and already rebuilds the topic graph itself after each book by default, so
step 2 above is optional if you're only adding one book. Adding several books in one sitting? Pass
`--defer-graph` to each `add_book.py` call and run `build_graph.py` once at the end instead - that
rebuilds the (heavyweight, whole-library) graph a single time rather than after every book.

## Trail generator flags

`python -m src.agent.trail_generator` accepts:

- `--idea "<text>"` - the question or theme to build the trail around. Omit it and the tool
  auto-generates `--num-ideas` (default 5) candidate ideas and picks one itself.
- `--count N` - how many trails to generate in one run (default 1).
- `--no-synthesis` - skip the trailing "## The synthesis (draft)" section (saves one LLM call).
- `--percentile N` - novelty percentile used when selecting topics (default 90).
- `--no-quality-gate` - bypass the diversity / single-author checks described below and save the
  trail anyway. Use sparingly - it's a debugging escape hatch, not something to ship trails with.

**Anchored mode** (steer the trail toward a specific book or cross-book bridge instead of a
free-form idea) adds: `--anchor-book "<title substring>"`, `--anchor-chunks "<csv chunk ids>"`,
`--seed-topics "<topic;topic>"`, `--title "<text>"`, `--desc "<text>"`,
`--exclude-books "<title;title>"`, `--k N` (companion excerpts, default 6), and `--per-book N`
(max excerpts per book, default 1). Run `python -m src.agent.trail_generator --help` for the full
list with descriptions.

**Output:** trails are written as timestamped Markdown to `data/trails/`. The generator retries
past quality-gate rejections automatically (a few attempts per requested trail), so a single
rejected idea won't leave you empty-handed; if nothing clears the gate it prints guidance and
exits non-zero. On a small library a narrow question that only one or two of your books address is
the usual reason a run comes up empty - broaden the `--idea`, add a book that bridges your theme,
or steer it with `--anchor-book`.

## How it works

```
EPUB → parse & chunk → topic extraction (your LLM) → embeddings (all-MiniLM-L6-v2)
     → cross-book topic graph (PMI co-occurrence, Leiden clustering, novelty scoring)
     → trail generation (your LLM): excerpts across books + connection notes + a draft synthesis
```

- **LLM:** one provider does both topic extraction and trail generation, set via `LLM_PROVIDER`
  (`anthropic` default, or `openai` for any OpenAI-compatible endpoint) and `LLM_MODEL`. Default is
  `claude-sonnet-5`. **Cost note:** topic extraction makes one LLM call per text chunk, so on a large
  library the model choice matters - Sonnet is a sensible middle; drop `LLM_MODEL` to a cheaper tier
  (Haiku / a mini model) to keep ingestion affordable, or raise it (Opus / a frontier model) for the
  richest trails.
- **Embeddings:** `all-MiniLM-L6-v2` (384-dim, runs locally, English) - no API.
- **Metadata:** uses Calibre's `ebook-meta` if present for cleaner title/author; degrades gracefully
  to the filename if not (Calibre is a recommended, not required, dependency).
- **Topic graph and topic canonicalization:** the PMI/Leiden cross-book graph only forms edges
  where the *same* topic recurs across books, but topic labels are LLM-generated and tend to be
  nearly unique per book. To compensate, `build_graph.py` **canonicalizes topics by default**: it
  merges semantically-equivalent labels ("nature of happiness" / "nature of true happiness") into
  one canonical topic before building edges, which is what lets the same idea worded differently
  across books become one graph node (on the demo library this takes edges from 20 to 50).
  The pass is reversible (it only sets `canonical_id`; no rows or chunk links are deleted),
  idempotent, and costs seconds on small libraries - budget roughly **12 extra minutes** on a very
  large (~225K-topic) one. Opt out with `--no-canonicalize`; tune with `--merge-threshold`
  (default 0.90). Even so, on a small or highly varied library the graph can still be **sparse**
  and `build_graph.py` will say so - trail selection then falls back to **novelty ranking +
  embedding similarity**, which still produces strong cross-book trails. The graph's contribution
  grows with library size and thematic overlap.

## What it is / isn't

- **Is:** a personal thinking instrument for reading across a themed set of books; a reference
  implementation of a syntopic-reading pipeline.
- **Isn't:** a summarizer, a replacement for reading, or useful on a single book - trails are
  cross-book by design (the quality gate wants a trail to span **several** books on a shared theme).

**"No trails generated"?** The quality gate (`src/agent/trail_constraints.py`) rejects any trail
that leans too hard on one book: it must span a minimum number of distinct books, and no single
author may supply more than ~45-50% of the excerpts. **That minimum scales to your library size** -
a large library (7+ books) must span at least 4 books; a small demo-sized library (4-6) needs 3.
A themed set of at least 5-6 books on a shared question gives the tool enough range to clear the
bar reliably; a small or single-author library, or a narrow question only one or two of your books
actually address, will often get turned away by the diversity check rather than quietly produce a
thin trail. If a run is rejected, try a broader question or add a book that bridges your theme.

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
