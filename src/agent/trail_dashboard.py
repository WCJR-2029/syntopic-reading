"""Library quality dashboard (T6).

Renders the score store (T5) into QUALITY-DASHBOARD.md — a sorted leaderboard of every scored
trail plus summary stats. This is the visibility surface: the first time library quality is
visible at a glance. Generated FROM the JSON store, never hand-edited.
"""

import statistics
from pathlib import Path
from typing import Dict, List

from src.agent.trail_quality_rubric import BUCKETS

DEFAULT_DASHBOARD = "data/trails/QUALITY-DASHBOARD.md"
_BUCKET_ORDER = [b[0] for b in BUCKETS]  # Exemplar, Solid, Mediocre, Failure


def _flag_tags(entry: Dict) -> str:
    """Compact flag summary for the leaderboard's Flags column."""
    tags: List[str] = []
    if entry.get("hard_capped"):
        tags.append("⚑capped")
    facts = entry.get("facts", {})
    tas = facts.get("top_author_share")
    if tas and tas > 0.45:
        tags.append(f"{facts.get('top_author','?')} {tas:.0%}")
    for f in entry.get("flags", []):
        if f.startswith("HARD description_mismatch"):
            tags.append("desc-mismatch")
        elif f.startswith("low_book_diversity"):
            tags.append("thin")
        elif f.startswith("HARD verbatim"):
            tags.append("verbatim-repeat")
    # de-dup, keep order
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); out.append(t)
    return " · ".join(out) if out else "—"


def _short_title(title: str, n: int = 48) -> str:
    return title if len(title) <= n else title[: n - 1] + "…"


def render_dashboard(store: Dict[str, Dict]) -> str:
    entries = list(store.values())
    if not entries:
        return "# Syntopic Library — Trail Quality\n\n_No trails scored yet._\n"

    entries.sort(key=lambda e: e.get("composite", 0), reverse=True)
    composites = [e.get("composite", 0) for e in entries]
    n = len(entries)
    avg = sum(composites) / n
    median = statistics.median(composites)
    by_bucket = {b: 0 for b in _BUCKET_ORDER}
    for e in entries:
        by_bucket[e.get("bucket", "Failure")] = by_bucket.get(e.get("bucket", "Failure"), 0) + 1
    below_gate = sum(1 for c in composites if c < 22)  # Mediocre + Failure
    capped = sum(1 for e in entries if e.get("hard_capped"))

    lines: List[str] = []
    lines.append("# Syntopic Library — Trail Quality")
    lines.append("")
    lines.append(f"**{n} trails scored**  ·  avg **{avg:.1f}** / 35  ·  median **{median:.0f}**  ·  "
                 f"**{below_gate}** below gate (<22)" + (f"  ·  {capped} hard-capped" if capped else ""))
    lines.append("")
    dist = "  ".join(f"{b} {by_bucket[b]}" for b in _BUCKET_ORDER)
    lines.append(f"Distribution:  {dist}")
    lines.append("")
    lines.append("_Score = sum of 7 rubric parameters (/35). Buckets: Exemplar 30-35 · Solid 22-29 · "
                 "Mediocre 15-21 · Failure <15. Generated from quality-scores.json — do not hand-edit._")
    lines.append("")
    lines.append("| # | Trail | Score | Bucket | Books | Flags |")
    lines.append("|---:|---|---:|---|---:|---|")
    for i, e in enumerate(entries, 1):
        facts = e.get("facts", {})
        lines.append(
            f"| {i} | {_short_title(e.get('title',''))} | "
            f"{e.get('composite',0)} | {e.get('bucket','?')} | "
            f"{facts.get('book_count','?')} | {_flag_tags(e)} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_dashboard(store: Dict[str, Dict], path: str | Path = DEFAULT_DASHBOARD) -> str:
    md = render_dashboard(store)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return str(p)
