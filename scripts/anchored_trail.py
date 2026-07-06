#!/usr/bin/env python3
"""
anchored_trail.py — Anchored syntopic trail retrieval (thin CLI wrapper).

WHY THIS EXISTS
---------------
The stock `trail_generator.py --idea` does NOT let you steer a trail toward a
specific book or a specific cross-book bridge: its ideation stage re-derives its
own high-novelty search topics from the seed, so the anchor book you care about
never gets retrieved. (Discovered 2026-07-01 while trying to build
Case-Against-Reality bridges — all 5 trails excluded the new book entirely.)

As of Phase 7 (ADR-005) the retrieval procedure lives in ONE shared module,
`src/agent/anchored_retrieval.py`, imported by both `trail_generator.py`
(the `--anchor-book` generator flags) and this script. This file is now a thin
wrapper: it calls the shared core and emits the JSON pack + optional draft
markdown scaffold. There is no retrieval logic here to drift out of sync (which
is what caused the old hardcoded-model-name bug).

Use this script when you want the raw retrieval pack for hand-editorial work
(you write the `Connection:` notes yourself). Use `trail_generator.py
--anchor-book ...` for the fully-automated, gate-checked, saved trail.

USAGE
-----
  python scripts/anchored_trail.py \
      --title "The Case Against Matter" \
      --seed-topics "materialism; consciousness and physical reality; nature of reality" \
      --anchor-book "The Case Against Reality" \
      [--anchor-chunks 91214,91106] [--k 6] [--per-book 1] \
      [--exclude-books "Pinker;Rationality"] \
      --json /tmp/pack.json [--draft-md data/trails/draft.md]
"""
import argparse
import json
import sys

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.agent import anchored_retrieval as ar


def _pack_for_json(core_pack: dict, title: str, desc: str) -> dict:
    """Project the rich core pack down to the stable JSON-pack shape.

    Keeps the historical keys so downstream consumers / saved packs don't break:
      anchors    -> [{book, chunk_id, text}]
      companions -> [{book, chunk_id, matched_topic, similarity, text}]
    """
    return {
        "title": title,
        "desc": desc,
        "seed_topics": core_pack["seed_topics"],
        "anchor_book": core_pack["anchor_book"],
        "anchors": [
            {"book": a["book_title"], "chunk_id": a["chunk_id"], "text": a["text"]}
            for a in core_pack["anchors"]
        ],
        "companions": [
            {"book": c["book_title"], "chunk_id": c["chunk_id"],
             "matched_topic": c["matched_topic"], "similarity": c["similarity"],
             "text": c["text"]}
            for c in core_pack["companions"]
        ],
    }


def main():
    ap = argparse.ArgumentParser(description="Anchored syntopic trail retrieval (thin wrapper)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--seed-topics", default=None,
                    help="semicolon-separated EXISTING topic labels; builds the centroid. "
                         "Omit to auto-derive from the anchor book.")
    ap.add_argument("--anchor-book", default=None, help="title substring; required present in trail")
    ap.add_argument("--anchor-chunks", default=None, help="csv chunk ids to force as anchors")
    ap.add_argument("--anchor-n", type=int, default=2, help="auto-picked anchor chunks if not given")
    ap.add_argument("--k", type=int, default=6, help="companion excerpts")
    ap.add_argument("--per-book", type=int, default=1)
    ap.add_argument("--min-words", type=int, default=120)
    ap.add_argument("--front-matter-skip", type=int, default=12,
                    help="skip anchor-book chunks with chunk_index below this (praise/TOC pages)")
    ap.add_argument("--exclude-books", default="", help="semicolon substrings to drop from companions")
    ap.add_argument("--json", default=None)
    ap.add_argument("--draft-md", default=None)
    args = ap.parse_args()

    seeds = ([s.strip() for s in args.seed_topics.split(";") if s.strip()]
             if args.seed_topics else None)
    anchor_chunks = ([int(x) for x in args.anchor_chunks.split(",")]
                     if args.anchor_chunks else None)
    exclude_books = [s.strip() for s in args.exclude_books.split(";") if s.strip()]

    conn = ar.connect()
    try:
        core_pack = ar.retrieve_anchored(
            conn.cursor(),
            anchor_book=args.anchor_book,
            anchor_chunks=anchor_chunks,
            seed_topics=seeds,
            k=args.k,
            per_book=args.per_book,
            min_words=args.min_words,
            anchor_n=args.anchor_n,
            front_matter_skip=args.front_matter_skip,
            exclude_books=exclude_books,
        )
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    finally:
        conn.close()

    if core_pack["missing_seeds"]:
        print(f"  ! seed topics not found (skipped): {core_pack['missing_seeds']}",
              file=sys.stderr)

    pack = _pack_for_json(core_pack, args.title, args.desc)

    # --- console arc
    print(f"\n=== {args.title} ===")
    for a in pack["anchors"]:
        print(f"  ANCHOR  {a['book'][:44]:44} (chunk {a['chunk_id']})")
    for comp in pack["companions"]:
        print(f"  {comp['similarity']:.3f}  {comp['book'][:44]:44} "
              f"(chunk {comp['chunk_id']})  <- {comp['matched_topic']}")

    if args.json:
        json.dump(pack, open(args.json, "w"), indent=2)
        print(f"\nJSON -> {args.json}")
    if args.draft_md:
        lines = [f"# {args.title}\n", f"**Description**: {args.desc}\n", "---\n\n## Trail Excerpts\n"]
        n = 1
        for a in pack["anchors"]:
            lines.append(f"### Excerpt {n}: {a['book']}\n\n**Connection**: _[ANCHOR — author note]_\n\n> {a['text']}\n")
            n += 1
        for comp in pack["companions"]:
            lines.append(f"### Excerpt {n}: {comp['book']}\n\n**Connection**: _[note — matched on "
                         f"'{comp['matched_topic']}', sim {comp['similarity']}]_\n\n> {comp['text']}\n")
            n += 1
        open(args.draft_md, "w").write("\n".join(lines))
        print(f"DRAFT MD -> {args.draft_md}")


if __name__ == "__main__":
    main()
