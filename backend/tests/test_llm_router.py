import pytest

from app.llm.router import _extract_balanced_objects, _repair_json, _strip_code_fences, parse_lesson_json, parse_llm_json


class TestParseLessonJson:
    def test_plain_json(self):
        assert parse_lesson_json('{"title": "T", "exercises": []}')["title"] == "T"

    def test_code_fenced(self):
        content = '```json\n{"title": "T", "exercises": []}\n```'
        assert parse_lesson_json(content)["title"] == "T"

    def test_lesson_embedded_in_prose(self):
        content = 'Here is your lesson!\n{"title": "T", "exercises": [{"question": "Q", "answer": "A"}]}\nEnjoy!'
        assert parse_lesson_json(content)["exercises"][0]["answer"] == "A"

    def test_braces_inside_string_values(self):
        content = '{"title": "use {braces} carefully", "exercises": []}'
        assert parse_lesson_json(content)["title"] == "use {braces} carefully"

    def test_smart_quotes_repaired(self):
        content = '{"title": \u201cT\u201d, "exercises": []}'
        assert parse_lesson_json(content)["title"] == "T"

    def test_trailing_comma_repaired(self):
        assert parse_lesson_json('{"title": "T", "exercises": [],}')["title"] == "T"

    def test_nested_exercise_objects(self):
        content = '{"title": "T", "exercises": [{"question": "Q", "answer": "A", "meta": {"a": 1}}]}'
        assert len(parse_lesson_json(content)["exercises"]) == 1

    def test_conversational_shape_rejected(self):
        # parse_llm_json silently falls back to a {"reply": ...} dict —
        # parse_lesson_json must instead fail loudly.
        with pytest.raises(ValueError):
            parse_lesson_json('{"reply": "hello there"}')

    def test_raises_on_garbage(self):
        with pytest.raises(ValueError):
            parse_lesson_json("no json here at all")

    def test_raises_on_empty(self):
        with pytest.raises(ValueError):
            parse_lesson_json("")


class TestExtractBalancedObjects:
    def test_single_object_with_prose(self):
        assert _extract_balanced_objects('prefix {"a": 1} suffix') == ['{"a": 1}']

    def test_nested_object_extracted_as_one_block(self):
        assert _extract_balanced_objects('{"a": {"b": 2}}') == ['{"a": {"b": 2}}']

    def test_brace_inside_string_does_not_break_depth(self):
        assert _extract_balanced_objects('{"a": "literal } brace", "b": 1}') == ['{"a": "literal } brace", "b": 1}']

    def test_escaped_quote_inside_string(self):
        text = r'{"a": "say \"hi\" {"}'
        assert _extract_balanced_objects(text) == [text]

    def test_unbalanced_returns_empty(self):
        assert _extract_balanced_objects('{"a": 1') == []


class TestRepairJson:
    def test_trailing_commas(self):
        assert _repair_json('{"a": [1, 2,],}') == '{"a": [1, 2]}'

    def test_smart_quotes(self):
        assert _repair_json('{\u201ca\u201d: 1}') == '{"a": 1}'


class TestStripCodeFences:
    def test_json_fence(self):
        assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_no_fence_unchanged(self):
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'


class TestParseLlmJson:
    def test_plain_json(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_code_fenced_json(self):
        content = '```json\n{"reply": "hello", "question_count": 2, "is_complete": false}\n```'
        parsed = parse_llm_json(content)
        assert parsed["reply"] == "hello"
        assert parsed["question_count"] == 2

    def test_json_embedded_in_prose(self):
        content = 'Sure! Here is the analysis:\n{"estimated_level": "B1", "confidence": 0.7}\nHope that helps.'
        parsed = parse_llm_json(content)
        assert parsed["estimated_level"] == "B1"
        assert parsed["confidence"] == 0.7

    def test_repaired_json(self):
        content = '{"a": 1,}'
        assert parse_llm_json(content) == {"a": 1}

    def test_smart_quotes_repaired(self):
        # Smart quotes used as JSON delimiters (not inside the string value).
        content = '{"reply": \u201chello\u201d, "question_count": 1}'
        parsed = parse_llm_json(content)
        assert parsed["reply"] == "hello"
        assert parsed["question_count"] == 1

    def test_conversational_fallback(self):
        parsed = parse_llm_json("hello there")
        assert parsed["reply"] == "hello there"

    def test_reply_extraction_fallback(self):
        parsed = parse_llm_json('something {"reply": "hi"} else')
        assert parsed["reply"] == "hi"

    def test_bare_array_is_wrapped(self):
        # A top-level JSON array (e.g. a bare lessons list) must be wrapped as
        # {"items": [...]} — never returned as a list, which crashed callers
        # doing parsed.keys() with an AttributeError (plain-text 500).
        parsed = parse_llm_json('[{"topic": "A"}, {"topic": "B"}]')
        assert isinstance(parsed, dict)
        assert parsed["items"] == [{"topic": "A"}, {"topic": "B"}]

    def test_fenced_array_is_wrapped(self):
        content = '```json\n[{"topic": "A"}]\n```'
        parsed = parse_llm_json(content)
        assert isinstance(parsed, dict)
        assert parsed["items"] == [{"topic": "A"}]

    def test_array_after_repair_is_wrapped(self):
        content = '[{"topic": "A"},]'
        parsed = parse_llm_json(content)
        assert isinstance(parsed, dict)
        assert parsed["items"] == [{"topic": "A"}]

    def test_always_returns_dict(self):
        for content in ('{"a": 1}', '[1, 2]', 'prose only', ''):
            assert isinstance(parse_llm_json(content), dict)
