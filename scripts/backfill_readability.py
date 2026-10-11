"""One-time readability backfill over existing trails (Phase 8.1, Fixes 1+2).

For every trail .md in data/trails/:
  1. Recover each excerpt's FULL chunk text from the DB (anchored trails were hard-cut at
     850 chars mid-word; --idea trails carry whole chunks with ragged edges), repair it
     (sentence edges + junk lines, src/readers/excerpt_repair), and re-apply the existing
     **bold** highlights.
  2. One Sonnet call per trail writes a "Setting" line per excerpt - orientation for a
     reader who has NOT read the book.
  3. Rewrite the trail file in place: Setting line inserted before Connection, quote text
     replaced with the repaired version.

Resumable: trails whose files already contain a **Setting**: line are skipped.
Run:  python scripts/backfill_readability.py [--limit N] [--dry-run]

NOTE: rewriting changes content hashes, so `trail_quality.py --all` will re-score
everything afterward - that re-run is the designed yardstick (ADR-001) and populates the
new `accessibility` field.
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

import sys as _sys, pathlib as _pl
_ROOT = str(_pl.Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)  # so `import src` works run-from-repo without `pip install -e .`

from src.config import DATABASE_PATH
from src.llm.client import get_client
from src.readers.excerpt_repair import repair_excerpt, clean_for_reading, flatten_prose
from src.agent.trail_parser import parse_trail

TRAILS = REPO / "data" / "trails"
SETTINGS_MODEL = "claude-sonnet-5-5" # orientation notes are not frontier work (Anthropic-only override;
                                      # ignored under LLM_PROVIDER=openai - see settings_for_trail below)
MAX_EXCERPT_CHARS = 1800             # cap after full-chunk recovery
_HDR = re.compile(r"^###\s+Excerpt\s+(\d+):.*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def normalized(s: str) -> str:
    return " ".join(_BOLD.sub(r"\1", s).split())


class ChunkIndex:
    """All chunk texts loaded once, whitespace-normalized, for fast substring recovery.
    (Per-excerpt SQL scans with REPLACE() over the 600MB text column were minutes-slow.)"""

    def __init__(self, cur):
        self.rows = [(t, " ".join(t.split())) for (t,) in
                     cur.execute("SELECT text FROM chunks").fetchall()]

    def recover(self, excerpt_text: str) -> str | None:
        """Find the source chunk by a distinctive mid-excerpt substring.

        Tries several probe windows: a single window can straddle a spot where the
        excerpt's text diverges from the chunk (dropped word, elision, highlight seam)
        and miss a chunk that another window finds.
        """
        flat = normalized(excerpt_text)
        if len(flat) < 120:
            return None
        for start in (80, 220, 380, 20):
            probe = flat[start:start + 60].strip()
            if len(probe) < 30:
                continue
            hits = [raw for raw, norm in self.rows if probe in norm]
            if len(hits) == 1:
                return hits[0]
        return None


def reapply_highlights(new_text: str, old_text: str) -> str:
    """Re-wrap the old **bold** phrases where they still occur in the repaired text."""
    for phrase in _BOLD.findall(old_text):
        clean = phrase.strip()
        if clean and clean in new_text and f"**{clean}**" not in new_text:
            new_text = new_text.replace(clean, f"**{clean}**", 1)
    return new_text


def settings_for_trail(client, doc, repaired: dict) -> dict:
    """One LLM call → {seq: setting}. repaired: seq -> excerpt text."""
    parts = [f"Trail: {doc.title}\nTrail description: {doc.description}\n"]
    for e in doc.excerpts:
        seq = e["sequence_order"]
        text = repaired.get(seq, e["text"])
        parts.append(f"[Excerpt {seq}] book: {e['book_title']} | author: {e['author']}\n"
                     f"{normalized(text)[:900]}\n")
    prompt = "\n".join(parts) + """
For EACH excerpt above, write a "setting" note (1-2 sentences) that orients a reader who has
NOT read that book: who the author is / what the book's project is, and what is happening in
this specific passage (who is speaking, what "they/it" refers to, what came just before).
Plain language, no jargon, no assuming familiarity. Do not restate the trail's argument -
that is the connection note's job. If the book title looks mangled, infer the real book.

