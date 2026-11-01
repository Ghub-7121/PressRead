"""翻译服务：分块、provider 降级、熔断、缓存、必应三档语气。"""

import json
import tempfile
import unittest
from pathlib import Path

from pressread.config import Config
from pressread.fetch import NetworkError
from pressread.store import Store
from pressread.translate import TranslationError, Translator, _chunks


class ChunkTests(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(_chunks("Hello there."), ["Hello there."])

    def test_empty(self):
        self.assertEqual(_chunks("   "), [])

    def test_long_text_split_on_sentences(self):
        text = " ".join(["The kelp grows quickly in cold water."] * 20)
        chunks = _chunks(text, 120)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 120)
        self.assertEqual(" ".join(chunks), " ".join(text.split()))

    def test_single_sentence_longer_than_limit(self):
        text = "word " * 200
        chunks = _chunks(text.strip() + ".", 60)
        self.assertTrue(all(len(chunk) <= 60 for chunk in chunks))


BING_PAGE = (
    '<html><head><script>var _G={IG:"74F8BC3BE97C4903BA6F73979F581DD2"};</script></head>'
    '<body><div id="tta_out" data-iid="translator.5023"></div>'
    "<script>var params_AbusePreventionHelper = [1793423456652,\"QCHW76j_xqvaT7\",3600000];</script>"
    "</body></html>"
)


class _Resp:
    def __init__(self, text: str, url: str = ""):
        self.text = text
        self.url = url
        self.status = 200
        self.elapsed = 0.01
        self.headers: dict[str, str] = {}


