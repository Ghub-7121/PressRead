"""SQLite 存储：缓存、文章、RSS 缓存、每日练习、生词本、导出记录、连续天数。"""

import tempfile
import time
import unittest
from pathlib import Path

from pressread.store import Store, make_id


def day_str(offset_days: int = 0) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - offset_days * 86400))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "pressread.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class CacheTests(StoreTestCase):
    def test_put_get_and_hits(self):
        self.store.cache_put("word", "Kelp", {"definition": "seaweed"}, provider="oxford", scope="oxford")
        hit = self.store.cache_get("word", "  kelp  ", scope="oxford")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["payload"]["definition"], "seaweed")
        self.assertEqual(hit["hits"], 1)
        self.store.cache_get("word", "kelp", scope="oxford")
        self.assertEqual(self.store.cache_stats()["hits"], 2)

    def test_scope_isolated(self):
        self.store.cache_put("translation", "hello", {"text": "你好"}, scope="en>zh-CN")
        self.assertIsNone(self.store.cache_get("translation", "hello", scope="en>ja"))

    def test_miss_returns_none(self):
        self.assertIsNone(self.store.cache_get("word", "nothing-here"))

    def test_overwrite_keeps_single_row(self):
        self.store.cache_put("word", "kelp", {"v": 1})
        self.store.cache_put("word", "kelp", {"v": 2})
        self.assertEqual(self.store.cache_stats()["total"], 1)
        self.assertEqual(self.store.cache_get("word", "kelp")["payload"]["v"], 2)

    def test_max_age_rejects_stale_entry(self):
        self.store.cache_put("word", "kelp", {"v": 1})
        self.assertIsNone(self.store.cache_get("word", "kelp", max_age=0))
        self.assertIsNotNone(self.store.cache_get("word", "kelp", max_age=3600))
        # 过期只是不当作命中，条目仍在，等「清理过期」删除
        self.assertEqual(self.store.cache_stats()["total"], 1)
        self.assertEqual(self.store.cache_stats()["hits"], 1)

    def test_purge_all_and_expired(self):
        self.store.cache_put("word", "a", {})
        self.store.cache_put("word", "b", {})
        self.assertEqual(self.store.cache_purge(max_age_seconds=3600), 0)
        self.assertEqual(self.store.cache_purge(), 2)
        self.assertEqual(self.store.cache_stats()["total"], 0)


class ArticleTests(StoreTestCase):
    def test_save_get_known(self):
        article = {
            "url": "https://example.com/kelp",
            "source_id": "wired",
            "source_name": "WIRED",
            "category": "科技",
            "title": "Kelp",
            "paragraphs": ["a" * 80, "b" * 80],
            "author": "Mara",
            "published": "2026-09-14",
            "word_count": 20,
            "fetched_at": time.time(),
        }
        article_id = self.store.save_article(article)
        self.assertEqual(article_id, make_id(article["url"]))
        self.assertTrue(self.store.article_known(article["url"]))
        self.assertFalse(self.store.article_known("https://example.com/other"))
        again = self.store.get_article(article_id)
        self.assertEqual(again["title"], "Kelp")
        self.assertEqual(again["paragraphs"], article["paragraphs"])

    def test_save_is_idempotent_for_same_url(self):
        article = {
            "url": "https://example.com/x",
            "source_id": "wired",
            "source_name": "WIRED",
            "category": "科技",
            "title": "First",
            "paragraphs": ["x" * 80],
            "word_count": 10,
            "fetched_at": time.time(),
        }
        self.store.save_article(article)
        article["title"] = "Second"
        article_id = self.store.save_article(article)
        self.assertEqual(self.store.stats()["articles"], 1)
        self.assertEqual(self.store.get_article(article_id)["title"], "Second")


class FeedTests(StoreTestCase):
    def test_feed_ttl(self):
        self.store.save_feed("wired", [{"title": "a", "url": "https://example.com/a"}])
        self.assertIsNotNone(self.store.get_feed("wired", max_age_seconds=600))
        self.assertIsNone(self.store.get_feed("wired", max_age_seconds=0))
        self.assertIsNone(self.store.get_feed("npr", max_age_seconds=600))


class PracticeTests(StoreTestCase):
    def _open(self, day: str, index: int = 0, title: str = "Kelp") -> dict:
        return self.store.ensure_practice(
            day, "article-1", index, "The kelp grows.", source_name="WIRED", title=title
        )

    def test_ensure_is_idempotent(self):
        first = self._open(day_str(), 0)
        second = self._open(day_str(), 0)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.day_practices(day_str())), 1)

    def test_draft_and_complete(self):
        day = day_str()
        practice = self._open(day)
        self.store.update_practice(practice["id"], my_translation="海带生长。")
        self.assertEqual(self.store.get_practice(practice["id"])["my_translation"], "海带生长。")
        self.assertEqual(self.store.day_done_count(day), 0)

        refs = [{"en": "The kelp grows.", "zh": "海带生长。"}]
        self.store.complete_practice(practice["id"], "海带生长。", refs)
        self.assertEqual(self.store.day_done_count(day), 1)
        done = self.store.day_done(day)
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["refs"], refs)

    def test_update_ignores_unknown_fields(self):
        practice = self._open(day_str())
        self.store.update_practice(practice["id"], hacker="x", status="active")
        row = self.store.get_practice(practice["id"])
        self.assertEqual(row["status"], "active")
        self.assertNotIn("hacker", row)

    def test_stats(self):
        day = day_str()
        for index in range(3):
            practice = self._open(day, index)
            self.store.complete_practice(practice["id"], "", [])
        stats = self.store.stats()
        self.assertEqual(stats["paragraphs"], 3)
        self.assertEqual(stats["days"], 1)


