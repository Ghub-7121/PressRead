"""并发抓取编排：竞速取第一个成功结果、落选结果入预取缓存、超时兜底。

这一组测试不联网：HttpClient 换成可编排延迟的替身，Store 用临时库。
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from pressread.feeds import SOURCE_MAP, FeedItem
from pressread.fetch import NetworkError
from pressread.service import ArticleService
from pressread.store import Store

SENTENCE = (
    "The kelp forest along the northern coast has expanded by nearly a third "
    "over the past decade, according to a survey released this week. "
)


def make_page(title: str, paragraphs: int = 3) -> str:
    body = "".join(f"<p>{SENTENCE * 3}</p>" for _ in range(paragraphs))
    return (
        "<html><head><title>{0}</title>"
        '<meta property="og:title" content="{0}"></head>'
        "<body><article><h1>{0}</h1>{1}</article></body></html>"
    ).format(title, body)


def make_distinct_page(title: str, paragraphs: int = 6) -> str:
    """每段内容不同，避免被正文抽取的「重复段落去重」合并成一段。"""
    body = "".join(
        f"<p>Part {i + 1}. {SENTENCE * 3}</p>" for i in range(paragraphs)
    )
    return (
        "<html><head><title>{0}</title>"
        '<meta property="og:title" content="{0}"></head>'
        "<body><article><h1>{0}</h1>{1}</article></body></html>"
    ).format(title, body)


class FakeConfig:
    """够用的配置替身：只实现 service 真正读的那几个方法。"""

    def __init__(self, sources: dict, fetch: dict | None = None, practice: dict | None = None):
        self.data = {
            "sources": sources,
            "fetch": fetch or {},
            "cache": {},
            "practice": practice or {},
            "ui": {"show_demo_on_failure": True},
        }

    @property
    def enabled_sources(self) -> list[str]:
        return [sid for sid, on in self.data["sources"].items() if on]

    @property
    def paragraphs_per_unit(self) -> int:
        return int(self.data.get("practice", {}).get("paragraphs_per_unit", 1))

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def fetch_number(self, key, default, *, low, high):
        # 替身不做上下限裁剪，方便测试用极短预算
        try:
            return float(self.data.get("fetch", {}).get(key, default))
        except (TypeError, ValueError):
            return default


class FakeHttp:
    """按 URL 编排响应：字符串=页面，Exception=失败，数字=延迟秒数。"""

    def __init__(self, pages: dict, delays: dict | None = None):
        self.pages = pages
        self.delays = delays or {}
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def get_text(self, url: str, **kwargs) -> str:
        with self._lock:
            self.calls.append(url)
        delay = self.delays.get(url, 0)
        if delay:
            time.sleep(delay)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            raise NetworkError("服务器返回 404", url=url, status=404)
        return page


def item(source_id: str, slug: str, title: str = "Kelp") -> FeedItem:
    return FeedItem(
        source_id=source_id,
        title=title,
        url=f"https://{source_id}.test/{slug}",
        summary="",
        category=SOURCE_MAP[source_id].category if source_id in SOURCE_MAP else "",
    )


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "pressread.db")
        self.today = "2026-10-30"

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def build(self, pages, delays=None, fetch=None, practice=None) -> tuple[ArticleService, FakeHttp]:
        cfg = FakeConfig(
            {"wired": True, "npr": True, "quanta": True},
            fetch or {"race_width": 3, "article_timeout": 5, "race_budget_seconds": 4},
            practice,
        )
        http = FakeHttp(pages, delays)
        svc = ArticleService(cfg, self.store, http)
        return svc, http


class RaceTests(ServiceTestCase):
    def test_first_success_wins_without_waiting_slowest(self):
        """三个候选并发开始，最快返回的那篇胜出，总耗时不等于最慢者。"""
        a, b, c = item("wired", "a"), item("npr", "b"), item("quanta", "c")
        pages = {
            a.url: make_page("A"),
            b.url: make_page("B"),
            c.url: make_page("C"),
        }
        delays = {a.url: 0.05, b.url: 0.9, c.url: 1.4}
        svc, http = self.build(pages, delays)

        started = time.time()
        unit, errors = svc._race([a, b, c], self.today)
        elapsed = time.time() - started

        self.assertIsNotNone(unit, f"竞速没有选出文章：{errors}")
        self.assertEqual(unit.article.url, a.url)
        self.assertLess(elapsed, 0.6, "主线程等到了慢请求，说明没有先到先用")
        time.sleep(0.1)
        self.assertEqual(len(http.calls), 3, "三个候选应当同时发起")

    def test_race_starts_all_candidates_concurrently(self):
        """并发度：三个请求应在同一时刻被打出，而不是依次串行。"""
        items = [item("wired", "a"), item("npr", "b"), item("quanta", "c")]
        pages = {i.url: make_page(i.source_id) for i in items}
        delays = {i.url: 0.25 for i in items}
        svc, http = self.build(pages, delays)

        started = time.time()
        unit, _ = svc._race(items, self.today)
        elapsed = time.time() - started

        self.assertIsNotNone(unit)
        self.assertLess(elapsed, 0.6, f"串行执行会超过 0.75 秒，实际 {elapsed:.2f}")
        self.assertEqual(len(http.calls), 3)

    def test_empty_body_is_skipped(self):
        """最快返回但抽不出正文的候选要被跳过，等下一个成功者。"""
        bad, good = item("wired", "bad"), item("npr", "good")
        pages = {
            bad.url: "<html><body><nav><a href='/x'>menu</a></nav></body></html>",
            good.url: make_page("Good"),
        }
        svc, _ = self.build(pages, {bad.url: 0.02, good.url: 0.3})

        unit, _ = svc._race([bad, good], self.today)
        self.assertIsNotNone(unit)
        self.assertEqual(unit.article.url, good.url)

    def test_network_error_is_skipped(self):
        broken, good = item("wired", "e"), item("npr", "g")
        pages = {broken.url: NetworkError("连接超时", url=broken.url), good.url: make_page("G")}
        svc, _ = self.build(pages, {broken.url: 0.02, good.url: 0.15})

        unit, errors = svc._race([broken, good], self.today)
        self.assertIsNotNone(unit)
        self.assertEqual(unit.article.url, good.url)
        self.assertTrue(any("超时" in e for e in errors))

    def test_budget_exhausted_returns_none(self):
        """全部卡住时按整批预算退出，不能无限等。"""
        items = [item("wired", "a"), item("npr", "b")]
        pages = {i.url: make_page(i.source_id) for i in items}
        svc, _ = self.build(
            pages,
            {i.url: 5 for i in items},
            {"race_width": 2, "article_timeout": 30, "race_budget_seconds": 0.4},
        )

        started = time.time()
        unit, errors = svc._race(items, self.today)
        elapsed = time.time() - started

        self.assertIsNone(unit)
        self.assertLess(elapsed, 1.5, "超时兜底没有生效")
        self.assertTrue(any("没有源返回可用正文" in e for e in errors))


class PrefetchWriteTests(ServiceTestCase):
    def test_losers_are_cached(self):
        """未被选中的候选抓完后要写进预取缓存。"""
        a, b = item("wired", "a"), item("npr", "b")
        pages = {a.url: make_page("A"), b.url: make_page("B")}
        svc, _ = self.build(pages, {a.url: 0.05, b.url: 0.2})

        unit, _ = svc._race([a, b], self.today)
        self.assertEqual(unit.article.url, a.url)

        # 等落选的 b 线程写完
        deadline = time.time() + 3
        urls = []
        while time.time() < deadline:
            urls = [r["url"] for r in self.store.prefetch_recent(3600)]
            if b.url in urls:
                break
            time.sleep(0.05)
        self.assertIn(b.url, urls, "落选文章没有进入预取缓存")

    def test_winner_is_saved_as_article(self):
        a = item("wired", "a")
        svc, _ = self.build({a.url: make_page("A")})
        unit, _ = svc._race([a], self.today)
        self.assertIsNotNone(unit)
        self.assertTrue(self.store.article_known(a.url))


class PrefetchReadTests(ServiceTestCase):
    def seed_prefetch(self, url: str, title: str = "Cached piece") -> None:
        self.store.save_prefetch(
            {
                "url": url,
                "source_id": "wired",
                "source_name": "WIRED",
                "category": "科技",
                "title": title,
                "paragraphs": [SENTENCE * 3, SENTENCE * 3],
                "author": "",
                "published": "",
                "word_count": 168,
                "fetched_at": time.time(),
                "offline": False,
            }
        )

    def test_next_unit_hits_cache_without_network(self):
        """预取缓存命中时不应发起任何请求。"""
        url = "https://wired.test/cached"
        self.seed_prefetch(url)
        svc, http = self.build({})  # 任何请求都会 404

        unit = svc.next_unit(today=self.today)

        self.assertEqual(unit.article.url, url)
        self.assertEqual(http.calls, [], "缓存命中时不该联网")
        self.assertTrue(self.store.article_known(url), "命中后应转入正式文章库")

    def test_expired_cache_is_ignored(self):
        url = "https://wired.test/stale"
        self.store.save_prefetch(
            {
                "url": url,
                "source_id": "wired",
                "source_name": "WIRED",
                "category": "科技",
                "title": "stale",
                "paragraphs": [SENTENCE * 3],
                "word_count": 84,
                "fetched_at": time.time() - 7200,
            }
        )
        svc, _ = self.build({})
        unit = svc.next_unit(today=self.today)
        self.assertTrue(unit.offline, "过期缓存不该被使用，应走离线兜底")
        self.assertEqual(self.store.prefetch_stats()["count"], 0)

    def test_purge_then_cache_miss(self):
        url = "https://wired.test/cached"
        self.seed_prefetch(url)
        self.store.prefetch_purge()
        svc, _ = self.build({})
        unit = svc.next_unit(today=self.today)
        self.assertTrue(unit.offline)


class TimeoutFallbackTests(ServiceTestCase):
    def test_all_sources_down_falls_back_to_demo(self):
        """所有源都失败时退回离线示例，不抛异常。"""
        svc, http = self.build(
            {}, None, {"race_width": 2, "race_budget_seconds": 1.5}
        )
        unit = svc.next_unit(today=self.today)
        self.assertTrue(unit.offline)
        self.assertEqual(unit.article.url, "pressread://demo")
        self.assertTrue(svc.last_errors())

    def test_feed_timeout_is_tolerated(self):
        """文章列表阶段有源卡住时，先用已返回的源继续，不整体失败。"""
        cfg = FakeConfig(
            {"wired": True, "npr": True},
            {"race_width": 1, "article_timeout": 5,
             "race_budget_seconds": 3, "feed_budget_seconds": 0.8},
        )

        class SlowFeedHttp(FakeHttp):
            def get_text(self, url, **kwargs):
                with self._lock:
                    self.calls.append(url)
                if "wired" in url:
                    time.sleep(3)  # 这一源拖后腿
                    return ""
                if url == SOURCE_MAP["npr"].feed_url:
                    return (
                        '<?xml version="1.0"?><rss version="2.0"><channel>'
                        "<item><title>NPR piece</title>"
                        "<link>https://npr.test/story</link></item>"
                        "</channel></rss>"
                    )
                return make_page("NPR piece")

        http = SlowFeedHttp({})
        svc = ArticleService(cfg, self.store, http)

        started = time.time()
        feeds, errors = svc.fetch_all_feeds(enough=1)
        elapsed = time.time() - started

        self.assertIn("npr", feeds)
        self.assertLess(elapsed, 2.0, "凑够源数就该先走，不该等卡住的源")


class UnitGroupTests(ServiceTestCase):
    """「换一段」按索引取下一段；「一次显示 N 段」按设置合并连续段。"""

    def article(self, paragraphs: int = 6, url: str = "https://wired.test/multi"):
        from pressread.article import extract_article

        return extract_article(
            make_distinct_page("Kelp", paragraphs),
            url=url,
            source_id="wired",
            source_name="WIRED",
            category="科技",
        )

    def test_unit_at_returns_the_indexed_paragraph(self):
        """换一段：按起始序号取到对应那一段，内容与切分结果一致。"""
        art = self.article(5)
        units = art.practice_units()
        self.assertGreaterEqual(len(units), 4)
        svc, _ = self.build({})

        unit = svc.unit_at(art, units[2]["index"])
        self.assertIsNotNone(unit, "unit_at 取不到已存在的段落，换一段会直接换文章")
        self.assertEqual(unit.index, units[2]["index"])
        self.assertEqual(unit.span, 1)
        self.assertEqual(unit.text, units[2]["text"])
        self.assertEqual(unit.sentences, units[2]["sentences"])

    def test_unit_at_out_of_range_returns_none(self):
        """读到末尾时返回 None，由界面决定换文章。"""
        art = self.article(3)
        svc, _ = self.build({})
        self.assertIsNone(svc.unit_at(art, 999))

    def test_default_group_is_single_paragraph(self):
        """默认设置下与旧行为一致：一次一段。"""
        art = self.article(5)
        svc, _ = self.build({})
        self.assertEqual(svc.group_size(), 1)
        unit = svc.unit_at(art, 0)
        self.assertEqual(unit.span, 1)

    def test_group_size_merges_consecutive_paragraphs(self):
        """一次显示 3 段：正文与句子按顺序拼起来。"""
        art = self.article(6)
        units = art.practice_units()
        svc, _ = self.build({}, practice={"paragraphs_per_unit": 3})

        unit = svc.unit_at(art, units[0]["index"])
        self.assertEqual(unit.index, units[0]["index"])
        self.assertEqual(unit.span, 3)
        expected = [s for u in units[:3] for s in u["sentences"]]
        self.assertEqual(unit.sentences, expected)
        for u in units[:3]:
            self.assertIn(u["text"], unit.text)

    def test_para_starts_mark_each_paragraph(self):
        """合并后每段第一句的下标要跟着走，阅读页靠它分段缩进。"""
        art = self.article(6)
        units = art.practice_units()
        svc, _ = self.build({}, practice={"paragraphs_per_unit": 3})
        unit = svc.unit_at(art, units[0]["index"])
        first = len(units[0]["sentences"])
        second = first + len(units[1]["sentences"])
        self.assertEqual(unit.para_starts, [0, first, second])
        self.assertEqual(len(unit.para_starts), unit.span)

    def test_single_paragraph_has_one_start(self):
        art = self.article(4)
        svc, _ = self.build({})
        unit = svc.unit_at(art, 0)
        self.assertEqual(unit.para_starts, [0])

    def test_short_article_takes_everything_left(self):
        """文章不够 N 段时取剩下的全部，span 按实际段数记。"""
        art = self.article(4)
        units = art.practice_units()
        svc, _ = self.build({}, practice={"paragraphs_per_unit": 3})

        last = len(units) - 1
        unit = svc.unit_at(art, units[last]["index"])
        self.assertEqual(unit.index, units[last]["index"])
        self.assertEqual(unit.span, 1, "最后一段之后没有内容，span 应为 1")

        middle = svc.unit_at(art, units[last - 1]["index"])
        self.assertEqual(middle.span, 2, "倒数第二段起只剩两段")

    def test_article_shorter_than_group_uses_all(self):
        """整篇只有两段、设置要三段：能取多少取多少。"""
        art = self.article(2)
        units = art.practice_units()
        svc, _ = self.build({}, practice={"paragraphs_per_unit": 3})
        unit = svc.unit_at(art, units[0]["index"])
        self.assertEqual(unit.span, len(units))

    def test_next_unit_starts_where_group_fits(self):
        """首次抽段要留够 N 段，不能从倒数第二段起只给两段。"""
        art = self.article(8)
        units = art.practice_units()
        svc, _ = self.build({}, practice={"paragraphs_per_unit": 3})

        unit = svc._unit_from_article(art, self.today)
        self.assertIsNotNone(unit)
        self.assertEqual(unit.span, 3)
        self.assertLessEqual(unit.index + unit.span, len(units))

    def test_demo_unit_advances_and_merges(self):
        """离线示例也支持换段与多段合并（它自带逐句译文，必须对齐）。"""
        svc, _ = self.build({})
        first = svc.demo_unit_at(0)
        second = svc.demo_unit_at(1)
        self.assertEqual(first.index, 0)
        self.assertEqual(second.index, 1)
        self.assertNotEqual(first.text, second.text)
        self.assertEqual(len(first.sentences), len(first.bundled_translations))

        svc2, _ = self.build({}, practice={"paragraphs_per_unit": 2})
        merged = svc2.demo_unit_at(0)
        self.assertEqual(merged.span, 2)
        self.assertEqual(len(merged.sentences), len(merged.bundled_translations))


class ProgressCountTests(ServiceTestCase):
    """一次练习覆盖多段时，每日进度按实际段数累计。"""

    def test_para_count_sums_into_daily_progress(self):
        pid = self.store.ensure_practice(
            self.today, "aid", 0, "text", para_count=3
        )["id"]
        self.store.complete_practice(pid, "译文", [])
        self.assertEqual(self.store.day_done_count(self.today), 3)
        self.assertEqual(self.store.stats()["paragraphs"], 3)

    def test_single_paragraph_still_counts_one(self):
        pid = self.store.ensure_practice(self.today, "aid", 0, "text")["id"]
        self.store.complete_practice(pid, "译文", [])
        self.assertEqual(self.store.day_done_count(self.today), 1)

    def test_reopen_updates_span_only_while_active(self):
        """同一段被重新抽到、段数变了：未完成时更新，完成后不动。"""
        record = self.store.ensure_practice(self.today, "aid", 0, "old", para_count=1)
        again = self.store.ensure_practice(
            self.today, "aid", 0, "new", para_count=2
        )
        self.assertEqual(again["id"], record["id"])
        self.assertEqual(again["para_count"], 2)
        self.assertEqual(again["text"], "new")

        self.store.complete_practice(record["id"], "译文", [])
        after = self.store.ensure_practice(
            self.today, "aid", 0, "changed", para_count=4
        )
        self.assertEqual(after["para_count"], 2, "已完成记录不该被后来的设置改写")
        self.assertEqual(after["text"], "new")

    def test_old_database_gains_para_count_column(self):
        """老库（没有 para_count）打开时自动补列，历史记录按 1 段计。"""
        import sqlite3

        old_path = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(str(old_path))
        conn.execute(
            """CREATE TABLE practice (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   day TEXT NOT NULL, article_id TEXT NOT NULL,
                   para_index INTEGER NOT NULL, source_name TEXT DEFAULT '',
                   title TEXT DEFAULT '', text TEXT NOT NULL,
                   my_translation TEXT DEFAULT '', refs TEXT DEFAULT '[]',
                   status TEXT NOT NULL DEFAULT 'active',
                   created_at REAL NOT NULL, updated_at REAL NOT NULL,
                   completed_at REAL DEFAULT 0,
                   UNIQUE(day, article_id, para_index))"""
        )
        conn.execute(
            "INSERT INTO practice(day, article_id, para_index, text, status, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (self.today, "old", 0, "legacy text", "done", 1.0, 1.0),
        )
        conn.commit()
        conn.close()

        store = Store(old_path)
        try:
            self.assertEqual(store.day_done_count(self.today), 1)
            row = store.find_practice(self.today, "old", 0)
            self.assertEqual(row["para_count"], 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
