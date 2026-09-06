"""Deterministic pronunciation scoring from a read-aloud recording.

Pipeline (all local, no cloud):
1. Moonshine/personal-api transcribes the recording → text (+ word timestamps
   when personal-api exposes them).
2. WER — word-level edit distance between the expected sentence and the
   transcript. Catches mispronunciations that change the word.
3. PER — phoneme-level edit distance via phonemizer/espeak-ng. Catches sound
   errors ("th" → "s") that leave the word recognizable to the ASR.
4. Fluency — hesitations, long pauses and speaking rate from word timestamps.
   None when timestamps are unavailable (pre personal-api upgrade).

Composite = 0.6 * word_accuracy + 0.25 * phoneme_accuracy + 0.15 * fluency,
renormalized when a component is missing. Everything here is pure functions —
no I/O — so it is trivially testable.
"""

from __future__ import annotations

import re
import unicodedata

# phonemizer is optional at runtime: metrics degrade gracefully (no PER) when
# espeak-ng is missing. Imported lazily so tests/CI without the system lib work.
try:
    # macOS (homebrew) installs espeak-ng outside phonemizer's default search
    # paths — point at it unless the operator already set the env var. Debian
    # (the Docker image) needs none of this: apt puts it where phonemizer looks.
    import os

    if "PHONEMIZER_ESPEAK_LIBRARY" not in os.environ:
        for _candidate in (
            "/opt/homebrew/opt/espeak-ng/lib/libespeak-ng.1.dylib",
            "/usr/local/opt/espeak-ng/lib/libespeak-ng.1.dylib",
        ):
            if os.path.exists(_candidate):
                os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = _candidate
                break

    from phonemizer import phonemize as _espeak_phonemize

    _PHONEMIZER_AVAILABLE = True
except Exception:  # pragma: no cover - depends on system espeak-ng
    _PHONEMIZER_AVAILABLE = False

# Gap (seconds) between consecutive words that counts as a hesitation pause.
_PAUSE_GAP_SECONDS = 0.5
# Words/sec considered comfortably fluent for read-aloud speech.
_WPS_MIN, _WPS_IDEAL = 1.2, 2.2
_FILLER_WORDS = {"uh", "um", "er", "ah", "erm", "hmm", "mm", "huh"}


def phonemizer_available() -> bool:
    return _PHONEMIZER_AVAILABLE


_NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
    "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
    "18": "eighteen", "19": "nineteen", "20": "twenty", "30": "thirty",
    "40": "forty", "50": "fifty", "60": "sixty", "70": "seventy",
    "80": "eighty", "90": "ninety", "100": "one hundred",
}


def normalize_text(text: str) -> list[str]:
    """Lowercase, strip punctuation (keeping intra-word apostrophes), map
    standalone numbers to words, and return the word list."""
    text = unicodedata.normalize("NFKC", text).lower()
    # Keep apostrophes inside words (don't → dont would break ASR matches).
    text = re.sub(r"(?<!\w)['’](?!\w)|[^\w\s'’]", " ", text)
    words = []
    for raw in text.split():
        word = raw.replace("’", "'")
        if word in _NUMBER_WORDS:
            word = _NUMBER_WORDS[word]
        words.append(word)
    return words


