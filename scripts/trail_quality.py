#!/usr/bin/env python3
"""Trail quality CLI (T7).

Score trails with the hybrid judge and (re)generate the library dashboard.

  python scripts/trail_quality.py --all                # score every active trail (incremental)
  python scripts/trail_quality.py --trail <file.md>    # score one
  python scripts/trail_quality.py --all --limit 8      # score first N (cost control / demo)
  python scripts/trail_quality.py --all --force        # re-score everything
  python scripts/trail_quality.py --dashboard-only     # just regenerate the dashboard from the store

Incremental by content hash (unchanged trails are skipped); the store is checkpointed every 10
trails so a long run is crash-safe. Mirrors doctor.py's UX.
"""

import argparse
from pathlib import Path

import anthropic
from src.config import ANTHROPIC_API_KEY, TRAILS_DIR
from src.agent.trail_quality import score_trail
from src.agent import trail_quality_store as store_mod
from src.agent.trail_dashboard import write_dashboard
from src.agent.trail_constraints import _CORPUS_SKIP_PREFIXES

# Non-trail support files in data/trails/ to never score.
SKIP_PREFIXES = _CORPUS_SKIP_PREFIXES + ("QUALITY", "CLEANUP")


def discover(trails_dir: Path):
    return [p for p in sorted(trails_dir.glob("*.md")) if not p.name.startswith(SKIP_PREFIXES)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Score trails + build the quality dashboard.")
    ap.add_argument("--all", action="store_true", help="score every active trail")
    ap.add_argument("--trail", help="score a single trail (filename in trails dir or a path)")
    ap.add_argument("--force", action="store_true", help="re-score even if unchanged")
    ap.add_argument("--limit", type=int, help="cap how many trails to score (cost control)")
    ap.add_argument("--store", default=store_mod.DEFAULT_STORE)
    ap.add_argument("--dashboard-only", action="store_true", help="regenerate dashboard, no scoring")
    args = ap.parse_args()

    trails_dir = Path(TRAILS_DIR)
    store = store_mod.load_store(args.store)

    if args.dashboard_only:
        path = write_dashboard(store)
        print(f"dashboard → {path}  ({len(store)} trails in store)")
        return 0

    if args.trail:
        t = Path(args.trail)
        targets = [t if t.exists() else trails_dir / args.trail]
    elif args.all:
        targets = discover(trails_dir)
    else:
        ap.error("specify --all, --trail <file>, or --dashboard-only")
        return 2

    if args.limit:
        targets = targets[: args.limit]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    scored = skipped = failed = 0
    for i, p in enumerate(targets, 1):
        chash = store_mod.content_hash(p)
        if not store_mod.needs_scoring(store, p.name, chash, args.force):
            skipped += 1
            continue
        try:
            s = score_trail(p, client=client, trails_dir=trails_dir)
            store_mod.put(store, s, chash)
            scored += 1
            cap = " ⚑capped" if s.hard_capped else ""
            print(f"[{i}/{len(targets)}] {s.composite:>2}/35 {s.bucket:<8} {s.title[:52]}{cap}")
            if scored % 10 == 0:
                store_mod.save_store(store, args.store)  # crash-safe checkpoint
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[{i}/{len(targets)}] FAIL {p.name}: {e}")

    store_mod.save_store(store, args.store)
    path = write_dashboard(store)
    print(f"\nscored {scored} · skipped {skipped} · failed {failed} · "
          f"store={args.store} ({len(store)} total) · dashboard={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
