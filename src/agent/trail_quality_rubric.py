"""Trail Quality Rubric (T1) — the judge backbone.

Adopts the curator's battle-tested 7-parameter rubric (from data/trails/PATTERN-OBSERVATIONS.md,
the critical analysis of 96 trails) rather than inventing a fresh one. Each parameter is
scored 1-5; the composite is /35 so scores are directly comparable to the 83 existing
ratings in TRAIL-RATINGS.md (the validation ground truth).

This module is the *rubric + prompt spec only*. It does NOT call an LLM (that is the judge
engine, T4) and does NOT compute structural facts (that is T3, reusing trail_constraints).
Keeping the rubric isolated makes it reviewable and tunable in calibration (T8) without
touching the engine.

Design provenance: every anchor below is grounded in a causal pattern documented in
PATTERN-OBSERVATIONS.md ("What High-Scoring Trails Share" / "Top 5 failure modes" /
"Structural Patterns"). See ADR-001.
"""

from typing import Dict, List

# ---------------------------------------------------------------------------
# The 7 parameters (the curator's rubric, with anchored 1-5 descriptions)
# ---------------------------------------------------------------------------
# Each anchor encodes a causal mechanism from the 96-trail analysis so the judge
# scores the MECHANISM, not a vague impression.

DIMENSIONS: List[Dict] = [
    {
        "key": "conceptual_through_line",
        "name": "Conceptual through-line",
        "assesses": "Is a single inevitable idea illuminated? Is the title earned by the arc?",
        "anti_pattern": "Loosely-themed greatest-hits; a thesis announced upfront the excerpts only partly substantiate.",
        "anchors": {
            5: "One inevitable idea. The title names a metaphor the excerpts cumulatively EARN — the reader arrives at it feeling its inevitability.",
            4: "Clear through-line, title largely earned, one or two excerpts ride along rather than advance it.",
            3: "Competent, coherent theme but the title is announced rather than earned; unremarkable.",
            2: "Theme is loose; several excerpts feel collected rather than sequenced; title over-promises.",
            1: "Loosely-themed greatest-hits. No real through-line; the title promises more than the trail delivers.",
        },
    },
    {
        "key": "dialectic_vs_stacking",
        "name": "Dialectic vs. stacking",
        "assesses": "Do sources in different registers clash or converge productively, or do they all say the same thing? "
                    "CRITICAL: distinguish a source that dominates because it is the INEVITABLE anchor text for the "
                    "question (a virtue) from one that dominates because retrieval STACKED it (the failure). A 60% "
                    "inevitable anchor whose excerpts are the strongest available can still be a 4-5; a 45% "
                    "retrieval-stacked default is a 1-2.",
        "anti_pattern": "Redundancy disguised as diversity; retrieval-stacked single-author/single-book dominance; "
                        "same-register echo chamber. (Author-level dominance — one author across several of their own "
                        "books — is the strongest failure predictor; see the structural facts for top_author_share.)",
        "anchors": {
            5: "Sources occupy genuinely different epistemological registers (philosophy/biography/fiction/theory) AND "
               "include at least one deliberately register-incongruent source that forces confrontation rather than "
               "reinforcement; dialectic emerges by structural necessity. An inevitable load-bearing anchor (≤~60%) is fine.",
            4: "Real interplay across registers; if one source leads it is the inevitable anchor for the question "
               "(its excerpts are the strongest available), not a retrieval default; minor echoing.",
            3: "Some genuine interplay but partial mutual reinforcement; register variety is thin.",
            2: "Mostly stacking — sources echo within a narrow register; dominance reads as a retrieval default "
               "rather than an inevitable anchor.",
            1: "Single-author/single-register monoculture where one source dominates BECAUSE retrieval stacked it "
               "(not because it is inevitable) — redundancy disguised as diversity.",
        },
    },
    {
        "key": "excerpt_strength",
        "name": "Excerpt strength",
        "assesses": "Are the passages the strongest expressions of the idea? (Within-trail verbatim repetition is "
                    "scored separately as a deterministic flag, not here — judge the passages on their own merit.)",
        "anti_pattern": "Topic-keyword match without concept match; decorative or filler passages.",
        "anchors": {
            5: "Passages are unambiguously among the strongest expressions of the theme; a reader who knows the texts recognizes the choices as inspired.",
            4: "Strong, well-chosen passages; one or two are merely relevant rather than the best.",
            3: "Relevant but not the strongest available; competent keyword-and-concept match.",
            2: "Several passages are topical but not conceptually central; some feel decorative.",
            1: "Topic-keyword match without concept match; weak or padded passages.",
        },
    },
    {
        "key": "synthesis_depth",
        "name": "Synthesis depth in notes",
        "assesses": "Do the connection notes synthesize (assert agreement/disagreement/build), or merely describe?",
        "anti_pattern": "Travelogue transitions: 'X also discusses this', 'Y confirms Z'. (Note LENGTH is unreliable; "
                        "COMMITMENT is reliable.) Beware confident-but-false: an assertive note over an adjacent-not-"
                        "dialectical pairing is NOT a 5 — the relationship must be real.",
        "anchors": {
            5: "Notes assert a specific relationship between sources ('Hamilton's libel defense as the bedrock on which the muckraking tradition stands') AND the quoted excerpts substantiate that relationship, not merely state it.",
            4: "Most notes commit to a relationship the excerpts support; a few describe.",
            3: "Mix of genuine synthesis and description.",
            2: "Mostly descriptive notes; the reader is left to do the synthesizing work.",
            1: "Travelogue transitions throughout; notes describe what each excerpt covers, never the relationship.",
        },
    },
    {
        "key": "non_obviousness",
        "name": "Non-obviousness",
        "assesses": "Is the connection unreachable via a quick search or a single-book reading?",
        "anti_pattern": "Restating what any well-read person already knows; reachable via any reading list.",
        "anchors": {
            5: "Genuinely non-obvious synthesis; the connection is unreachable via Google or single-book reading.",
            4: "Fresh framing that most well-read readers would not have assembled.",
            3: "Somewhat fresh but reachable by a well-read person who thought about it.",
            2: "Largely conventional; the synthesis restates familiar territory.",
            1: "Obvious. Restates what any reader of an HBR list / standard canon already knows.",
        },
    },
    {
        "key": "generativity",
        "name": "Generativity",
        "assesses": "Does the trail create new questions, frames, or applications? Score this ORTHOGONALLY to "
                    "non-obviousness: non-obviousness = was the connection hard to reach; generativity = does it open "
                    "new work. A trail can be non-obvious yet closed, or obvious yet generative — do not mirror the two.",
        "anti_pattern": "Conclusion-flavored greatest-hits that close rather than open.",
        "anchors": {
            5: "Opens new questions, frames, or applications; leaves the reader with productive new tools.",
            4: "Generative; suggests at least one fresh line of thought.",
            3: "Mildly generative; mostly consolidates.",
            2: "Largely closes the topic; little new is opened.",
            1: "Conclusion-flavored; a greatest-hits that terminates thought rather than generating it.",
        },
    },
    {
        "key": "anti_pattern_absence",
        "name": "Anti-pattern absence",
        "assesses": "Residual whole-trail integrity NOT already captured by dims 1-6: epistemic false equivalence "
                    "(self-help leveled with canonical rigor), buzzword soup, and the reverse-engineered 'feel' "
                    "(description more interesting than the trail delivers). Do NOT re-penalize a flaw already scored "
                    "in dims 1-6 — that double-counts. (Description-content mismatch is a separate deterministic flag.)",
        "anti_pattern": "Self-help equated with rigorous sources; buzzword soup; trail built backward from a thesis.",
        "anchors": {
            5: "No false equivalence, no buzzword soup, no reverse-engineered feel.",
            4: "Clean except one minor drag (e.g., a single self-help inclusion that does not break the register).",
            3: "One real residual anti-pattern present but not load-bearing.",
            2: "A structural false-equivalence (self-help treated as equal to canonical rigor) or clear buzzword soup.",
            1: "Multiple residual anti-patterns stack: false equivalence + buzzword soup + reverse-engineered feel.",
        },
    },
]