def _levenshtein_ops(a: list[str], b: list[str]) -> tuple[int, int, int, int]:
    """Word/phoneme-level alignment via Levenshtein with backtrace.

    Returns (matches, substitutions, deletions, insertions) where deletions
    are expected tokens missing from the transcript and insertions are
    transcript tokens the expected text doesn't have.
    """
    rows, cols = len(a), len(b)
    # dp[i][j] = (cost, matches, subs, dels, ins)
    dp = [[(0, 0, 0, 0, 0)] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        dp[i][0] = (i, 0, 0, i, 0)
    for j in range(1, cols + 1):
        dp[0][j] = (j, 0, 0, 0, j)
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = (dp[i - 1][j - 1][0], dp[i - 1][j - 1][1] + 1,
                            dp[i - 1][j - 1][2], dp[i - 1][j - 1][3], dp[i - 1][j - 1][4])
            else:
                candidates = {
                    "sub": (dp[i - 1][j - 1][0] + 1, dp[i - 1][j - 1][1],
                            dp[i - 1][j - 1][2] + 1, dp[i - 1][j - 1][3], dp[i - 1][j - 1][4]),
                    "del": (dp[i - 1][j][0] + 1, dp[i - 1][j][1],
                            dp[i - 1][j][2], dp[i - 1][j][3] + 1, dp[i - 1][j][4]),
                    "ins": (dp[i][j - 1][0] + 1, dp[i][j - 1][1],
                            dp[i][j - 1][2], dp[i][j - 1][3], dp[i][j - 1][4] + 1),
                }
                dp[i][j] = candidates["sub"]
                for key in ("del", "ins"):
                    if candidates[key][0] < dp[i][j][0]:
                        dp[i][j] = candidates[key]
    _, matches, subs, dels, ins = dp[rows][cols]
    return matches, subs, dels, ins


def word_accuracy(expected: str, transcript: str) -> dict:
    exp, got = normalize_text(expected), normalize_text(transcript)
    if not exp:
        # Nothing expected: an empty transcript is trivially perfect, any
        # spoken word is a pure insertion error.
        return {"word_accuracy": 0.0 if got else 1.0, "substitutions": 0,
                "deletions": 0, "insertions": len(got), "expected_words": 0}
    matches, subs, dels, ins = _levenshtein_ops(exp, got)
    # Standard WER: (S + D + I) / N_expected — insertions count against the
    # score too (rambling off-script is a pronunciation/fluency error).
    errors = subs + dels + ins
    return {
        "word_accuracy": round(max(0.0, 1.0 - errors / len(exp)), 4),
        "substitutions": subs,
        "deletions": dels,
        "insertions": ins,
        "expected_words": len(exp),
    }


# --- Phoneme-level scoring -------------------------------------------------

_phoneme_cache: dict[str, str] = {}
# The bank is small (~30 unique sentences) but the cache also serves arbitrary
# user transcripts — bound it so a long-lived worker doesn't grow unbounded.
_PHONEME_CACHE_MAX = 1024


def _phonemize(text: str) -> str | None:
    if not _PHONEMIZER_AVAILABLE:
        return None
    cached = _phoneme_cache.get(text)
    if cached is not None:
        return cached
    try:
        result = _espeak_phonemize(text, language="en-us", backend="espeak", strip=True)
    except Exception:
        return None
    result = result.strip()
    if len(_phoneme_cache) >= _PHONEME_CACHE_MAX:
        _phoneme_cache.clear()
    _phoneme_cache[text] = result
    return result


def phoneme_accuracy(expected: str, transcript: str) -> float | None:
    """1 - (phoneme edit distance / expected phoneme count). None when the
    phonemizer backend is unavailable or either side phonemizes to nothing."""
    if not _PHONEMIZER_AVAILABLE:
        return None
    exp_phones = _phonemize(expected)
    got_phones = _phonemize(transcript)
    if not exp_phones or got_phones is None:
        return None
    exp_tokens = [t for t in exp_phones.split() if t]
    got_tokens = [t for t in got_phones.split() if t]
    if not exp_tokens:
        return None
    if not got_tokens:
        return 0.0
    # Phoneme tokens can be multi-char ("ɔː") — compare char streams, the
    # standard approach for per-feature phoneme distance.
    exp_stream = list("".join(exp_tokens))
    got_stream = list("".join(got_tokens))
    matches, subs, dels, ins = _levenshtein_ops(exp_stream, got_stream)
    errors = subs + dels + ins
    return round(max(0.0, 1.0 - errors / len(exp_stream)), 4)


# --- Fluency from word timestamps -------------------------------------------

def fluency_score(words: list[dict]) -> dict | None:
    """Fluency signals from Moonshine word timestamps.

    `words` is [{"word": str, "start": float, "end": float}, ...] in seconds.
    Returns None when timestamps are unavailable (len < 2) — the composite
    then renormalizes without the fluency component.
    """
    if len(words) < 2:
        return None
    duration = words[-1]["end"] - words[0]["start"]
    if duration <= 0:
        return None
    pauses, pause_total = 0, 0.0
    for prev, cur in zip(words, words[1:]):
        gap = cur["start"] - prev["end"]
        if gap > _PAUSE_GAP_SECONDS:
            pauses += 1
            pause_total += gap
    fillers = sum(1 for w in words if w.get("word", "").strip(".,;:!?").lower() in _FILLER_WORDS)
    wps = len(words) / duration
    score = 100.0
    pause_ratio = pause_total / duration
    score -= min(40.0, pause_ratio * 100.0)
    score -= min(30.0, fillers * 7.5)
    if wps < _WPS_IDEAL:
        score -= min(30.0, (_WPS_IDEAL - wps) * 30.0)
    return {
        "score": round(max(0.0, score), 1),
        "duration_seconds": round(duration, 2),
        "words_per_second": round(wps, 2),
        "long_pauses": pauses,
        "pause_total_seconds": round(pause_total, 2),
        "fillers": fillers,
    }


# --- Composite --------------------------------------------------------------

def composite_score(word_acc: float, phoneme_acc: float | None, fluency: float | None) -> float:
    """0-100 composite. Weights 60/25/15 (word/phoneme/fluency), renormalized
    when a component is missing."""
    weighted, total_w = word_acc * 0.6, 0.6
    if phoneme_acc is not None:
        weighted += phoneme_acc * 0.25
        total_w += 0.25
    if fluency is not None:
        weighted += (fluency / 100.0) * 0.15
        total_w += 0.15
    if total_w <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (weighted / total_w) * 100.0)), 1)


def score_pronunciation(expected_text: str, transcript: str, words: list[dict] | None = None) -> dict:
    """Full scoring entry point. Returns a JSON-serializable metrics dict that
    is stored on the speaking answer's AssessmentMessage.metrics."""
    word_acc = word_accuracy(expected_text, transcript)
    phon_acc = phoneme_accuracy(expected_text, transcript)
    fluency = fluency_score(words or [])
    return {
        "word_accuracy": word_acc["word_accuracy"],
        "substitutions": word_acc["substitutions"],
        "deletions": word_acc["deletions"],
        "insertions": word_acc["insertions"],
        "phoneme_accuracy": phon_acc,
        "fluency": fluency,
        "composite": composite_score(
            word_acc["word_accuracy"],
            phon_acc,
            fluency["score"] if fluency else None,
        ),
        # Provenance: lets the analysis know which components had evidence.
        "phonemizer_used": _PHONEMIZER_AVAILABLE,
        "timestamps_used": bool(words),
    }
