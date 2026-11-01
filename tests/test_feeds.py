"""RSS/Atom 解析与条目筛选。"""

import unittest

from pressread.config import DEFAULT_SOURCES
from pressread.feeds import (
    CATEGORY_ORDER,
    PRESETS,
    SOURCES,
    SOURCE_MAP,
    FeedItem,
    Source,
    _parse_date,
    filter_items,
    filter_sources,
    parse_feed,
    parse_listing,
)

LISTING = """<html><body>
<header><a href="/subscribe">Subscribe to National Geographic</a></header>
<a aria-label="A New Way to See Osaka&#x27;s Ancient Tombs"
   href="/travel/article/osaka-tombs">card</a>
<a href="https://www.nationalgeographic.com/animals/article/rhinos"
   data-track-story_title="What Does It Take to Move 2,000 Rhinos?"></a>
<a href="/animals/article/"><span>Read more</span></a>
<a href="/podcast/the-overheard">The Overheard podcast episode</a>
<a href="#top">Back to top</a>
<a href="/science/article/november-2026-night-sky-events">2026 Night Sky Events</a>
</body></html>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>WIRED</title>
    <item>
      <title>Inside the Kelp Boom</title>
      <link>https://www.wired.com/story/kelp-boom/</link>
      <description>&lt;p&gt;A short &lt;b&gt;summary&lt;/b&gt; of the piece.&lt;/p&gt;</description>
      <content:encoded><![CDATA[<p>Full body text here.</p>]]></content:encoded>
      <dc:date>2026-09-14T07:30:00Z</dc:date>
    </item>
    <item>
      <title>Watch: The Ocean in 4K</title>
      <link>https://www.wired.com/video/ocean/</link>
      <description>A video, not an article.</description>
    </item>
    <item>
      <title>Kelp&#8217;s Long Winter</title>
      <link>https://www.wired.com/story/kelp-winter/</link>
      <pubDate>Mon, 15 Sep 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>No link here</title>
      <description>Dropped because the item has no URL.</description>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Quanta Magazine</title>
  <link href="https://www.quantamagazine.org/feed/" rel="self"/>
  <entry>
    <title>A Proof That Should Not Work</title>
    <link rel="self" href="https://www.quantamagazine.org/feed/proof"/>
    <link rel="alternate" href="https://www.quantamagazine.org/a-proof-that-should-not-work-20260914/"/>
    <summary>Mathematicians found a shortcut.</summary>
    <published>2026-09-14T12:00:00Z</published>
  </entry>
  <entry>
    <title>Podcast: The Joy of Sets</title>
    <link href="https://www.quantamagazine.org/podcast/joy-of-sets/"/>
    <updated>2026-09-13T08:00:00Z</updated>
  </entry>
  <entry>
    <title>The Joy of Finite Sets</title>
    <link href="https://www.quantamagazine.org/the-joy-of-finite-sets-20260912/"/>
    <updated>2026-09-12T08:00:00Z</updated>
  </entry>
</feed>
"""