DIMENSION_KEYS = [d["key"] for d in DIMENSIONS]

# ---------------------------------------------------------------------------
# Composite scale + buckets (the curator's thresholds — must match TRAIL-RATINGS.md)
# ---------------------------------------------------------------------------
COMPOSITE_MIN = len(DIMENSIONS)        # 7
COMPOSITE_MAX = len(DIMENSIONS) * 5    # 35

BUCKETS = [
    ("Exemplar", 30, 35),
    ("Solid", 22, 29),
    ("Mediocre", 15, 21),
    ("Failure", 7, 14),
]


def bucket_for(composite: int) -> str:
    """Map a /35 composite to the curator's bucket label."""
    for name, lo, hi in BUCKETS:
        if lo <= composite <= hi:
            return name
    return "Failure" if composite < 15 else "Exemplar"


# ---------------------------------------------------------------------------
# 4-dimension dashboard headline (the rollup the curator picked) — derived from the 7
# ---------------------------------------------------------------------------
# These are presentation rollups, NOT separately-scored axes. Gap analysis is the
# per-trail gap_note, not a 1-5 score. Excerpt strength + synthesis depth still
# count toward the /35 composite even though they have no headline of their own.
HEADLINE_ROLLUP: Dict[str, List[str]] = {
    "narrative_coherence": ["conceptual_through_line"],
    "cross_author_interplay": ["dialectic_vs_stacking"],
    "novelty_honesty": ["non_obviousness", "generativity"],
    # gap_analysis -> gap_note (free text), informed by anti_pattern_absence
}


