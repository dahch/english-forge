import json

from app.routers.lessons import normalize_llm_lesson


class TestNormalizeLlmLesson:
    def test_clean_lesson_passthrough(self):
        lesson = {
            "title": "T",
            "topic": "grammar",
            "level": "B1",
            "explanation": "E",
            "examples": ["a"],
            "exercises": [{"question": "Q", "answer": "A"}],
        }
        n = normalize_llm_lesson(lesson)
        assert n["title"] == "T"
        assert n["exercises"][0]["answer"] == "A"

    def test_double_encoded_lists(self):
        n = normalize_llm_lesson({
            "examples": json.dumps(["a", "b"]),
            "exercises": json.dumps([{"question": "Q", "answer": "A"}]),
        })
        assert n["examples"] == ["a", "b"]
        assert n["exercises"][0]["question"] == "Q"

    def test_double_encoded_exercise_strings(self):
        n = normalize_llm_lesson({"exercises": [json.dumps({"question": "Q", "answer": "A"})]})
        assert n["exercises"] == [{"question": "Q", "answer": "A"}]

    def test_scalar_coerced_to_list(self):
        n = normalize_llm_lesson({"examples": "just one", "exercises": {"question": "Q", "answer": "A"}})
        assert n["examples"] == ["just one"]
        assert n["exercises"][0]["question"] == "Q"

    def test_junk_exercises_dropped(self):
        n = normalize_llm_lesson({
            "exercises": [
                "not json {",
                {"noquestion": "x"},
                {"question": "   "},
                {"question": "Keep me", "answer": 5},
            ]
        })
        assert [e["question"] for e in n["exercises"]] == ["Keep me"]
        assert n["exercises"][0]["answer"] == "5"

    def test_title_fallback(self):
        assert normalize_llm_lesson({})["title"] == "Personalized Lesson"

    def test_empty_string_fields_become_empty_lists(self):
        n = normalize_llm_lesson({"examples": "", "exercises": ""})
        assert n["examples"] == []
        assert n["exercises"] == []

    def test_whitespace_stripped_from_question(self):
        n = normalize_llm_lesson({"exercises": [{"question": "  Q  ", "answer": "A"}]})
        assert n["exercises"][0]["question"] == "Q"
