# Contributing

## What this project is

This is a personal project, built for the author's own use and shared publicly as a reference
implementation of a syntopic-reading pipeline. It is **provided as-is and essentially
unmaintained**. That means, realistically:

- Issues and pull requests may go unanswered, or answered slowly.
- There's no support offered for getting your specific library, environment, or setup working -
  `PREPARING-YOUR-LIBRARY.md` and `README.md` are the documentation, and they're the extent of it.
- Feature requests are welcome to open, but there's no roadmap and no commitment they'll be built.

If you fork this and build something of your own on top of it, that's the best-case outcome for a
reference implementation - go for it.

## Hard rules (non-negotiable)

Two categories of contribution will be closed on sight, no exceptions:

### 1. No copyrighted content

Never commit:
- Book files (EPUB or any other format) that aren't clearly public domain.
- Raw extracted text, long excerpts, or full chapters from in-copyright books.
- Reading trails, examples, or test fixtures built from in-copyright works.

Public-domain sources (Project Gutenberg, Standard Ebooks) are fine to reference or bundle for
demo/test purposes. Anything else - assume it's in copyright and keep it out of the repo entirely,
including in test data, issue attachments, and PR descriptions.

### 2. No DRM-circumvention tooling or instructions

Never submit code, scripts, documentation, or even discussion in an issue or PR that describes,
links to, or implements removing DRM from an e-book or any other file. This includes:
- Code that strips DRM from EPUB/MOBI/PDF files.
- Links to DRM-removal tools, plugins, or guides.
- "Here's how you'd do it" explanations, even framed hypothetically.

This project's stance is that you convert formats you already have the legal right to use
(`PREPARING-YOUR-LIBRARY.md` covers Calibre and Pandoc for exactly that) - it does not help with
circumventing rights restrictions, full stop.

Contributions that violate either rule will be closed without extended discussion. This isn't
personal - it's a bright line, kept bright on purpose.

## If you do want to contribute

Assuming your contribution doesn't run into either rule above:

- Keep pull requests small and focused on one change. Large, sprawling PRs are harder to review
  and more likely to sit unanswered given the maintenance posture above.
- Install dev dependencies and run the test suite before opening a PR:

  ```bash
  pip install -e ".[dev]"
  pytest
  ```

- Match the existing code style - look at neighboring files before introducing a new pattern or
  dependency.
- Describe the change and its motivation clearly in the PR description; a short "why" saves more
  review time than a long "what."

Thanks for taking the time to look at this project seriously enough to consider improving it.
