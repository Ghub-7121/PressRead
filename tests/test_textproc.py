"""文本工具：分句、清洗、样板过滤、难度与练习评分。"""

import unittest

from pressread import textproc as tp


class SentenceSplitTests(unittest.TestCase):
    def test_basic_three(self):
        self.assertEqual(
            tp.split_sentences("One thing. Two things! Three?"),
            ["One thing.", "Two things!", "Three?"],
        )

    def test_abbreviation_kept(self):
        text = "Dr. Chen left the lab at noon."
        self.assertEqual(tp.split_sentences(text), [text])

    def test_us_abbreviation(self):
        text = "The U.S. economy grew slowly. Analysts were surprised."
        self.assertEqual(len(tp.split_sentences(text)), 2)

    def test_decimal_not_split(self):
        text = "It grew 3.5 percent in June. That surprised us."
        self.assertEqual(len(tp.split_sentences(text)), 2)

    def test_quote_closing(self):
        text = 'He said, "Go home." Then he left.'
        self.assertEqual(len(tp.split_sentences(text)), 2)

    def test_ellipsis(self):
        text = "She paused... then continued. Nobody spoke."
        self.assertEqual(len(tp.split_sentences(text)), 2)

    def test_trailing_space_and_empty(self):
        self.assertEqual(tp.split_sentences("   "), [])
        self.assertEqual(tp.split_sentences("Only one sentence"), ["Only one sentence"])

    def test_spans_match_sentences(self):
        text = (
            "Kelp absorbs nitrogen as it grows. A single acre can pull several tons "
            "out of the water in a season, researchers say. That effect is local."
        )
        spans = tp.sentence_spans(text)
        pieces = [text[a:b] for a, b in spans]
        self.assertEqual(pieces, tp.split_sentences(text))
        self.assertEqual(len(pieces), 3)


class CleanTests(unittest.TestCase):
    def test_whitespace_and_citation(self):
        self.assertEqual(
            tp.clean_paragraph("New   research [12] shows\nthis."),
            "New research shows this.",
        )

    def test_space_before_punctuation(self):
        self.assertEqual(tp.clean_paragraph("Wait , what ?"), "Wait, what?")

    def test_bracket_spacing(self):
        self.assertEqual(tp.clean_paragraph("The ( kelp ) farm."), "The (kelp) farm.")

    def test_empty(self):
        self.assertEqual(tp.clean_paragraph(""), "")

    def test_ligature(self):
        self.assertEqual(tp.normalize_space("pro\ufb01le"), "profile")


class BoilerplateTests(unittest.TestCase):
    BODY = (
        "Kelp absorbs nitrogen and phosphorus as it grows, which is precisely what "
        "coastal waters receive too much of from septic systems and lawn runoff."
    )

    def test_body_is_not_boilerplate(self):
        self.assertFalse(tp.is_boilerplate(self.BODY))

    def test_subscribe(self):
        self.assertTrue(
            tp.is_boilerplate(
                "Subscribe now and get unlimited access to everything we publish, every day."
            )
        )

    def test_photo_credit(self):
        self.assertTrue(tp.is_boilerplate("Photograph: Mara Ellison"))
        self.assertTrue(tp.is_boilerplate("A barge at dawn. Photograph: Mara Ellison"))

    def test_reporter_bio(self):
        self.assertTrue(
            tp.is_boilerplate("Mara Ellison is a senior reporter covering the ocean economy.")
        )

    def test_too_short(self):
        self.assertTrue(tp.is_boilerplate("Too short."))


class WordTests(unittest.TestCase):
    def test_words_in_offsets(self):
        text = "The kelp farm's yield doubled."
        words = [w for w, _s, _e in tp.words_in(text)]
        self.assertEqual(words, ["The", "kelp", "farm's", "yield", "doubled"])

    def test_normalize_word(self):
        self.assertEqual(tp.normalize_word("Twilight,"), "twilight")
        self.assertEqual(tp.normalize_word("children’s"), "children")
        self.assertEqual(tp.normalize_word("(Kelp)"), "kelp")

    def test_word_count_ignores_numbers(self):
        self.assertEqual(tp.word_count("There are 40 farms in 6 states."), 5)


class DifficultyTests(unittest.TestCase):
    def test_short_sentence_is_easy(self):
        level, detail = tp.level_estimate("The sea is deep. Fish swim here.")
        self.assertEqual(level, "A2")
        self.assertIn("平均句长", detail)

    def test_very_long_sentence_is_hard(self):
        long_sentence = " ".join(["word"] * 45) + "."
        self.assertEqual(tp.level_estimate(long_sentence)[0], "C2")

    def test_empty(self):
        self.assertEqual(tp.level_estimate(""), ("—", "暂无数据"))

    def test_practice_score_prefers_medium_paragraph(self):
        text = (
            "Kelp absorbs nitrogen and phosphorus as it grows, which is precisely what "
            "coastal waters receive too much of from septic systems, lawn runoff and "
            "sewage overflows. A single acre of the stuff can pull several tons of "
            "nitrogen out of the water in a season. Researchers caution that the effect "
            "is local and temporary once the crop is hauled ashore."
        )
        self.assertGreaterEqual(tp.practice_score(text), 5.0)

    def test_practice_score_rejects_boilerplate(self):
        self.assertLess(tp.practice_score("Subscribe now to read the rest of this story."), 0)


class SplitLongTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(tp.split_long_paragraph("Short one."), ["Short one."])

    def test_long_text_chunked(self):
        sentence = "The kelp grows quickly in cold water and needs no fertilizer at all."
        text = " ".join([sentence] * 40)
        chunks = tp.split_long_paragraph(text, max_chars=720)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 760)
        self.assertEqual(" ".join(chunks), tp.normalize_space(text))


class ParagraphStartTests(unittest.TestCase):
    """paragraph_starts：空行分段后，每段第一句在整体句子列表里的下标。"""

    def test_single_paragraph(self):
        text = "The kelp grows quickly. It needs cold water."
        self.assertEqual(tp.paragraph_starts(text), [0])

    def test_two_paragraphs(self):
        text = "First one. Second one.\n\nThird one."
        sentences = tp.split_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertEqual(tp.paragraph_starts(text, sentences), [0, 2])

    def test_blank_lines_with_spaces(self):
        text = "First one. Second one.\n   \nThird one."
        self.assertEqual(tp.paragraph_starts(text), [0, 2])

    def test_mismatch_falls_back_to_single_paragraph(self):
        """分句口径对不上时宁可整段显示，也不要把句子错位到别的段。"""
        text = "First one. Second one.\n\nThird one."
        self.assertEqual(tp.paragraph_starts(text, ["Only one sentence."]), [0])

    def test_empty_text(self):
        self.assertEqual(tp.paragraph_starts(""), [0])


class TitleKeyTests(unittest.TestCase):
    def test_normalized(self):
        self.assertEqual(
            tp.title_case_key("The Ocean’s Twilight-Zone!"),
            "the ocean s twilight zone",
        )


if __name__ == "__main__":
    unittest.main()
