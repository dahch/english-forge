import pytest

from app.llm.openai_compatible import _extract_content
from app.llm.router import LLMRouter, _extract_balanced_objects, _repair_json, _strip_code_fences, parse_lesson_json, parse_llm_json


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


class TestExtractContent:
    def test_content_present(self):
        data = {"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}], "usage": {}}
        content, finish = _extract_content(data, "test-provider")
        assert content == "hi"
        assert finish == "stop"

    def test_reasoning_content_never_used_as_content(self):
        # Reasoning models return their chain-of-thought in reasoning_content
        # when the budget ran out. It must NOT leak as content — the router
        # retries with a bigger budget instead.
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": None, "reasoning_content": '{"correct": true}'},
                "finish_reason": "length",
            }],
            "usage": {"completion_tokens": 200},
        }
        content, finish = _extract_content(data, "test-provider")
        assert content is None
        assert finish == "length"

    def test_empty_content_stays_empty(self):
        data = {"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}], "usage": {}}
        content, finish = _extract_content(data, "test-provider")
        assert content is None
        assert finish == "length"

    def test_blank_reasoning_content_ignored(self):
        data = {"choices": [{"message": {"content": "", "reasoning_content": "   "}, "finish_reason": "stop"}], "usage": {}}
        content, _ = _extract_content(data, "test-provider")
        assert content == ""

    def test_missing_choices(self):
        content, finish = _extract_content({}, "test-provider")
        assert content is None
        assert finish is None


class _ScriptedProvider:
    """ChatProvider stub returning queued results (or raising) per call, and
    recording the max_tokens budget of each request."""

    def __init__(self, results, name: str = "scripted"):
        self._results = list(results)
        self._name = name
        self.calls: list[int] = []

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return "test-model"

    async def complete(self, messages, system_prompt, *, temperature=0.7, max_tokens=2048, response_format=None):
        self.calls.append(max_tokens)
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestCompleteWithFallback:
    @pytest.mark.asyncio
    async def test_retries_with_bigger_budget_on_length(self):
        # Reasoning model burns the budget thinking → empty content with
        # finish_reason="length". The router retries the same provider once
        # with a larger budget before moving on.
        provider = _ScriptedProvider([
            {"content": None, "finish_reason": "length", "usage": {}},
            {"content": '{"ok": true}', "finish_reason": "stop", "usage": {}},
        ])
        router = LLMRouter(None, "user")
        router._providers = [provider]

        result = await router.complete_with_fallback(messages=[], system_prompt="s", max_tokens=200)
        assert result["content"] == '{"ok": true}'
        assert result["provider"] == "scripted"
        assert provider.calls == [200, 4096]

    @pytest.mark.asyncio
    async def test_empty_content_without_length_moves_on_without_retry(self):
        # Empty content with a non-length finish_reason means the provider
        # produced nothing — more tokens won't help, so it's failed after a
        # single call (no budget bump) and the next provider is tried.
        failing = _ScriptedProvider([
            {"content": None, "finish_reason": "stop", "usage": {}},
        ], name="failing")
        ok = _ScriptedProvider([{"content": "fine", "finish_reason": "stop", "usage": {}}], name="ok")
        router = LLMRouter(None, "user")
        router._providers = [failing, ok]

        result = await router.complete_with_fallback(messages=[], system_prompt="s")
        assert result["content"] == "fine"
        assert result["provider"] == "ok"
        # Failing provider was called once, at the default budget, no retry.
        assert failing.calls == [2048]

    @pytest.mark.asyncio
    async def test_retry_exhausted_moves_to_next_provider(self):
        first = _ScriptedProvider([
            {"content": None, "finish_reason": "length", "usage": {}},
            {"content": None, "finish_reason": "length", "usage": {}},
        ], name="first")
        second = _ScriptedProvider([{"content": "ok", "finish_reason": "stop", "usage": {}}], name="second")
        router = LLMRouter(None, "user")
        router._providers = [first, second]

        result = await router.complete_with_fallback(messages=[], system_prompt="s", max_tokens=200)
        assert result["provider"] == "second"
        assert first.calls == [200, 4096]

    @pytest.mark.asyncio
    async def test_all_providers_failing_raises(self):
        provider = _ScriptedProvider([{"content": None, "finish_reason": "stop", "usage": {}}], name="only")
        router = LLMRouter(None, "user")
        router._providers = [provider]

        with pytest.raises(RuntimeError, match="All providers failed"):
            await router.complete_with_fallback(messages=[], system_prompt="s")
