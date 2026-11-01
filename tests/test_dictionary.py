"""词典解析与查词服务：剑桥英汉、牛津英英、备用词典、离线词表、缓存。"""

import unittest
from pathlib import Path

from pressread.config import Config
from pressread.dictionary import (
    DictEntry,
    DictionaryService,
    LocalGlossary,
    Sense,
    WordNotFound,
    parse_cambridge_html,
    parse_fallback_api,
    parse_oxford_api,
    parse_oxford_html,
)
from pressread.fetch import NetworkError
from pressread.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
OXFORD_URL = "https://www.oxfordlearnersdictionaries.com/definition/english/run"
CAMBRIDGE_URL = (
    "https://dictionary.cambridge.org/dictionary/english-chinese-simplified/run"
)

API_PAYLOAD = {
    "results": [
        {
            "word": "kelp",
            "lexicalEntries": [
                {
                    "lexicalCategory": {"text": "noun"},
                    "pronunciations": [
                        {"phoneticSpelling": "kelp", "dialects": ["British English"]},
                        {"phoneticSpelling": "kelp", "dialects": ["American English"]},
                    ],
                    "entries": [
                        {
                            "etymologies": ["late 17th century: of unknown origin."],
                            "senses": [
                                {
                                    "definitions": ["large brown seaweed that grows in cold water"],
                                    "examples": [{"text": "beds of kelp along the shore"}],
                                    "synonyms": [{"text": "seaweed"}],
                                },
                                {
                                    "definitions": ["the ash of kelp, used in glassmaking"],
                                    "subsenses": [
                                        {"definitions": ["a source of iodine"]}
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}


class CambridgeWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (FIXTURES / "cambridge_run.html").read_text(encoding="utf-8")
        cls.entry = parse_cambridge_html(html, "run", CAMBRIDGE_URL)

    def test_identity_is_chinese(self):
        self.assertEqual(self.entry.provider, "cambridge")
        self.assertEqual(self.entry.lang, "zh")
        self.assertTrue(self.entry.is_chinese)
        self.assertIn("剑桥", self.entry.source_label)

    def test_phonetics(self):
        self.assertEqual(self.entry.phonetics.get("uk"), "/rʌn/")
        self.assertEqual(self.entry.phonetics.get("us"), "/rʌn/")

    def test_senses_use_chinese_with_english_gloss(self):
        self.assertEqual(len(self.entry.senses), 3)  # 习语块被排除
        first = self.entry.senses[0]
        self.assertEqual(first.pos, "verb")
        self.assertEqual(first.definition, "跑，奔跑")
        self.assertTrue(first.gloss.startswith("to move along"))
        self.assertEqual(first.labels, ["figurative"])

    def test_example_pairs_english_and_chinese(self):
        examples = self.entry.senses[0].examples
        self.assertEqual(len(examples), 1)
        self.assertIn("The children had to run", examples[0])
        self.assertIn("孩子们", examples[0])

    def test_grouped_by_pos(self):
        groups = self.entry.grouped()
        self.assertEqual([pos for pos, _ in groups], ["verb", "noun"])
        self.assertEqual(groups[1][0], "noun")
        self.assertEqual(groups[1][1][0].labels, ["informal"])

    def test_missing_entry_raises(self):
        with self.assertRaises(WordNotFound):
            parse_cambridge_html("<html><body>no entry</body></html>", "zzz", CAMBRIDGE_URL)


class OxfordWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (FIXTURES / "oxford_run.html").read_text(encoding="utf-8")
        cls.entry = parse_oxford_html(html, "run", OXFORD_URL)

    def test_identity(self):
        self.assertEqual(self.entry.word, "run")
        self.assertEqual(self.entry.provider, "oxford_web")
        self.assertIn("牛津", self.entry.source_label)

    def test_phonetics_not_from_related_words(self):
        self.assertEqual(self.entry.phonetics.get("uk"), "/rʌn/")
        self.assertEqual(self.entry.phonetics.get("us"), "/rʌn/")

    def test_all_senses_parsed(self):
        self.assertEqual(len(self.entry.senses), 35)
        first = self.entry.senses[0]
        self.assertEqual(first.pos, "verb")
        self.assertTrue(first.definition.startswith("to move using your legs"))

    def test_senses_have_examples(self):
        self.assertTrue(any(sense.examples for sense in self.entry.senses))

    def test_idioms_carry_definitions(self):
        self.assertEqual(len(self.entry.idioms), 3)
        self.assertEqual(self.entry.idioms[0]["phrase"], "come running")
        for idiom in self.entry.idioms:
            self.assertTrue(idiom["phrase"])
            self.assertTrue(idiom["definition"])

    def test_word_origin(self):
        self.assertIn("Old English", self.entry.origin)

    def test_grouped_by_pos(self):
        groups = self.entry.grouped()
        self.assertTrue(groups)
        self.assertEqual(sum(len(items) for _pos, items in groups), len(self.entry.senses))

    def test_no_entry_raises(self):
        with self.assertRaises(WordNotFound):
            parse_oxford_html("<html><body>No entry found</body></html>", "zzz", OXFORD_URL)


class OxfordApiTests(unittest.TestCase):
    def test_parse(self):
        entry = parse_oxford_api(API_PAYLOAD, "kelp", "https://od-api/x")
        self.assertEqual(entry.provider, "oxford_api")
        self.assertEqual(len(entry.senses), 3)  # 含 subsense
        self.assertEqual(entry.senses[0].pos, "noun")
        self.assertIn("brown seaweed", entry.senses[0].definition)
        self.assertEqual(entry.senses[0].examples, ["beds of kelp along the shore"])
        self.assertEqual(entry.senses[0].synonyms, ["seaweed"])
        self.assertEqual(entry.phonetics.get("uk"), "/kelp/")
        self.assertEqual(entry.phonetics.get("us"), "/kelp/")
        self.assertIn("17th century", entry.origin)

    def test_empty_results(self):
        with self.assertRaises(WordNotFound):
            parse_oxford_api({"results": []}, "kelp", "https://od-api/x")


class FallbackTests(unittest.TestCase):
    def test_parse(self):
        payload = [
            {
                "word": "kelp",
                "origin": "unknown",
                "phonetics": [{"text": "/kelp/"}],
                "meanings": [
                    {
                        "partOfSpeech": "noun",
                        "definitions": [
                            {"definition": "brown seaweed", "example": "kelp forest"}
                        ],
                    }
                ],
            }
        ]
        entry = parse_fallback_api(payload, "kelp")
        self.assertEqual(entry.provider, "dictionaryapi")
        self.assertIn("非牛津", entry.source_label)
        self.assertEqual(entry.senses[0].examples, ["kelp forest"])

    def test_bad_payload(self):
        with self.assertRaises(WordNotFound):
            parse_fallback_api({"title": "No Definitions Found"}, "kelp")


class GlossaryTests(unittest.TestCase):
    def test_known_word(self):
        entry = LocalGlossary().lookup("twilight")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.provider, "local")
        self.assertTrue(any(sense.definition for sense in entry.senses))

    def test_case_insensitive(self):
        self.assertIsNotNone(LocalGlossary().lookup("Twilight"))

    def test_unknown_word(self):
        self.assertIsNone(LocalGlossary().lookup("floccinaucinihilipilification"))


class EntryRoundTripTests(unittest.TestCase):
    def test_dict_roundtrip(self):
        entry = DictEntry(
            word="kelp",
            provider="oxford_web",
            source_label="牛津学习者词典",
            phonetics={"uk": "/kelp/"},
            senses=[Sense(pos="noun", definition="brown seaweed", examples=["kelp forest"])],
            idioms=[{"phrase": "kelp forest", "definition": "a forest of kelp"}],
            origin="unknown",
        )
        again = DictEntry.from_dict(entry.to_dict())
        self.assertEqual(again.word, entry.word)
        self.assertEqual(again.senses[0].definition, "brown seaweed")
        self.assertEqual(again.idioms, entry.idioms)
        self.assertFalse(again.cached)


class FakeHttp:
    def __init__(self, html="", error=None):
        self.html = html
        self.error = error
        self.calls = 0

    def get_text(self, url, headers=None, timeout=None, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.html

    def get_json(self, url, headers=None, timeout=None, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return API_PAYLOAD


class ServiceTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.html = (FIXTURES / "oxford_run.html").read_text(encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _config(self, **oxford):
        config = Config(data=Config.load(Path(self.tmp.name) / "c.json").data,
                        path=Path(self.tmp.name) / "c.json")
        config.data["oxford"].update({"app_id": "", "app_key": ""})
        config.data["oxford"].update(oxford)
        return config

    def test_second_lookup_comes_from_cache(self):
        http = FakeHttp(self.html)
        service = DictionaryService(http, self.store, self._config(allow_web=True))
        first = service.lookup("run", lang="en")
        self.assertFalse(first.cached)
        second = service.lookup("run", lang="en")  # 进程内记忆，不碰网络也不碰库
        self.assertTrue(second.cached)
        self.assertEqual(http.calls, 1)
        self.assertEqual(len(second.senses), 35)

        # 换一个进程（新的 service）时从 SQLite 缓存读，命中计数 +1
        fresh = DictionaryService(http, self.store, self._config(allow_web=True))
        third = fresh.lookup("run", lang="en")
        self.assertTrue(third.cached)
        self.assertEqual(http.calls, 1)
        self.assertEqual(self.store.cache_stats()["hits"], 1)

    def test_chinese_and_english_cached_separately(self):
        zh_html = (FIXTURES / "cambridge_run.html").read_text(encoding="utf-8")
        http = FakeHttp(zh_html)
        service = DictionaryService(http, self.store, self._config(allow_web=True))
        zh = service.lookup("run")  # 默认英汉
        self.assertEqual(zh.provider, "cambridge")
        self.assertEqual(zh.lang, "zh")
        # 同一个词再查英英，必须另发一次请求、另存一条缓存
        http.html = self.html
        en = service.lookup_en("run")
        self.assertEqual(en.provider, "oxford_web")
        self.assertEqual(http.calls, 2)
        self.assertEqual(self.store.cache_stats()["words"], 2)

    def test_unknown_word_uses_fallback_then_glossary(self):
        http = FakeHttp(error=NetworkError("连接超时"))
        service = DictionaryService(
            http, self.store, self._config(allow_web=True, allow_fallback=False)
        )
        entry = service.lookup("twilight")
        self.assertEqual(entry.provider, "local")

    def test_network_failure_without_glossary(self):
        http = FakeHttp(error=NetworkError("连接超时"))
        service = DictionaryService(
            http, self.store, self._config(allow_web=True, allow_fallback=False)
        )
        with self.assertRaises(WordNotFound):
            service.lookup("defenestrate")

    def test_empty_word_rejected(self):
        service = DictionaryService(FakeHttp(""), self.store, self._config())
        with self.assertRaises(WordNotFound):
            service.lookup("   ")


if __name__ == "__main__":
    unittest.main()