class StreakTests(StoreTestCase):
    def test_streak_counts_back_from_yesterday(self):
        for offset in (1, 2):
            practice = self.store.ensure_practice(
                day_str(offset), "a", 0, "text", source_name="WIRED", title="t"
            )
            self.store.complete_practice(practice["id"], "", [])
        self.assertEqual(self.store.streak(goal=1), 2)
        self.assertEqual(self.store.streak(goal=2), 0)

    def test_streak_includes_today_when_done(self):
        for offset in (0, 1):
            practice = self.store.ensure_practice(
                day_str(offset), "a", 0, "text", source_name="WIRED", title="t"
            )
            self.store.complete_practice(practice["id"], "", [])
        self.assertEqual(self.store.streak(goal=1), 2)


class WordAndExportTests(StoreTestCase):
    def test_saved_words(self):
        self.store.add_word("Kelp", {"provider": "local"})
        self.assertTrue(self.store.is_saved_word("kelp"))
        words = self.store.saved_words()
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["entry"]["provider"], "local")
        self.store.remove_word("KELP")
        self.assertFalse(self.store.is_saved_word("kelp"))

    def test_export_history(self):
        self.store.record_export("1.0.0", day_str(), "C:/tmp/a.md", "both", 5)
        self.store.record_export("1.0.1", day_str(), "C:/tmp/b.md", "both", 5)
        history = self.store.export_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["version"], "1.0.1")

    def test_meta_roundtrip(self):
        self.assertIsNone(self.store.meta_get("nothing"))
        self.assertEqual(self.store.meta_get("nothing", "fallback"), "fallback")
        self.store.meta_set("export_version", "1.0.7")
        self.assertEqual(self.store.meta_get("export_version"), "1.0.7")


class PrefetchCacheTests(StoreTestCase):
    """并发抓取落选结果的预取缓存：独立表，清理时只动这张表。"""

    @staticmethod
    def article(url: str, text: str = "Kelp forests grow fast.") -> dict:
        return {
            "url": url,
            "source_id": "wired",
            "source_name": "WIRED",
            "category": "科技",
            "title": "A title",
            "paragraphs": [text],
            "author": "",
            "published": "",
            "word_count": 5,
            "fetched_at": time.time(),
            "offline": False,
        }

    def test_roundtrip(self):
        self.store.save_prefetch(self.article("https://x.test/a"))
        rows = self.store.prefetch_recent(3600)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://x.test/a")
        self.assertEqual(rows[0]["paragraphs"], ["Kelp forests grow fast."])

    def test_overwrite_keeps_single_row(self):
        self.store.save_prefetch(self.article("https://x.test/a", "one"))
        self.store.save_prefetch(self.article("https://x.test/a", "two"))
        rows = self.store.prefetch_recent(3600)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["paragraphs"], ["two"])

    def test_expired_is_hidden_and_deleted(self):
        self.store.save_prefetch(
            self.article("https://x.test/old"), fetched_at=time.time() - 7200
        )
        self.store.save_prefetch(self.article("https://x.test/new"))
        rows = self.store.prefetch_recent(3600)
        self.assertEqual([r["url"] for r in rows], ["https://x.test/new"])
        # 过期行被顺手删掉，只剩 1 条
        self.assertEqual(self.store.prefetch_stats()["count"], 1)

    def test_newest_first(self):
        self.store.save_prefetch(self.article("https://x.test/a"), fetched_at=time.time() - 100)
        self.store.save_prefetch(self.article("https://x.test/b"), fetched_at=time.time())
        rows = self.store.prefetch_recent(3600)
        self.assertEqual([r["url"] for r in rows], ["https://x.test/b", "https://x.test/a"])

    def test_purge_only_touches_prefetch_table(self):
        """清理预取缓存时，词典/翻译缓存、历史文章、RSS 缓存、每日练习都不能丢。"""
        self.store.cache_put("word", "kelp", {"definition": "seaweed"})
        self.store.cache_put("translation", "hello", {"text": "你好"})
        self.store.save_article(self.article("https://x.test/kept"))
        self.store.save_feed("wired", [{"url": "https://x.test/f", "title": "t"}])
        self.store.ensure_practice("2026-10-30", "aid", 0, "text")
        self.store.save_prefetch(self.article("https://x.test/p1"))
        self.store.save_prefetch(self.article("https://x.test/p2"))

        removed = self.store.prefetch_purge()

        self.assertEqual(removed, 2)
        self.assertEqual(self.store.prefetch_stats()["count"], 0)
        self.assertIsNotNone(self.store.cache_get("word", "kelp"))
        self.assertIsNotNone(self.store.cache_get("translation", "hello"))
        self.assertEqual(self.store.cache_stats()["total"], 2)
        self.assertTrue(self.store.article_known("https://x.test/kept"))
        self.assertIsNotNone(self.store.get_feed("wired", 3600))
        self.assertEqual(self.store.day_done_count("2026-10-30"), 0)
        self.assertEqual(len(self.store.day_practices("2026-10-30")), 1)

    def test_purge_by_age(self):
        self.store.save_prefetch(self.article("https://x.test/old"), fetched_at=time.time() - 7200)
        self.store.save_prefetch(self.article("https://x.test/new"))
        self.assertEqual(self.store.prefetch_purge(3600), 1)
        self.assertEqual(self.store.prefetch_stats()["count"], 1)


if __name__ == "__main__":
    unittest.main()
