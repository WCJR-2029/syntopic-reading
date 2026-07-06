"""Trail score persistence (T5).

A single JSON keyed by trail filename, each entry holding the TrailScore plus a content hash
of the source markdown. Incremental: a trail whose content hash is unchanged is skipped unless
forced. The dashboard (T6) is generated FROM this store. See ADR-002.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from src.agent.trail_quality import TrailScore

DEFAULT_STORE = "data/trails/quality-scores.json"


def content_hash(path: str | Path) -> str:
    """Stable short hash of a trail file's bytes (detects edits → triggers re-score)."""
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:12]


def load_store(path: str | Path = DEFAULT_STORE) -> Dict[str, Dict]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_store(store: Dict[str, Dict], path: str | Path = DEFAULT_STORE) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def needs_scoring(store: Dict[str, Dict], filename: str, chash: str, force: bool = False) -> bool:
    if force:
        return True
    entry = store.get(filename)
    return not entry or entry.get("content_hash") != chash


def put(store: Dict[str, Dict], score: TrailScore, chash: str) -> None:
    entry = score.to_dict()
    entry["content_hash"] = chash
    store[score.trail_file] = entry
