"""Tests for the deterministic assessment aggregation (level + confidence)."""

from app.services.assessment_scoring import (
    band_from_score,
    compute_confidence,
    final_level,
    strengths_weaknesses,
)
from app.services.assessment_bank import LISTENING_ITEMS, SPEAKING_ITEMS


class TestBandFromScore:
    def test_cutoffs(self):
        assert band_from_score(0) == "A1"
        assert band_from_score(20) == "A1"
        assert band_from_score(21) == "A2"
        assert band_from_score(40) == "A2"
        assert band_from_score(55) == "B1"
        assert band_from_score(56) == "B2"
        assert band_from_score(70) == "B2"
        assert band_from_score(71) == "C1"
        assert band_from_score(86) == "C2"
        assert band_from_score(100) == "C2"


class TestFinalLevel:
    def test_median_of_odd_count(self):
        scores = {"grammar": 60, "vocabulary": 55, "fluency": 45, "listening": 90, "pronunciation": 20}
        # bands: B2, B1, B1, C1, A1 → sorted [A1, B1, B1, B2, C1] → median B1
        assert final_level(scores) == "B1"

    def test_even_count_takes_lower_median(self):
        scores = {"grammar": 60, "vocabulary": 20, "fluency": 60, "listening": 20}
        # bands: B2, A1, B2, A1 → lower median A1 (conservative)
        assert final_level(scores) == "A1"

    def test_ignores_unmeasured_dimensions(self):
        scores = {"grammar": 60, "vocabulary": 55, "fluency": 45, "listening": None, "pronunciation": None}
        assert final_level(scores) == "B1"

    def test_none_when_nothing_measured(self):
        assert final_level({"grammar": None}) is None
        assert final_level({}) is None


class TestComputeConfidence:
    def test_full_evidence_beats_partial(self):
        full = compute_confidence(
            {"grammar": 60, "vocabulary": 60, "fluency": 60, "listening": 60, "pronunciation": 60},
            n_exchanges=10, n_listening=6, n_speaking=6,
        )
        thin = compute_confidence(
            {"grammar": 60},
            n_exchanges=3, n_listening=0, n_speaking=0,
        )
        assert full > thin

    def test_spread_penalized(self):
        coherent = compute_confidence(
            {"grammar": 60, "vocabulary": 60, "fluency": 60, "listening": 60, "pronunciation": 60},
            n_exchanges=8, n_listening=4, n_speaking=4,
        )
        spread = compute_confidence(
            {"grammar": 90, "vocabulary": 90, "fluency": 90, "listening": 10, "pronunciation": 10},
            n_exchanges=8, n_listening=4, n_speaking=4,
        )
        assert coherent > spread

    def test_clamped_to_valid_range(self):
        tiny = compute_confidence({"grammar": 10}, n_exchanges=1, n_listening=0, n_speaking=0)
        assert 0.05 <= tiny <= 0.95
        huge = compute_confidence(
            {"grammar": 50, "vocabulary": 50, "fluency": 50, "listening": 50, "pronunciation": 50},
            n_exchanges=20, n_listening=10, n_speaking=10,
        )
        assert huge <= 0.95


class TestStrengthsWeaknesses:
    def test_only_measured_dimensions(self):
        strengths, weaknesses = strengths_weaknesses(
            {"grammar": 80, "vocabulary": 40, "fluency": None, "listening": None, "pronunciation": None}
        )
        assert strengths == ["grammar"]
        assert weaknesses == ["vocabulary"]

    def test_unmeasured_never_claimed(self):
        strengths, weaknesses = strengths_weaknesses(
            {"grammar": None, "vocabulary": None, "fluency": None, "listening": None, "pronunciation": None}
        )
        assert strengths == []
        assert weaknesses == []

    def test_sorted_by_severity(self):
        strengths, weaknesses = strengths_weaknesses(
            {"grammar": 90, "vocabulary": 75, "fluency": 60, "listening": 20, "pronunciation": 50}
        )
        assert strengths == ["grammar", "vocabulary"]
        assert weaknesses == ["listening", "pronunciation"]


class TestItemBanks:
    def test_banks_have_enough_items(self):
        # Enough items for the sections to be meaningful and for the adaptive
        # early stop to still leave data.
        assert len(LISTENING_ITEMS) >= 4
        assert len(SPEAKING_ITEMS) >= 4

    def test_listening_items_have_expected_answers(self):
        for item in LISTENING_ITEMS:
            assert item.expected_answer.strip()
            assert item.tutor_text.strip()
            assert item.id

    def test_speaking_items_are_unique(self):
        texts = [i.text for i in SPEAKING_ITEMS]
        assert len(texts) == len(set(texts))
