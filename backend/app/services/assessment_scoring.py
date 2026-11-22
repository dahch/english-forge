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
BAND_INDEX = {band: index for index, band in enumerate(CEFR_ORDER)}

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


def shift_band(band: str, steps: int) -> str:
    """Move a CEFR band down by steps, clamped at A1."""
    index = max(0, min(len(CEFR_ORDER) - 1, BAND_INDEX.get(band, 0) - steps))
    return CEFR_ORDER[index]


def listening_band(evidence: list[tuple[str, bool]]) -> str | None:
    """CEFR band from graded listening items, weighted by item difficulty.

    Raw accuracy mapped straight onto 0-100 inflated the result: answering only
    the A2 item correctly scored 100 (C2). The band now comes from the hardest
    item answered correctly, demoted when overall accuracy is poor.
    """
    if not evidence:
        return None
    correct_bands = [band for band, correct in evidence if correct]
    if not correct_bands:
        return "A1"
    accuracy = len(correct_bands) / len(evidence)
    base = max(correct_bands, key=lambda band: BAND_INDEX.get(band, 0))
    if accuracy < 0.34:
        return shift_band(base, 2)
    if accuracy < 0.6:
        return shift_band(base, 1)
    return base


def pronunciation_band(evidence: list[tuple[str, float | None]]) -> str | None:
    """CEFR band from read-aloud items, weighted by sentence difficulty.

    Reading a B1 sentence with 90% accuracy is a B1 performance, not C2. The
    band is the item's band, demoted by how far the composite falls short; a
    consistently excellent reader is credited with the hardest item read.
    """
    scored = [(band, score) for band, score in evidence if score is not None]
    if not scored:
        return None
    average = statistics.fmean(score for _, score in scored)
    hardest = max((band for band, _ in scored), key=lambda band: BAND_INDEX.get(band, 0))
    if average >= 85:
        return hardest
    achieved: list[str] = []
    for band, score in scored:
        if score >= 85:
            achieved.append(band)
        elif score >= 70:
            achieved.append(shift_band(band, 1))
        elif score >= 50:
            achieved.append(shift_band(band, 2))
        else:
            achieved.append(shift_band(band, 3))
    indexes = sorted(BAND_INDEX[band] for band in achieved)
    return CEFR_ORDER[indexes[(len(indexes) - 1) // 2]]


def final_level_from_bands(bands: dict[str, str | None]) -> str | None:
    """Conservative median of the measured bands (same rule as final_level)."""
    valid = [band for band in bands.values() if band in BAND_INDEX]
    if not valid:
        return None
    indexes = sorted(BAND_INDEX[band] for band in valid)
    return CEFR_ORDER[indexes[(len(indexes) - 1) // 2]]


def strengths_weaknesses(dimension_scores: dict[str, float | None]) -> tuple[list[str], list[str]]:
    """Deterministic labels from measured dims only. Dimensions with no
    evidence are never claimed as strengths or weaknesses."""
    measured = {k: v for k, v in dimension_scores.items() if v is not None}
    strengths = [k for k, v in sorted(measured.items(), key=lambda kv: -kv[1]) if v >= 70]
    weaknesses = [k for k, v in sorted(measured.items(), key=lambda kv: kv[1]) if v <= 50]
    return strengths, weaknesses


def mean_or_none(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None
