"""Curated item banks for the multi-skill assessment sections.

Listening items are played as audio only — the student never sees the text —
so each `tutor_text` must be self-contained when spoken. Items are ordered by
increasing CEFR band; the section stops early after consecutive failures.

Speaking items are read-aloud sentences with a phonetic focus. They graduate
from simple to genuinely difficult so the WER/PER scoring has resolution at
every level.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ListeningItem:
    id: str
    band: str  # CEFR band this item targets
    tutor_text: str  # spoken aloud via TTS, hidden from the student
    expected_answer: str  # model answer used by the grader


@dataclass(frozen=True)
class SpeakingItem:
    id: str
    band: str
    text: str  # the sentence the student reads aloud
    focus: str  # phonetic focus, shown to the student


# Ordered easy → hard. Grading is lenient: any response capturing the key
# points counts as correct (the LLM grader sees tutor_text + expected_answer).
LISTENING_ITEMS: list[ListeningItem] = [
    ListeningItem(
        id="lis_a2_1",
        band="A2",
        tutor_text=(
            "Hi! Listen carefully. My name is Sarah and I work at a small bakery "
            "on Maple Street. We open at seven in the morning and close at three "
            "in the afternoon. Now, a question: what time does the bakery close?"
        ),
        expected_answer="The bakery closes at three in the afternoon.",
    ),
    ListeningItem(
        id="lis_b1_1",
        band="B1",
        tutor_text=(
            "Here is the next one. Yesterday I planned to go running in the park, "
            "but it started raining, so I stayed home and watched a documentary "
            "about penguins instead. Question: why didn't the speaker go running?"
        ),
        expected_answer="Because it started raining, so they stayed home.",
    ),
    ListeningItem(
        id="lis_b1_2",
        band="B1",
        tutor_text=(
            "Next. A man is giving directions: 'Go straight for two blocks, then "
            "turn left at the pharmacy. The museum is the third door on your "
            "right, next to the café.' Question: what building is next to the museum?"
        ),
        expected_answer="The café.",
    ),
    ListeningItem(
        id="lis_b2_1",
        band="B2",
        tutor_text=(
            "This one is longer. Companies used to think that remote work would "
            "hurt productivity, but recent studies suggest the opposite: most "
            "employees get more done at home, although they feel lonelier and "
            "miss face-to-face contact with colleagues. Question: according to "
            "the speaker, what is the main drawback of remote work?"
        ),
        expected_answer="Employees feel lonely / miss face-to-face contact with colleagues, even though productivity improved.",
    ),
    ListeningItem(
        id="lis_c1_1",
        band="C1",
        tutor_text=(
            "Almost done. Listen to this: 'Had the committee heeded the auditor's "
            "warnings, the breach might never have occurred; instead, the report "
            "was shelved and its author quietly reassigned.' Question: what "
            "happened to the report, and what does that imply about the committee?"
        ),
        expected_answer="The report was shelved (ignored) and its author reassigned — implying the committee failed to act on the warnings, so the breach was preventable.",
    ),
    ListeningItem(
        id="lis_c1_2",
        band="C1",
        tutor_text=(
            "Last one. 'While the new policy is touted as a triumph of "
            "environmental regulation, critics contend that it merely shifts "
            "pollution overseas rather than reducing it outright.' Question: "
            "what do critics say the policy actually does to pollution?"
        ),
        expected_answer="It shifts pollution overseas rather than actually reducing it.",
    ),
]

# Ordered easy → hard, targeting common English pronunciation challenges.
SPEAKING_ITEMS: list[SpeakingItem] = [
    SpeakingItem(
        id="spk_a2_1",
        band="A2",
        text="I live near the harbor and work every day.",
        focus="Word stress and the 'r' in 'harbor' and 'work'.",
    ),
    SpeakingItem(
        id="spk_b1_1",
        band="B1",
        text="She thought the whole thing was thoroughly boring.",
        focus="The 'th' sounds in 'thought', 'thing', 'thoroughly'.",
    ),
    SpeakingItem(
        id="spk_b1_2",
        band="B1",
        text="The very worst weather was in February and September.",
        focus="'v' vs 'w' in 'very' and 'weather'; month endings.",
    ),
    SpeakingItem(
        id="spk_b2_1",
        band="B2",
        text="Particularly comfortable clothes are necessary for traveling.",
        focus="Long consonant clusters: 'particularly', 'comfortable', 'necessary'.",
    ),
    SpeakingItem(
        id="spk_c1_1",
        band="C1",
        text="Three thirsty thieves thwarted the thorough search on Thursday.",
        focus="Voiceless 'th' chains and the 'thr' clusters.",
    ),
    SpeakingItem(
        id="spk_c1_2",
        band="C1",
        text="Rural jurors routinely ruled on controversial judicial behavior.",
        focus="The 'r/l' contrasts and 'juror/judicial' word stress.",
    ),
]

# How many consecutive wrong answers end the listening section early.
LISTENING_EARLY_STOP_FAILS = 2
# Safety cap (the bank itself is 6, but this documents the intent).
LISTENING_MAX_ITEMS = len(LISTENING_ITEMS)
SPEAKING_MAX_ITEMS = len(SPEAKING_ITEMS)
