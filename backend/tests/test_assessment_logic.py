from app.services.assessment_flow import wants_to_finish


class TestWantsToFinish:
    def test_short_english(self):
        assert wants_to_finish("let's finish")

    def test_im_done(self):
        assert wants_to_finish("I'm done")

    def test_spanish(self):
        assert wants_to_finish("terminar")

    def test_with_punctuation(self):
        assert wants_to_finish("stop!")

    def test_three_words(self):
        assert wants_to_finish("I want to stop")

    def test_mentioning_words_in_long_sentence_does_not_trigger(self):
        # "done" mentioned inside a longer statement is not an end request.
        assert not wants_to_finish("I'm done with work for today, how about you")

    def test_unrelated_message(self):
        assert not wants_to_finish("I like swimming in the ocean")

    def test_empty(self):
        assert not wants_to_finish("")

    def test_word_containing_end_word_is_not_a_match(self):
        assert not wants_to_finish("I finished my homework yesterday")
