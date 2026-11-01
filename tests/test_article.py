"""正文抽取：容器投票、图注/样板过滤、JSON-LD 兜底、练习段落切分。"""

import json
import unittest
from pathlib import Path

from pressread.article import extract_article, metadata_only

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureArticleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (FIXTURES / "sample_article.html").read_text(encoding="utf-8")
        cls.article = extract_article(
            html,
            url="https://www.wired.com/story/kelp",
            source_id="wired",
            source_name="WIRED",
        )

    def test_title_strips_site_suffix(self):
        self.assertEqual(self.article.title, "The Quiet Rise of Urban Kelp Farms")

    def test_author_and_date(self):
        self.assertEqual(self.article.author, "Mara Ellison")
        self.assertEqual(self.article.published, "2026-09-14")

    def test_body_paragraphs(self):
        paragraphs = self.article.paragraphs
        self.assertEqual(len(paragraphs), 6)
        self.assertTrue(paragraphs[0].startswith("On a converted barge"))
        self.assertIn("shared dryer", paragraphs[4])

    def test_caption_and_newsletter_dropped(self):
        joined = " ".join(self.article.paragraphs)
        self.assertNotIn("Photograph", joined)
        self.assertNotIn("newsletter", joined.lower())
        self.assertNotIn("All rights reserved", joined)

    def test_navigation_dropped(self):
        joined = " ".join(self.article.paragraphs).lower()
        self.assertNotIn("subscribe", joined)

    def test_word_count_matches_paragraphs(self):
        expected = sum(len(p.split()) for p in self.article.paragraphs)
        self.assertEqual(self.article.word_count, expected)

    def test_practice_units(self):
        units = self.article.practice_units()
        self.assertGreaterEqual(len(units), 4)
        self.assertEqual([u["index"] for u in units], list(range(len(units))))
        for unit in units:
            self.assertGreaterEqual(unit["words"], 30)
            self.assertLessEqual(unit["words"], 150)
            self.assertGreaterEqual(len(unit["sentences"]), 1)
            self.assertIn(unit["level"][0], {"A2", "B1", "B2", "C1", "C2"})
            self.assertGreater(unit["score"], 0)

    def test_to_dict_roundtrip(self):
        data = self.article.to_dict()
        self.assertEqual(data["paragraphs"], self.article.paragraphs)
        self.assertEqual(data["title"], self.article.title)


class TitleTests(unittest.TestCase):
    def _extract(self, title: str, source_name: str = "WIRED"):
        html = f"<html><head><title>{title}</title></head><body></body></html>"
        return extract_article(
            html, url="https://example.com/a", source_id="wired", source_name=source_name
        ).title

    def test_known_suffix_removed(self):
        self.assertEqual(self._extract("Kelp Farms Are Everywhere | WIRED"), "Kelp Farms Are Everywhere")

    def test_source_name_suffix_removed(self):
        self.assertEqual(
            self._extract("Kelp Farms Are Everywhere — Quanta Magazine", "Quanta Magazine"),
            "Kelp Farms Are Everywhere",
        )

    def test_unknown_suffix_kept(self):
        title = "Kelp Farms Are Everywhere | A Field Guide"
        self.assertEqual(self._extract(title), title)


class JsonLdTests(unittest.TestCase):
    def test_body_from_jsonld_when_no_html_paragraphs(self):
        body = (
            "The first paragraph of a story that only exists as structured data, long "
            "enough to pass the ninety character threshold used by the extractor.\n\n"
            "The second paragraph continues the story with more detail about the farms "
            "and the people who run them along a cold and windy stretch of coast.\n\n"
            "A third paragraph wraps up the piece with a look at what comes next for the "
            "growers and the small towns that depend on the seasonal work."
        )
        payload = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "The Structured Data Story",
            "author": {"name": "Ada Lopez"},
            "articleBody": body,
        }
        html = (
            "<html><head><title>Fallback page</title>"
            f'<script type="application/ld+json">{json.dumps(payload)}</script>'
            "</head><body><div id='app'></div></body></html>"
        )
        article = extract_article(
            html, url="https://example.com/ld", source_id="wired", source_name="WIRED"
        )
        self.assertEqual(article.title, "The Structured Data Story")
        self.assertEqual(article.author, "Ada Lopez")
        self.assertEqual(len(article.paragraphs), 3)


class MetadataOnlyTests(unittest.TestCase):
    def test_long_summary_becomes_paragraph(self):
        summary = (
            "Researchers say the kelp farms along the northeastern coast cleaned several "
            "tons of nitrogen out of the water this season, though the effect is local."
        )
        article = metadata_only(
            url="https://example.com/x",
            source_id="wired",
            source_name="WIRED",
            category="科技",
            title="Kelp, by the numbers",
            summary=summary,
        )
        self.assertEqual(len(article.paragraphs), 1)
        self.assertTrue(article.paragraphs[0].startswith("Researchers say"))

    def test_short_summary_dropped(self):
        article = metadata_only(
            url="https://example.com/y",
            source_id="wired",
            source_name="WIRED",
            category="科技",
            title="Too short",
            summary="Kelp is good.",
        )
        self.assertEqual(article.paragraphs, [])
        self.assertEqual(article.title, "Too short")


class EmptyPageTests(unittest.TestCase):
    def test_empty_html_uses_fallback_title(self):
        article = extract_article(
            "<html><body></body></html>",
            url="https://example.com/z",
            source_id="wired",
            source_name="WIRED",
            fallback_title="Kelp from the feed",
        )
        self.assertEqual(article.title, "Kelp from the feed")
        self.assertEqual(article.paragraphs, [])


if __name__ == "__main__":
    unittest.main()
