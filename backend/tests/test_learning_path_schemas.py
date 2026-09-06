import json
from datetime import datetime, timezone

from app.routers.learning_paths import PathLessonResponse


def _lesson(content) -> PathLessonResponse:
    return PathLessonResponse(
        id="l1",
        path_id="p1",
        lesson_type="grammar",
        topic="Topic",
        description="Description",
        content=content,
        order=1,
        completed=False,
        completed_at=None,
    )


class TestPathLessonResponseContent:
    def test_json_string_content_is_parsed_to_object(self):
        # Production 500: content is stored as a JSON string but the
        # response model must expose it as an object — a str annotation
        # failed validation AFTER the path had been persisted.
        stored = json.dumps({"focus": "Hedging", "lesson_type": "conversation"})
        lesson = _lesson(stored)
        assert lesson.content == {"focus": "Hedging", "lesson_type": "conversation"}

    def test_object_content_passes_through(self):
        content = {"focus": "Cleft sentences", "lesson_type": "grammar"}
        assert _lesson(content).content == content

    def test_null_content_allowed(self):
        assert _lesson(None).content is None

    def test_unparseable_content_degrades_to_null(self):
        # Corrupt content must not 500 the whole path response.
        assert _lesson("not json").content is None

    def test_double_encoded_non_object_degrades_to_null(self):
        # Valid JSON that parses to a non-object (double-encoded array/
        # number) must also degrade to None, not fail dict validation
        # post-persist (review finding).
        assert _lesson('"[1, 2]"').content is None
        assert _lesson('"5"').content is None
        assert _lesson('"null"').content is None

    def test_full_serialization_round_trip(self):
        stored = json.dumps({"focus": "F", "lesson_type": "vocabulary"})
        lesson = _lesson(stored)
        dumped = lesson.model_dump(mode="json")
        assert isinstance(dumped["content"], dict)
        assert dumped["completed"] is False
        assert isinstance(dumped["completed_at"], datetime) or dumped["completed_at"] is None