class RssTests(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE_MAP["wired"]
        self.items = parse_feed(RSS, self.source)

    def test_video_and_linkless_items_dropped(self):
        # 视频条目、没有链接的条目在解析阶段就被过滤
        self.assertEqual(len(self.items), 2)
        urls = [item.url for item in self.items]
        self.assertNotIn("https://www.wired.com/video/ocean/", urls)

    def test_fields(self):
        first = self.items[0]
        self.assertEqual(first.title, "Inside the Kelp Boom")
        self.assertEqual(first.url, "https://www.wired.com/story/kelp-boom/")
        self.assertEqual(first.source_id, "wired")
        self.assertEqual(first.category, "科技")
        self.assertEqual(first.published, "2026-09-14")
        self.assertNotIn("<p>", first.summary)

    def test_entity_decoded(self):
        titles = [item.title for item in self.items]
        self.assertIn("Kelp’s Long Winter", titles)

    def test_source_label(self):
        self.assertEqual(self.items[0].source_label, "WIRED")


class AtomTests(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE_MAP["quanta"]
        self.items = parse_feed(ATOM, self.source)

    def test_prefers_alternate_link(self):
        self.assertEqual(
            self.items[0].url,
            "https://www.quantamagazine.org/a-proof-that-should-not-work-20260914/",
        )

    def test_published_from_atom(self):
        self.assertEqual(self.items[0].published, "2026-09-14")

    def test_updated_fallback_and_podcast_dropped(self):
        self.assertEqual(len(self.items), 2)
        self.assertEqual(self.items[1].published, "2026-09-12")
        self.assertNotIn("podcast", " ".join(item.url for item in self.items))


class FilterTests(unittest.TestCase):
    def _item(self, title: str, summary: str = "") -> FeedItem:
        return FeedItem(source_id="wired", title=title, url="https://example.com/x", summary=summary)

    def test_drops_short_and_overlong_titles(self):
        items = [
            self._item("Kelp"),  # 太短，像是栏目名
            self._item("A perfectly reasonable headline about kelp"),
            self._item("K" * 200),  # 太长，多半是拼接错误
        ]
        kept = filter_items(items)
        self.assertEqual([item.title for item in kept], ["A perfectly reasonable headline about kelp"])

    def test_drops_huge_summary(self):
        items = [self._item("A perfectly reasonable headline", "x" * 20001)]
        self.assertEqual(filter_items(items), [])

    def test_parse_skips_subscribe_urls(self):
        xml = RSS.replace(
            "https://www.wired.com/story/kelp-winter/",
            "https://www.wired.com/newsletters/subscribe/",
        )
        urls = [item.url for item in parse_feed(xml, SOURCE_MAP["wired"])]
        self.assertNotIn("https://www.wired.com/newsletters/subscribe/", urls)

    def test_items_roundtrip(self):
        item = parse_feed(RSS, SOURCE_MAP["wired"])[0]
        again = FeedItem.from_dict(item.to_dict())
        self.assertEqual(again.to_dict(), item.to_dict())


class DateTests(unittest.TestCase):
    def test_rfc822(self):
        self.assertEqual(_parse_date("Mon, 15 Sep 2026 10:00:00 GMT"), "2026-09-15")

    def test_iso(self):
        self.assertEqual(_parse_date("2026-09-14T07:30:00Z"), "2026-09-14")

    def test_garbage(self):
        self.assertEqual(_parse_date("last tuesday"), "")
        self.assertEqual(_parse_date(""), "")


class ListingTests(unittest.TestCase):
    """没有 RSS 的站点（国家地理）走列表页解析。"""

    def setUp(self):
        self.source = SOURCE_MAP["nationalgeographic"]
        self.items = parse_listing(LISTING, self.source)

    def test_source_declares_html_kind(self):
        self.assertEqual(self.source.kind, "html")
        self.assertTrue(self.source.link_pattern)

    def test_relative_links_are_absolute(self):
        urls = [item.url for item in self.items]
        self.assertIn(
            "https://www.nationalgeographic.com/travel/article/osaka-tombs", urls
        )
        self.assertTrue(all(url.startswith("https://") for url in urls))

    def test_title_from_aria_label_and_track_attr(self):
        titles = [item.title for item in self.items]
        self.assertIn("A New Way to See Osaka's Ancient Tombs", titles)
        self.assertIn("What Does It Take to Move 2,000 Rhinos?", titles)

    def test_skips_nav_podcast_and_empty(self):
        urls = " ".join(item.url for item in self.items)
        self.assertNotIn("/subscribe", urls)
        self.assertNotIn("/podcast/", urls)
        self.assertNotIn("#top", urls)
        # 空标题的卡片链接不该混进来
        self.assertNotIn("https://www.nationalgeographic.com/animals/article/", urls.split())

    def test_category_inherited(self):
        self.assertTrue(all(item.category == "科学" for item in self.items))
        self.assertTrue(all(item.source_id == "nationalgeographic" for item in self.items))


class SourceTests(unittest.TestCase):
    def test_unique_ids_and_parens_label(self):
        ids = [s.id for s in SOURCES]
        self.assertEqual(len(ids), len(set(ids)))
        source: Source = SOURCE_MAP["npr"]
        self.assertIn("（", source.label)

    def test_every_source_is_complete(self):
        for source in SOURCES:
            self.assertTrue(source.feed_url.startswith("http"), source.id)
            self.assertTrue(source.homepage.startswith("http"), source.id)
            self.assertTrue(source.blurb, source.id)
            self.assertIn(source.category, CATEGORY_ORDER, source.id)
            if source.kind == "html":
                self.assertTrue(source.link_pattern, source.id)

    def test_default_flags_cover_all_sources(self):
        """新增来源必须同时登记默认开关，否则老配置里读到 False 也无人察觉。"""
        missing = [s.id for s in SOURCES if s.id not in DEFAULT_SOURCES]
        self.assertEqual(missing, [])
        self.assertTrue(any(DEFAULT_SOURCES.get(s.id) for s in SOURCES))

    def test_narrative_categories_exist(self):
        """B 篇向的四类必须真的落到来源上，不能只是空分类。"""
        for category in ("随笔", "教育", "人物", "成长"):
            group = [s for s in SOURCES if s.category == category]
            self.assertGreaterEqual(len(group), 2, category)

    def test_presets_reference_real_sources(self):
        self.assertGreaterEqual(len(PRESETS), 3)
        for key, label, hint, ids in PRESETS:
            self.assertTrue(key and label and hint)
            self.assertTrue(ids, key)
            self.assertEqual(len(ids), len(set(ids)), key)
            unknown = [sid for sid in ids if sid not in SOURCE_MAP]
            self.assertEqual(unknown, [], key)

    def test_balanced_preset_matches_defaults(self):
        """「均衡默认」必须和出厂默认一致，否则点一下就跟首次安装不同。"""
        balanced = next(p[3] for p in PRESETS if p[0] == "balanced")
        expected = {s.id for s in SOURCES if DEFAULT_SOURCES.get(s.id)}
        self.assertEqual(set(balanced), expected)

    def test_broken_xml_raises(self):
        with self.assertRaises(ValueError):
            parse_feed("not xml at all", SOURCE_MAP["wired"])

    def test_feed_with_leading_blank_lines(self):
        """Guideposts / eSchool News 的 feed 在 <?xml ?> 前有空行，不该解析失败。"""
        self.assertEqual(len(parse_feed("\n\n  " + RSS, SOURCE_MAP["wired"])), 2)


class SourceFilterTests(unittest.TestCase):
    """设置页的来源搜索与分类筛选（纯函数，便于回归）。"""

    def test_category_filter(self):
        items = filter_sources(category="教育")
        self.assertTrue(items)
        self.assertTrue(all(s.category == "教育" for s in items))

    def test_all_category_is_no_filter(self):
        self.assertEqual(len(filter_sources(category="全部")), len(SOURCES))
        self.assertEqual(len(filter_sources(category="")), len(SOURCES))

    def test_query_matches_english_and_chinese(self):
        by_en = filter_sources(query="american scholar")
        self.assertIn("americanscholar", [s.id for s in by_en])
        by_zh = filter_sources(query="教育周刊")
        self.assertIn("edweek", [s.id for s in by_zh])

    def test_query_matches_blurb_keywords(self):
        hits = [s.id for s in filter_sources(query="第一人称")]
        self.assertIn("narratively", hits)
        self.assertIn("motherly", hits)
        self.assertNotIn("wired", hits)

    def test_multiple_terms_are_anded(self):
        both = filter_sources(query="教育 周刊")
        ids = [s.id for s in both]
        self.assertIn("edweek", ids)
        mismatch = [s.id for s in filter_sources(query="教育 天文学")]
        self.assertEqual(mismatch, [])

    def test_bare_ampersand_feed_is_repaired(self):
        """Literary Hub 的 feed 里有裸 &，修一遍后应该能正常解析。"""
        source = next(s for s in SOURCES if s.id == "lithub")
        xml = (
            '<?xml version="1.0"?><rss><channel><item>'
            "<title>Arts & Letters</title>"
            "<link>https://lithub.com/arts-and-letters/</link>"
            "</item></channel></rss>"
        )
        items = parse_feed(xml, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Arts & Letters")

    def test_query_and_category_combine(self):
        items = filter_sources(query="the", category="人物")
        self.assertTrue(all(s.category == "人物" for s in items))
        self.assertTrue(any("The Big Issue" == s.name for s in items))


if __name__ == "__main__":
    unittest.main()
