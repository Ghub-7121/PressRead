"""每日任务与离线示例：完成计数、草稿、连续天数、示例数据自洽。"""

import tempfile
import unittest
from pathlib import Path

from pressread.article import Article
from pressread.config import Config
from pressread.daily import DailyTask, today_key
from pressread.service import ArticleService, PracticeUnit
from pressread.store import Store


def make_unit(index: int = 0, text: str = "The kelp grows.") -> PracticeUnit:
    article = Article(
        url="https://example.com/kelp",
        source_id="wired",
        source_name="WIRED",
        category="科技",
        title="Kelp Boom",
        paragraphs=[text],
    )
    return PracticeUnit(
        article=article,
        index=index,
        text=text,
        sentences=["The kelp grows."],
        offline=False,
    )


class DailyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store = Store(base / "pressread.db")
        self.config = Config.load(base / "config.json")
        self.config.data["daily_goal"] = 2
        self.daily = DailyTask(self.store, self.config)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class DailyTaskTests(DailyTestCase):
    def test_open_creates_and_reuses(self):
        unit = make_unit()
        first = self.daily.open_unit(unit)
        second = self.daily.open_unit(unit)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["title"], "Kelp Boom")
        self.assertEqual(first["day"], today_key())

    def test_draft_then_complete(self):
        unit = make_unit()
        record = self.daily.open_unit(unit)
        self.daily.save_draft(record["id"], "海带生长。")
        self.assertEqual(self.store.get_practice(record["id"])["my_translation"], "海带生长。")
        self.assertEqual(self.daily.done_count(), 0)

        refs = [{"en": "The kelp grows.", "zh": "海带生长。"}]
        self.assertTrue(self.daily.complete(record["id"], "海带生长。", refs))
        self.assertFalse(self.daily.complete(record["id"], "海带生长。", refs))  # 重复完成不再计数
        self.assertEqual(self.daily.done_count(), 1)

    def test_goal_progress(self):
        self.assertEqual(self.daily.goal, 2)
        self.assertEqual(self.daily.remaining(), 2)
        self.assertFalse(self.daily.is_complete())

        for index in range(2):
            record = self.daily.open_unit(make_unit(index, f"Text number {index} about kelp."))
            self.daily.complete(record["id"], "", [])

        self.assertTrue(self.daily.is_complete())
        self.assertEqual(self.daily.remaining(), 0)
        self.assertGreaterEqual(self.daily.streak(), 1)

    def test_summary_shape(self):
        summary = self.daily.summary()
        self.assertEqual(summary["day"], today_key())
        self.assertEqual(summary["goal"], 2)
        self.assertEqual(summary["done"], 0)
        self.assertFalse(summary["complete"])
        self.assertIn("stats", summary)
        self.assertIn("streak", summary)

    def test_today_paragraphs_only_completed(self):
        done = self.daily.open_unit(make_unit(0, "Done paragraph about kelp farming."))
        self.daily.open_unit(make_unit(1, "Draft paragraph about kelp farming."))
        self.daily.complete(done["id"], "完成", [])
        paragraphs = self.daily.today_paragraphs()
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0]["status"], "done")

    def test_mark_active(self):
        record = self.daily.open_unit(make_unit())
        self.daily.mark_active(record["id"])
        self.assertEqual(self.store.get_practice(record["id"])["status"], "active")


class DemoArticleTests(unittest.TestCase):
    """离线示例是断网时的保底路径，数据必须自洽。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store = Store(base / "pressread.db")
        self.config = Config.load(base / "config.json")
        self.service = ArticleService(self.config, self.store, http=None)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_demo_unit(self):
        unit = self.service.load_demo_unit()
        self.assertTrue(unit.offline)
        self.assertEqual(unit.index, 0)
        self.assertTrue(unit.sentences)
        self.assertEqual(len(unit.sentences), len(unit.bundled_translations))
        self.assertTrue(all(zh for zh in unit.bundled_translations))

    def test_demo_sentences_match_splitter(self):
        from pressread.textproc import split_sentences

        unit = self.service.load_demo_unit()
        self.assertEqual(split_sentences(unit.text), unit.sentences)

    def test_demo_units_cover_every_paragraph(self):
        article = self.service.load_demo_unit().article
        count = len(article.paragraphs)
        for index in range(count):
            unit = self.service.demo_unit_at(index)
            self.assertIsNotNone(unit)
            self.assertEqual(unit.index, index)
            self.assertTrue(unit.text)
        # 越界时收敛到最后一页，不会返回空篇
        self.assertEqual(self.service.demo_unit_at(count + 5).index, count - 1)
        self.assertEqual(self.service.demo_unit_at(-3).index, 0)

    def test_demo_article_has_practice_units(self):
        article = self.service.load_demo_unit().article
        units = article.practice_units()
        self.assertGreaterEqual(len(units), 2)


if __name__ == "__main__":
    unittest.main()
