"""Trail quality judge engine (T4).

Combines: parse (T2) -> deterministic facts/flags (T3) -> one LLM call against the 7-param
rubric (T1) -> a validated TrailScore. Applies the hard-cap rule (a HARD structural flag —
within-trail verbatim repetition or description-content mismatch — caps the composite below 16
regardless of the LLM's read; see ADR-001 / red-team A5/A6).

Score persistence (T5), dashboard (T6) and CLI (T7) build on score_trail() here.
"""

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, TRAILS_DIR
from src.agent.trail_parser import parse_trail
from src.agent.trail_facts import compute_facts, has_hard_flag
from src.agent import trail_quality_rubric as rubric

_HARD_CAP_COMPOSITE = 15  # "< 16" per the hard-cap rule


@dataclass
class TrailScore:
    trail_file: str
    title: str
    scores: Dict[str, int]
    composite: int
    bucket: str
    headline: Dict[str, float]
    rationale: str
    gap_note: str
    accessibility: int = 0  # 1-5 cold-entry on-ramp (Phase 8.1 Fix 3) - NOT in the composite
    flags: List[str] = field(default_factory=list)
    facts: Dict = field(default_factory=dict)
    hard_capped: bool = False
    model: str = ""
    ts: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


class JudgeError(RuntimeError):
    pass


def _extract_json(text: str) -> Dict:
    """Pull a JSON object out of an LLM response (handles ```json fences, like research.py)."""
    t = text.strip()
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in t:
        t = t.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(t)


def _validate_scores(data: Dict) -> Dict[str, int]:
    """Ensure exactly the 7 rubric dimensions are present as ints in 1-5."""
    raw = data.get("scores", {})
    if not isinstance(raw, dict):
        raise JudgeError("'scores' missing or not an object")
    out: Dict[str, int] = {}
    for k in rubric.DIMENSION_KEYS:
        if k not in raw:
            raise JudgeError(f"missing dimension '{k}'")
        v = raw[k]
        if not isinstance(v, int) or not (1 <= v <= 5):
            raise JudgeError(f"dimension '{k}'={v!r} not an int in 1-5")
        out[k] = v
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def score_trail(
    path: str | Path,
    client=None,
    model: str = CLAUDE_MODEL,
    trails_dir: Optional[Path] = None,
    max_retries: int = 1,
) -> TrailScore:
    """Score one trail end-to-end. `client` is an anthropic.Anthropic (created if None)."""
    path = Path(path)
    if trails_dir is None:
        trails_dir = Path(TRAILS_DIR)
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    raw_text = path.read_text(encoding="utf-8")
    doc = parse_trail(path)
    facts = compute_facts(doc, trails_dir=trails_dir)
    prompt = rubric.build_judge_prompt(raw_text, facts)

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            data = _extract_json(msg.content[0].text)
            scores = _validate_scores(data)
            accessibility = data.get("accessibility", 0)
            if not isinstance(accessibility, int) or not (0 <= accessibility <= 5):
                accessibility = 0  # optional field - never fail the run over it
            rationale = str(data.get("rationale", "")).strip()
            gap_note = str(data.get("gap_note", "")).strip()
            break
        except (json.JSONDecodeError, JudgeError, KeyError) as e:
            last_err = e
            if attempt < max_retries:
                continue
            raise JudgeError(f"{path.name}: judge failed after {max_retries + 1} tries: {e}") from e

    # Structural flags (description-mismatch, verbatim-repeat) are INFORMATIONAL — surfaced in the
    # dashboard and fed to the LLM to weigh — but NOT an absolute cap. The full-83 run showed a hard
    # cap to <16 wrongly crushed many high-quality trails (e.g. Dopamine Paradox, your 33, → 15),
    # because the description-mismatch detector is false-positive-prone (it is opt-in in
    # trail_constraints for exactly that reason). See ADR-003.
    composite = sum(scores.values())
    bucket = rubric.bucket_for(composite)
    hard_capped = False  # retained for schema stability; capping is disabled (ADR-003)

    return TrailScore(
        trail_file=path.name,
        title=doc.title,
        scores=scores,
        composite=composite,
        bucket=bucket,
        accessibility=accessibility,
        headline=rubric.headline_from_scores(scores),
        rationale=rationale,
        gap_note=gap_note,
        flags=facts.get("flags", []),
        facts={k: facts[k] for k in ("book_count", "excerpt_count", "top_book",
                                     "top_book_share", "top_author", "top_author_share")},
        hard_capped=hard_capped,
        model=model,
        ts=_now(),
    )