class FakeHttp:
    """按 URL 分派的假网络层，记录调用与请求字段。"""

    def __init__(
        self,
        *,
        mymemory=None,
        google=None,
        libre=None,
        bing=None,
        bing_page: str = BING_PAGE,
        error=None,
    ):
        self.mymemory = mymemory
        self.google = google
        self.libre = libre
        # bing 可以是字符串（所有语气都返回它）或 {"": 默认, "Formal": ..., "Casual": ...}
        self.bing = bing
        self.bing_page = bing_page
        self.error = error
        self.calls: list[str] = []
        self.last_form: dict = {}
        self.last_headers: dict = {}
        self.forms: list[dict] = []

    # 抓页面（必应 token）
    def request(self, url, data=None, headers=None, timeout=None, method=None, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        return _Resp(self.bing_page, url)

    def get_json(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        if "mymemory" in url:
            if isinstance(self.mymemory, Exception):
                raise self.mymemory
            return self.mymemory
        if "translate.google" in url or "clients5" in url:
            if isinstance(self.google, Exception):
                raise self.google
            return self.google
        raise AssertionError(f"unexpected url {url}")

    def post_form(self, url, fields, headers=None, timeout=None, **kwargs):
        self.calls.append(url)
        self.last_form = dict(fields)
        self.last_headers = dict(headers or {})
        self.forms.append(dict(fields))
        if isinstance(self.bing, Exception):
            raise self.bing
        if isinstance(self.bing, dict):
            text = self.bing.get(fields.get("tone", ""), "默认译文")
        else:
            text = self.bing if self.bing is not None else "默认译文"
        if text == "__EMPTY__":
            return _Resp("")
        if text == "__205__":
            return _Resp(json.dumps({"statusCode": 205, "errorMessage": ""}))
        return _Resp(json.dumps([{"translations": [{"text": text, "to": "zh-Hans"}]}]))

    def post_json(self, url, payload, headers=None, timeout=None, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        if isinstance(self.libre, Exception):
            raise self.libre
        return self.libre


MM_OK = {
    "responseStatus": 200,
    "responseData": {"translatedText": "海带在冷水中生长得很快。"},
    "matches": [
        {"translation": "海带在冷水中长得很快。", "quality": "85"},
        {"translation": "海带长得快。", "quality": "40"},
        {"translation": "海带在冷水中生长得很快。", "quality": "100"},
    ],
}


class TranslatorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store = Store(base / "pressread.db")
        self.config = Config.load(base / "config.json")
        self.config.data["translator"]["providers"] = ["mymemory"]
        self.config.data["translator"]["target_lang"] = "zh-CN"

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def translator(self, http) -> Translator:
        return Translator(http, self.store, self.config)


class MyMemoryTests(TranslatorTestCase):
    def test_success_and_alternatives(self):
        http = FakeHttp(mymemory=MM_OK)
        result = self.translator(http).translate("The kelp grows quickly in cold water.")
        self.assertEqual(result.provider, "mymemory")
        self.assertEqual(result.text, "海带在冷水中生长得很快。")
        self.assertEqual(result.alternatives, ["海带在冷水中长得很快。"])
        self.assertEqual(result.source_label, "MyMemory 翻译记忆库")
        self.assertEqual(len(http.calls), 1)

    def test_cached_second_call(self):
        http = FakeHttp(mymemory=MM_OK)
        service = self.translator(http)
        service.translate("The kelp grows quickly in cold water.")
        again = service.translate("The kelp grows quickly in cold water.")
        self.assertTrue(again.cached)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.store.cache_stats()["texts"], 1)

    def test_chunked_requests_are_joined(self):
        http = FakeHttp(mymemory=MM_OK)
        long_text = " ".join(["The kelp grows quickly in cold water."] * 20)
        result = self.translator(http).translate(long_text)
        self.assertGreater(len(http.calls), 1)
        self.assertEqual(result.text.count("海带"), len(http.calls))

    def test_quota_warning(self):
        http = FakeHttp(mymemory={"responseStatus": 200,
                                  "responseData": {"translatedText": "MYMEMORY WARNING: quota"}})
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.")
        self.assertIn("翻译失败", str(ctx.exception))

    def test_empty_response_status(self):
        http = FakeHttp(mymemory={"responseStatus": 403,
                                  "responseDetails": "INVALID EMAIL PROVIDED"})
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.")
        self.assertIn("INVALID EMAIL", str(ctx.exception))


class FallbackTests(TranslatorTestCase):
    def test_google_used_when_mymemory_fails(self):
        self.config.data["translator"]["providers"] = ["mymemory", "google"]
        http = FakeHttp(
            mymemory=NetworkError("连接超时"),
            google=[[["海带生长得很快。", "The kelp grows quickly.", None, None]]],
        )
        result = self.translator(http).translate("The kelp grows quickly.")
        self.assertEqual(result.provider, "google")
        self.assertEqual(result.text, "海带生长得很快。")

    def test_all_providers_failing(self):
        self.config.data["translator"]["providers"] = ["mymemory", "google"]
        http = FakeHttp(mymemory=NetworkError("连接超时"), google=NetworkError("连接被重置"))
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.")
        self.assertIn("翻译失败", str(ctx.exception))

    def test_circuit_breaker_skips_after_two_failures(self):
        http = FakeHttp(mymemory=NetworkError("连接超时"))
        service = self.translator(http)
        for _ in range(2):
            with self.assertRaises(TranslationError):
                service.translate("Hello there.")
        calls_before = len(http.calls)
        with self.assertRaises(TranslationError):
            service.translate("A different sentence.")
        self.assertEqual(len(http.calls), calls_before)  # 熔断后不再发请求

    def test_libre_when_configured(self):
        self.config.data["translator"]["providers"] = []
        self.config.data["translator"]["libre_url"] = "https://libre.example.com"
        http = FakeHttp(libre={"translatedText": "海带生长得很快。"})
        result = self.translator(http).translate("The kelp grows quickly.")
        self.assertEqual(result.provider, "libre")
        self.assertEqual(result.source_url, "https://libre.example.com")

    def test_blank_text_rejected(self):
        with self.assertRaises(TranslationError):
            self.translator(FakeHttp(mymemory=MM_OK)).translate("   ")


class BingToneTests(TranslatorTestCase):
    """必应免 Key 通道：页面取 token，接口带 tone=Formal / Casual。"""

    def _use_bing(self, tone="default"):
        self.config.data["translator"]["providers"] = ["bing"]
        self.config.data["translator"]["tone"] = tone
        return FakeHttp(bing={"": "嘿，伙计，怎么样？", "Formal": "你好，朋友，你怎么样？",
                              "Casual": "嘿，老兄，咋了？"})

    def test_default_tone_sends_no_tone_field(self):
        http = self._use_bing()
        result = self.translator(http).translate("Hey buddy, what's up?")
        self.assertEqual(result.provider, "bing")
        self.assertEqual(result.text, "嘿，伙计，怎么样？")
        self.assertNotIn("tone", http.last_form)
        self.assertEqual(http.last_form["to"], "zh-Hans")
        self.assertEqual(http.last_form["fromLang"], "en")
        self.assertTrue(result.tone_capable)
        self.assertEqual(result.tone, "default")

    def test_formal_maps_to_Formal(self):
        http = self._use_bing(tone="formal")
        result = self.translator(http).translate("Hey buddy, what's up?", tone="formal")
        self.assertEqual(http.last_form["tone"], "Formal")
        self.assertEqual(result.text, "你好，朋友，你怎么样？")
        self.assertEqual(result.tone, "formal")
        self.assertIn("正式", result.note)

    def test_informal_maps_to_Casual(self):
        http = self._use_bing()
        result = self.translator(http).translate("Hey buddy, what's up?", tone="informal")
        self.assertEqual(http.last_form["tone"], "Casual")
        self.assertEqual(result.text, "嘿，老兄，咋了？")
        self.assertEqual(result.tone, "informal")

    def test_page_token_fetched_once(self):
        http = self._use_bing()
        service = self.translator(http)
        service.translate("First sentence here.")
        service.translate("Second sentence here.")
        pages = [u for u in http.calls if "bing.com/translator" in u and "ttranslate" not in u]
        posts = [u for u in http.calls if "ttranslatev3" in u]
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(posts), 2)

    def test_tones_cached_separately(self):
        http = self._use_bing()
        service = self.translator(http)
        service.translate("Hello there.")
        service.translate("Hello there.", tone="formal")
        service.translate("Hello there.", tone="formal")  # 命中缓存
        posts = [u for u in http.calls if "ttranslatev3" in u]
        self.assertEqual(len(posts), 2)
        self.assertEqual(self.store.cache_stats()["texts"], 2)

    def test_expired_token_refetched_once(self):
        http = self._use_bing()
        http.bing = "__EMPTY__"
        service = self.translator(http)
        # 第一次空响应 → 重取页面 → 第二次仍空则报错
        with self.assertRaises(TranslationError):
            service.translate("Hello there.")
        pages = [u for u in http.calls if "bing.com/translator" in u]
        self.assertEqual(len(pages), 2)  # 首次 + 过期后重取

    def test_status_205_triggers_retry(self):
        http = self._use_bing()
        http.bing = "__205__"
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.")
        self.assertIn("必应", str(ctx.exception))
        pages = [u for u in http.calls if "bing.com/translator" in u]
        self.assertEqual(len(pages), 2)

    def test_page_structure_changed_raises_clear_error(self):
        http = FakeHttp(bing_page="<html><body>验证码</body></html>")
        self.config.data["translator"]["providers"] = ["bing"]
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.")
        self.assertIn("必应", str(ctx.exception))

    def test_tone_without_bing_raises_clear_error(self):
        self.config.data["translator"]["providers"] = ["mymemory"]
        http = FakeHttp(mymemory=MM_OK)
        with self.assertRaises(TranslationError) as ctx:
            self.translator(http).translate("Hello there.", tone="formal")
        self.assertIn("必应", str(ctx.exception))
        self.assertEqual(http.calls, [])

    def test_non_tone_provider_is_labeled(self):
        http = FakeHttp(mymemory=MM_OK)
        result = self.translator(http).translate("The kelp grows quickly.")
        self.assertFalse(result.tone_capable)
        self.assertEqual(result.tone, "default")

    def test_bing_falls_back_to_mymemory(self):
        self.config.data["translator"]["providers"] = ["bing", "mymemory"]
        http = FakeHttp(bing=NetworkError("连接超时"), mymemory=MM_OK)
        result = self.translator(http).translate("The kelp grows quickly.")
        self.assertEqual(result.provider, "mymemory")


class ResultShapeTests(unittest.TestCase):
    def test_roundtrip(self):
        from pressread.translate import TranslationResult

        result = TranslationResult(
            text="你好",
            provider="mymemory",
            source_label="MyMemory 翻译记忆库",
            alternatives=["您好"],
            elapsed_ms=42,
        )
        again = TranslationResult.from_dict(result.to_dict())
        self.assertEqual(again.text, "你好")
        self.assertEqual(again.alternatives, ["您好"])
        self.assertFalse(again.tone_capable)
        self.assertEqual(again.tone, "default")
        # 耗时不入库：命中缓存时没有真实耗时，界面改用「来自本地缓存」标注
        self.assertEqual(again.elapsed_ms, 0)


if __name__ == "__main__":
    unittest.main()
