"""Deterministic aggregation of assessment dimension scores.

The LLM no longer decides the CEFR level or the confidence — those are
computed here from measured evidence:

- Each dimension is scored 0-100 (listening/pronunciation: computed from
  graded items and pronunciation metrics; grammar/vocabulary/fluency: LLM
  rubric scoring of the chat transcript).
- Band thresholds (documented in the assessment docs): 0-20 A1, 21-40 A2,
  41-55 B1, 56-70 B2, 71-85 C1, 86-100 C2.
- Final level = conservative median band across the measured dimensions.
- Confidence = coverage (measured dims / 5) + evidence volume (total answered
  items vs a full assessment) - spread penalty (disagreement between dims).
"""

from __future__ import annotations

import statistics

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

# Upper bounds of each band on the 0-100 dimension scale.
_BAND_CUTOFFS = [(20, "A1"), (40, "A2"), (55, "B1"), (70, "B2"), (85, "C1")]


def band_from_score(score: float) -> str:
    for cutoff, band in _BAND_CUTOFFS:
        if score <= cutoff:
            return band
    return "C2"


def final_level(dimension_scores: dict[str, float | None]) -> str | None:
    """Conservative median band across measured dimensions. For an even count
    of dimensions the lower-middle band wins (rounding down = conservative)."""
    bands = [
        band_from_score(v)
        for v in dimension_scores.values()
        if v is not None
    ]
    if not bands:
        return None
    indexes = sorted(CEFR_ORDER.index(b) for b in bands)
    mid = (len(indexes) - 1) // 2  # lower median for even counts
    return CEFR_ORDER[indexes[mid]]


def compute_confidence(
    dimension_scores: dict[str, float | None],
    n_exchanges: int,
    n_listening: int,
    n_speaking: int,
) -> float:
    """Deterministic confidence in [0.05, 0.95].

    - coverage: fraction of the 5 dimensions actually measured.
    - evidence: total answered items vs a full assessment (~20 items).
    - spread: large disagreement between dimensions lowers confidence.
    """
    scores = [v for v in dimension_scores.values() if v is not None]
    coverage = len(scores) / 5.0
    evidence = min(1.0, (n_exchanges + n_listening + n_speaking) / 20.0)
    spread = ((max(scores) - min(scores)) / 100.0) if scores else 1.0
    confidence = 0.35 + 0.25 * coverage + 0.30 * evidence - 0.15 * spread
    return round(max(0.05, min(0.95, confidence)), 2)


def strengths_weaknesses(dimension_scores: dict[str, float | None]) -> tuple[list[str], list[str]]:
    """Deterministic labels from measured dims only. Dimensions with no
    evidence are never claimed as strengths or weaknesses."""
    measured = {k: v for k, v in dimension_scores.items() if v is not None}
    strengths = [k for k, v in sorted(measured.items(), key=lambda kv: -kv[1]) if v >= 70]
    weaknesses = [k for k, v in sorted(measured.items(), key=lambda kv: kv[1]) if v <= 50]
    return strengths, weaknesses


def mean_or_none(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None