Return STRICT JSON only: {"settings": {"<excerpt number>": "<setting>", ...}}"""
    # (Thinking-block-safe text extraction now lives centrally in LLMClient.complete().)
    t = client.complete("", prompt, max_tokens=4000).strip()
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in t:
        t = t.split("```", 1)[1].split("```", 1)[0]
    return {int(k): v.strip() for k, v in json.loads(t)["settings"].items()}


def rewrite_trail(path: Path, repaired: dict, settings: dict) -> None:
    """Insert Setting lines + replace quote text, preserving everything else."""
    text = path.read_text(encoding="utf-8")
    headers = list(_HDR.finditer(text))
    out, cursor = [], 0
    for i, h in enumerate(headers):
        seq = int(h.group(1))
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[h.start():block_end]
        conn = re.search(r"^\*\*Connection\*\*:.*$", block, re.MULTILINE)
        if conn and seq in settings and "**Setting**:" not in block:
            block = block[:conn.start()] + f"**Setting**: {settings[seq]}  \n" + block[conn.start():]
        if seq in repaired:
            # Quote region = the "> ..." lines; replace with one repaired blockquote.
            block = re.sub(r"(?ms)^> .*?(?=\n### |\n---\n|\Z)",
                           "> " + repaired[seq] + "\n\n", block, count=1)
        out.append(text[cursor:h.start()])
        out.append(block)
        cursor = block_end
    out.append(text[cursor:])
    path.write_text("".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--requote", action="store_true",
                    help="Re-repair quote text ONLY, for ALL trails (no LLM; existing "
                         "Setting lines kept). Used to sweep the 2026-07-04 junk-line "
                         "corruption with the fixed excerpt_repair.")
    args = ap.parse_args()

    # SETTINGS_MODEL is a cheaper Anthropic-specific override for this one-time
    # utility; under LLM_PROVIDER=openai fall back to the configured default
    # model instead of sending an Anthropic model name to an OpenAI-compatible
    # endpoint.
    _default_client = get_client()
    client = get_client(model=SETTINGS_MODEL) if _default_client.provider == "anthropic" else _default_client
    db = sqlite3.connect(DATABASE_PATH)
    print("loading chunk index...", flush=True)
    index = ChunkIndex(db.cursor())
    print(f"  {len(index.rows)} chunks in memory", flush=True)

    files = sorted(TRAILS.glob("2026*.md"))
    done = skipped = failed = 0
    for path in files:
        if args.limit and (done + failed) >= args.limit:
            break
        raw = path.read_text(encoding="utf-8")
        if "**Setting**:" in raw and not args.requote:
            skipped += 1
            continue
        try:
            doc = parse_trail(path)
            repaired = {}
            recovered = 0
            for e in doc.excerpts:
                seq, old = e["sequence_order"], e["text"]
                full = index.recover(old)
                base = full if full else _BOLD.sub(r"\1", old)
                if full:
                    recovered += 1
                fixed = repair_excerpt(base)
                if len(fixed) > MAX_EXCERPT_CHARS:
                    fixed = clean_for_reading(fixed, budget=MAX_EXCERPT_CHARS)
                else:
                    fixed = flatten_prose(fixed)
                repaired[seq] = reapply_highlights(fixed, old)
            settings = {} if args.requote else settings_for_trail(client, doc, repaired)
            if args.dry_run:
                print(f"DRY {path.name}: {recovered}/{len(doc.excerpts)} chunks recovered, "
                      f"{len(settings)} settings")
                for s, v in list(settings.items())[:2]:
                    print(f"   [{s}] {v[:110]}")
            else:
                rewrite_trail(path, repaired, settings)
                print(f"OK  {path.name}: {recovered}/{len(doc.excerpts)} recovered, "
                      f"{len(settings)} settings")
            done += 1
        except Exception as ex:
            failed += 1
            print(f"FAIL {path.name}: {ex}")
    print(f"\nbackfilled {done} · skipped(already) {skipped} · failed {failed}")


if __name__ == "__main__":
    main()