def headline_from_scores(scores: Dict[str, int]) -> Dict[str, float]:
    """Roll the 7 parameter scores up into the 3 numeric headline dimensions."""
    out: Dict[str, float] = {}
    for headline, keys in HEADLINE_ROLLUP.items():
        vals = [scores[k] for k in keys if k in scores]
        out[headline] = round(sum(vals) / len(vals), 1) if vals else 0.0
    return out


# ---------------------------------------------------------------------------
# Output schema (validated by the engine, T4)
# ---------------------------------------------------------------------------
OUTPUT_SCHEMA = {
    "scores": {k: "int 1-5" for k in DIMENSION_KEYS},
    "rationale": "one line, the style of TRAIL-RATINGS.md entries",
    "gap_note": "1-2 sentences: which register/voice is missing, where it is one-sided, "
                "or where a conceptual jump occurs between excerpts",
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
_ANALYST_POSTURE = (
    "You are a critical literary-intellectual analyst scoring a syntopic reading 'trail' "
    "(an ordered sequence of book excerpts connected by an idea). Posture: assume mediocrity "
    "until the trail earns better, and a single-author/single-register stack is Mediocre however "
    "polished. BUT do not under-score genuine excellence out of conservatism: a genuinely "
    "exemplary trail SHOULD earn 5s on most dimensions (composite 30+), a competent-strong "
    "syntopic trail belongs in Solid (22-29), and Mediocre (15-21) is for stacking, thin register, "
    "or reverse-engineering. Calibrate against the reference trails below before scoring. Always "
    "justify a 5 by pointing at specific excerpts."
)

# Calibration anchors — illustrative trail archetypes from the rated corpus, spanning the range,
# used as few-shot reference points so the judge lands consistently on the curator's /35 scale
# (fixes the documented ~-4 deflation). Genericized from the real rated corpus; DISJOINT from any
# validation set so agreement testing stays honest.
_CALIBRATION_ANCHORS = """Reference points from the rated corpus (calibrate to these):
  - EXEMPLAR (33/35): a trail spanning 9 books across genuinely different registers (a neuroscience
    text, an existential-psychology text, a contemplative text, and several cognitive-science texts);
    notes do genuine interpretive work; clean of recycled magnets. This is what 5s on dialectic and
    synthesis look like.
  - SOLID (28/35): a trail where one foundational philosophy text is the INEVITABLE anchor at
    4/10 excerpts (justified, not a failure), building a coherent philosophy-to-decision-theory arc
    with several complementary sources. An inevitable anchor at ~40% is fine; this is competent-strong,
    not Exemplar.
  - MEDIOCRE (19/35): a trail that is an 8/10 single-author stack with a token counterweight, where
    the description promises additional sources that never appear in the excerpts. Author
    dominance + reverse-engineering = Mediocre however fluent the prose."""


def _format_dimensions() -> str:
    blocks = []
    for i, d in enumerate(DIMENSIONS, 1):
        anchors = "\n".join(f"      {s}: {d['anchors'][s]}" for s in (5, 4, 3, 2, 1))
        blocks.append(
            f"  {i}. {d['name']} (key: {d['key']})\n"
            f"     Assesses: {d['assesses']}\n"
            f"     Anti-pattern: {d['anti_pattern']}\n"
            f"     Anchors (1-5):\n{anchors}"
        )
    return "\n\n".join(blocks)


def _format_facts(facts: Dict) -> str:
    """Render deterministic structural facts (T3) the judge weighs but does not re-derive.

    Raw numbers only — NO bucket-correlation glosses (those would leak the answer and halo
    every dimension; see ADR-001 / red-team D2). Interpretation is the judge's job, guided
    by the anchors.
    """
    if not facts:
        return "  (no precomputed structural facts provided)"
    lines = []
    ec = facts.get("excerpt_count")
    bc = facts.get("book_count")
    if bc is not None:
        lines.append(f"  - Distinct books: {bc}")
    if ec is not None:
        lines.append(f"  - Excerpts: {ec}")
    top = facts.get("top_book_share")
    if top is not None:
        lines.append(f"  - Heaviest single BOOK: {facts.get('top_book', '?')} = {top:.0%} of excerpts")
    # Author-level dominance — the documented #1 failure predictor (one author across several
    # of their own books). Carried explicitly because top_book_share alone misses it.
    tas = facts.get("top_author_share")
    if tas is not None:
        lines.append(f"  - Heaviest single AUTHOR: {facts.get('top_author', '?')} = {tas:.0%} of excerpts")
    flags = facts.get("flags") or []
    if flags:
        lines.append("  - Structural flags (deterministic gate):")
        lines.extend(f"      * {f}" for f in flags)
    else:
        lines.append("  - Structural flags: none")
    return "\n".join(lines)


def build_judge_prompt(trail_text: str, facts: Dict | None = None) -> str:
    """Assemble the full judge prompt for one trail.

    trail_text : the raw trail markdown.
    facts      : deterministic structural facts from T3 (book_count, top_book_share,
                 top_book, excerpt_count, flags). Fed in so the LLM weighs but does
                 not re-derive them.
    """
    return f"""{_ANALYST_POSTURE}

{_CALIBRATION_ANCHORS}

Score the trail on these SEVEN parameters, each 1-5, using the anchors literally:

{_format_dimensions()}

Composite = sum of the seven (range {COMPOSITE_MIN}-{COMPOSITE_MAX}). Buckets:
  Exemplar 30-35 | Solid 22-29 | Mediocre 15-21 | Failure 7-14.

Precomputed structural facts (weigh these; do NOT recompute them):
{_format_facts(facts or {})}

Also produce:
  - rationale: ONE line in the terse style of an expert rating note (name specific books/excerpts
    and the specific dialectical work they do or fail to do).
  - gap_note: 1-2 sentences naming which register/voice is MISSING (e.g., 'esoteric sources with no
    skeptic', 'business books with no historical reach'), where the trail is one-sided, or where a
    conceptual jump occurs between excerpts.
  - accessibility: 1-5, SEPARATE from the seven (NOT in the composite). "Can a smart reader who
    has NOT read these books enter each excerpt cold?" 5 = every excerpt self-contained or
    oriented (clear referents, complete sentences, Setting notes where needed); 3 = a reader
    loses the thread in places (unexplained names/terms, mid-argument entries); 1 = most
    excerpts presuppose the source book. Judge the reader's on-ramp, not intellectual depth -
    a demanding trail can still be a 5 if it orients the reader at each step.

Return STRICT JSON only, no prose outside it:
{{
  "scores": {{ {", ".join(f'"{k}": <1-5>' for k in DIMENSION_KEYS)} }},
  "accessibility": <1-5>,
  "rationale": "<one line>",
  "gap_note": "<1-2 sentences>"
}}

--- TRAIL ---
{trail_text}
--- END TRAIL ---
"""
