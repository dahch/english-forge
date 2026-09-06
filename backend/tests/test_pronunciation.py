"""Tests for deterministic pronunciation scoring (WER/PER/fluency/composite)."""

import pytest

from app.services.pronunciation import (
    composite_score,
    fluency_score,
    normalize_text,
    phoneme_accuracy,
    phonemizer_available,
    score_pronunciation,
    word_accuracy,
)


class TestNormalizeText:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize_text("Hello, World!") == ["hello", "world"]

    def test_keeps_intra_word_apostrophes(self):
        assert normalize_text("I'm done.") == ["i'm", "done"]

    def test_maps_numbers(self):
        assert normalize_text("I have 3 cats and 10 dogs") == [
            "i", "have", "three", "cats", "and", "ten", "dogs",
        ]

    def test_collapses_whitespace(self):
        assert normalize_text("  a   b\tc\n") == ["a", "b", "c"]


class TestWordAccuracy:
    def test_perfect_match(self):
        m = word_accuracy("The quick brown fox", "The quick brown fox.")
        assert m["word_accuracy"] == 1.0
        assert m["substitutions"] == 0
        assert m["deletions"] == 0
        assert m["insertions"] == 0

    def test_substitution_counts(self):
        m = word_accuracy("the quick brown fox", "the quick red fox")
        assert m["word_accuracy"] == 0.75
        assert m["substitutions"] == 1

    def test_deletion_counts(self):
        m = word_accuracy("the quick brown fox", "the brown fox")
        assert m["deletions"] == 1
        assert m["word_accuracy"] == 0.75

    def test_insertion_counts(self):
        m = word_accuracy("the fox", "the quick brown fox")
        assert m["insertions"] == 2
        # Standard WER: 2 insertions / 2 expected words → accuracy 0.
        assert m["word_accuracy"] == 0.0

    def test_empty_transcript_zero(self):
        assert word_accuracy("some words", "")["word_accuracy"] == 0.0

    def test_empty_expected_counts_insertions(self):
        m = word_accuracy("", "extra words")
        assert m["word_accuracy"] == 0.0
        assert m["insertions"] == 2


class TestPhonemeAccuracy:
    def test_available_when_espeak_present(self):
        # The Docker image installs espeak-ng; when absent locally, PER is
        # None and the composite renormalizes — both behaviors are valid.
        if not phonemizer_available():
            pytest.skip("espeak-ng not installed")

    def test_perfect_sentence(self):
        if not phonemizer_available():
            pytest.skip("espeak-ng not installed")
        pa = phoneme_accuracy("She thought the whole thing", "She thought the whole thing")
        assert pa == 1.0

    def test_th_to_f_chain_detected(self):
        # "Three thirsty thieves" → "Free dirty dieves": each word stays
        # recognizable to the ASR but every voiceless 'th' is wrong. WER
        # alone can't see this; PER must punish it clearly.
        if not phonemizer_available():
            pytest.skip("espeak-ng not installed")
        pa = phoneme_accuracy("Three thirsty thieves", "Free dirty dieves")
        assert pa is not None
        assert pa < 0.8

    def test_unrelated_words_score_lower(self):
        if not phonemizer_available():
            pytest.skip("espeak-ng not installed")
        high = phoneme_accuracy("The harbor", "The harbour")
        low = phoneme_accuracy("The harbor", "Yellow friday")
        assert high is not None and low is not None
        assert high > low


class TestFluency:
    def test_none_without_timestamps(self):
        assert fluency_score([]) is None
        assert fluency_score([{"word": "one", "start": 0.0, "end": 0.3}]) is None

    def test_fluent_speech(self):
        words = [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
            for i, w in enumerate("I think that this is a very good idea today".split())
        ]
        f = fluency_score(words)
        assert f is not None
        assert f["score"] > 80
        assert f["long_pauses"] == 0

    def test_long_pause_penalized(self):
        words = [
            {"word": "I", "start": 0.0, "end": 0.3},
            {"word": "think", "start": 1.5, "end": 1.8},
        ]
        f = fluency_score(words)
        assert f is not None
        assert f["long_pauses"] == 1
        assert f["score"] < 80

    def test_fillers_penalized(self):
        words = [
            {"word": "uh", "start": 0.0, "end": 0.2},
            {"word": "um", "start": 0.3, "end": 0.5},
            {"word": "yes", "start": 0.6, "end": 0.9},
        ]
        f = fluency_score(words)
        assert f is not None
        assert f["fillers"] == 2


class TestComposite:
    def test_perfect(self):
        assert composite_score(1.0, 1.0, 100.0) == 100.0

    def test_renormalizes_without_phonemes(self):
        # word 1.0, no PER (renormalized), fluency 0 → 1.0*0.6/0.75 = 80
        assert composite_score(1.0, None, 0.0) == 80.0

    def test_renormalizes_without_anything(self):
        assert composite_score(1.0, None, None) == 100.0

    def test_clamped(self):
        assert composite_score(0.0, 0.0, 0.0) == 0.0


class TestScorePronunciation:
    def test_metrics_shape(self):
        m = score_pronunciation("I live near the harbor", "I live near the harbor", None)
        assert set(m) >= {
            "word_accuracy", "substitutions", "deletions", "insertions",
            "phoneme_accuracy", "fluency", "composite",
            "phonemizer_used", "timestamps_used",
        }
        assert m["timestamps_used"] is False
        assert m["fluency"] is None

    def test_composite_degrades_with_errors(self):
        good = score_pronunciation("a b c d", "a b c d", None)["composite"]
        mid = score_pronunciation("a b c d", "a b c x", None)["composite"]
        bad = score_pronunciation("a b c d", "z y w v", None)["composite"]
        assert good > mid > bad
