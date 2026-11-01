"""词典查询：英汉为主，英英按需。

两条独立链路，互不替换
1. 英汉（默认，点词即查）：剑桥英汉双解词典
   dictionary.cambridge.org/dictionary/english-chinese-simplified/
   牛津官方在线英汉（牛津高阶英汉双解第 10 版）需要登录授权，无法直接抓取，
   因此英汉统一走剑桥——同样是牛津/剑桥/朗文三大学习者词典之一，公开可访问。
2. 英英（按需，点「EN」圆钮才查）：
   Oxford Dictionaries API（配置 app_id / app_key 后启用）
   → 牛津学习者词典网页（义项、例句、习语、词源）
   → 备用词典 dictionaryapi.dev → 内置离线词表

界面永远标注真实来源，不会把剑桥或备用词典的内容冒充成牛津。
查询结果按「词 + 语言」缓存，默认 30 天，命中时直接返回并标记 cached。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Any

from . import htmltree, paths
from .fetch import HttpClient, NetworkError
from .htmltree import Node
from .textproc import normalize_word

OXFORD_WEB = "https://www.oxfordlearnersdictionaries.com/definition/english/{word}"
OXFORD_API = "https://od-api.oxforddictionaries.com/api/v2/entries/en-gb/{word}"
CAMBRIDGE_WEB = (
    "https://dictionary.cambridge.org/dictionary/english-chinese-simplified/{word}"
)
FALLBACK_API = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"


class WordNotFound(Exception):
    pass


@dataclass
class Sense:
    pos: str = ""
    # 主释义：英汉条目放中文译词，英英条目放英文释义
    definition: str = ""
    # 英汉条目的英文原释义（界面用小字附在中文译词下面）
    gloss: str = ""
    labels: list[str] = field(default_factory=list)
    grammar: str = ""
    examples: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sense":
        return cls(
            pos=data.get("pos", ""),
            definition=data.get("definition", ""),
            gloss=data.get("gloss", ""),
            labels=list(data.get("labels", [])),
            grammar=data.get("grammar", ""),
            examples=list(data.get("examples", [])),
            synonyms=list(data.get("synonyms", [])),
        )


@dataclass
class DictEntry:
    word: str
    provider: str
    source_label: str
    source_url: str = ""
    # zh = 英汉（中文译词为主）；en = 英英（英文释义为主）
    lang: str = "en"
    phonetics: dict[str, str] = field(default_factory=dict)
    senses: list[Sense] = field(default_factory=list)
    idioms: list[dict[str, str]] = field(default_factory=list)
    origin: str = ""
    note: str = ""
    fetched_at: float = field(default_factory=time.time)
    cached: bool = False

    @property
    def is_chinese(self) -> bool:
        return self.lang == "zh"

    @property
    def primary_pos(self) -> str:
        return self.senses[0].pos if self.senses else ""

    def grouped(self) -> list[tuple[str, list[Sense]]]:
        groups: list[tuple[str, list[Sense]]] = []
        for sense in self.senses:
            if groups and groups[-1][0] == sense.pos:
                groups[-1][1].append(sense)
            else:
                groups.append((sense.pos, [sense]))
        return groups

    def brief(self, limit: int = 180) -> str:
        if not self.senses:
            return ""
        text = self.senses[0].definition
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["senses"] = [s.to_dict() for s in self.senses]
        data["cached"] = False
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DictEntry":
        return cls(
            word=data.get("word", ""),
            provider=data.get("provider", ""),
            source_label=data.get("source_label", ""),
            source_url=data.get("source_url", ""),
            lang=data.get("lang", "en"),
            phonetics=dict(data.get("phonetics", {})),
            senses=[Sense.from_dict(s) for s in data.get("senses", [])],
            idioms=list(data.get("idioms", [])),
            origin=data.get("origin", ""),
            note=data.get("note", ""),
            fetched_at=float(data.get("fetched_at", time.time())),
            cached=False,
        )


# ------------------------------------------------------------------- 剑桥英汉
def parse_cambridge_html(html_text: str, word: str, url: str) -> DictEntry:
    """解析剑桥英汉双解词典词条。

    结构（2026-10 实测）：
        div.entry-body
          div.entry-body__el            每个词性一块
            span.pos                    词性
            span.uk / span.us > span.ipa
            span.usage                  语域标签（formal / informal / old-fashioned …）
            div.def-block
              div.def                   英文释义
              span.trans                中文译词
              div.examp > span.eg + span.trans   例句与例句译文
    """
    tree = htmltree.parse(html_text)
    body = tree.first("div", cls="entry-body")
    if body is None:
        raise WordNotFound("剑桥词典没有收录这个词")

    headword_node = body.first("span", cls="hw")
    headword = headword_node.text() if headword_node else word
    headword = headword or word

    phonetics: dict[str, str] = {}
    senses: list[Sense] = []
    entries = body.find_all("div", cls="entry-body__el") or [body]
    for el in entries:
        pos_node = el.first("span", cls="pos")
        pos = pos_node.text() if pos_node else ""
        for region, key in (("uk", "uk"), ("us", "us")):
            holder = el.first("span", cls=region)
            ipa = holder.first("span", cls="ipa") if holder else None
            if ipa and ipa.text():
                phonetics.setdefault(key, f"/{ipa.text().strip('/')}/")

        # 语域标签可能写在词性行（formal / informal / old-fashioned），
        # 也可能写在单个义项里；两处都收，去重后挂到该词性的义项上。
        pos_labels: list[str] = []
        for span in el.find_all("span", cls="usage"):
            text = span.text()
            if text and text not in pos_labels:
                pos_labels.append(text)

        for block in el.find_all("div", cls="def-block"):
            if any("idiom-block" in a.classes for a in block.ancestors()):
                continue  # 习语单独处理，避免混进主义项
            gloss_node = block.first("div", cls="def")
            gloss = _text(gloss_node, 600)
            zh_node = block.first("span", cls="trans")
            zh = _text(zh_node, 400)
            if not zh and not gloss:
                continue

            labels = list(pos_labels)
            for span in block.find_all("span", cls="usage"):
                text = span.text()
                if text and text not in labels:
                    labels.append(text)

            grammar = ""
            gram_node = block.first("span", cls="gram")
            if gram_node:
                grammar = gram_node.text()

            examples: list[str] = []
            for examp in block.find_all("div", cls="examp"):
                en_node = examp.first("span", cls="eg")
                cn_node = examp.first("span", cls="trans")
                en = en_node.text() if en_node else ""
                cn = cn_node.text() if cn_node else ""
                line = f"{en} — {cn}" if en and cn else (en or cn)
                line = line.strip(" —")
                if line and line not in examples:
                    examples.append(line)
                if len(examples) >= 2:
                    break

            senses.append(
                Sense(
                    pos=pos,
                    definition=zh or gloss,
                    gloss=gloss if zh else "",
                    labels=labels,
                    grammar=grammar,
                    examples=examples,
                )
            )

    if not senses:
        raise WordNotFound("剑桥词典页面结构无法识别")

    note = ""
    if headword.lower().replace(" ", "") != word.lower().replace(" ", ""):
        note = f"剑桥把「{word}」收在 {headword} 条目下，释义是该词条的相关义项。"

    return DictEntry(
        word=headword,
        provider="cambridge",
        source_label="剑桥英汉双解词典 · Cambridge English-Chinese (Simplified)",
        source_url=url,
        lang="zh",
        phonetics=phonetics,
        senses=senses,
        note=note or "剑桥大学出版社，英汉双解；英文释义保留，便于对照。",
    )


# --------------------------------------------------------------------- 牛津网页
def _text(node: Node | None, limit: int = 400) -> str:
    if node is None:
        return ""
    return node.text()[:limit]


def parse_oxford_html(html_text: str, word: str, url: str) -> DictEntry:
    tree = htmltree.parse(html_text)
    content = tree.first(id="entryContent") or tree
    # 只取主词条，避免把「相关词条 / 其他结果」的音标和释义算进来
    entry = content.first("div", cls="entry") or content
    top = entry.first("div", cls="top-container") or entry

    headword = ""
    h1 = top.first("h1", cls="headword")
    if h1:
        headword = h1.text()
    headword = headword or word

    phonetics: dict[str, str] = {}
    for node in top.find_all("div"):
        classes = node.classes
        if "phons_br" in classes or "phons_n_am" in classes:
            phon = node.first("span", cls="phon")
            if phon and phon.text():
                phonetics.setdefault("uk" if "phons_br" in classes else "us", phon.text())

    senses: list[Sense] = []
    idioms: list[dict[str, str]] = []
    current_pos = ""
    for node in entry.walk():
        if node.tag == "span" and "pos" in node.classes and node.parent is not None:
            text = node.text()
            if 1 <= len(text) <= 20:
                current_pos = text
            continue
        if node.tag == "li" and "sense" in node.classes:
            if any("idm-g" in a.classes for a in node.ancestors()):
                continue  # 习语区里的释义单独归到 idioms
            sense = _parse_sense(node, current_pos)
            if sense and sense.definition:
                senses.append(sense)
            continue
        if node.tag == "span" and "idm" in node.classes:
            phrase = node.text()
            definition = ""
            wrapper = next(
                (a for a in node.ancestors() if "idm-g" in a.classes), node.parent
            )
            if wrapper is not None:
                definition = _text(wrapper.first("span", cls="def"), 300)
            if phrase and definition:
                idioms.append({"phrase": phrase, "definition": definition})
            continue

    if not idioms:
        for node in entry.find_all("span", cls="idm"):
            phrase = node.text()
            if phrase:
                idioms.append({"phrase": phrase, "definition": ""})

    origin = ""
    for node in entry.find_all(pred=lambda n: n.attr("unbox") == "wordorigin"):
        text = node.text()
        text = re.sub(r"^word origin\s*", "", text, flags=re.I)
        if text:
            origin = text
            break
    if not origin:
        for node in entry.find_all(pred=lambda n: "wordorigin" in n.classes or n.id == "wordorigin"):
            origin = re.sub(r"^word origin\s*", "", node.text(), flags=re.I)
            if origin:
                break

    if not senses and not idioms:
        raise WordNotFound("牛津词典页面结构无法识别")

    return DictEntry(
        word=headword,
        provider="oxford_web",
        source_label="牛津学习者词典 · Oxford Learner's Dictionaries",
        source_url=url,
        lang="en",
        phonetics=phonetics,
        senses=senses,
        idioms=idioms,
        origin=origin[:600],
        note="网页抓取，义项与例句以牛津官网为准。" if not origin else "",
    )


def _parse_sense(node: Node, pos: str) -> Sense | None:
    definition_node = node.first("span", cls="def")
    if definition_node is None:
        # 有些义项把释义直接放在 span.sensetop 里
        definition_node = node.first("span", cls="sensetop")
    definition = _text(definition_node, 600)
    if not definition or len(definition) < 2:
        return None

    labels = []
    for span in node.find_all("span", cls="labels"):
        text = span.text()
        if text and text not in labels:
            labels.append(text)

    grammar = ""
    grammar_node = node.first("span", cls="grammar")
    if grammar_node:
        grammar = grammar_node.text()
    collocation = node.first("span", cls="cf")
    if collocation and collocation.text():
        grammar = f"{grammar} {collocation.text()}".strip()

    examples: list[str] = []
    for span in node.find_all("span", cls="x"):
        text = span.text()
        if text and text not in examples and len(examples) < 3:
            examples.append(text)
    for span in node.find_all("span", cls="unx"):
        text = span.text()
        if text and text not in examples and len(examples) < 3:
            examples.append(text)

    synonyms: list[str] = []
    for span in node.find_all("span", cls="syn"):
        text = span.text()
        if text and text not in synonyms:
            synonyms.append(text)

    return Sense(
        pos=pos,
        definition=definition,
        labels=labels,
        grammar=grammar,
        examples=examples,
        synonyms=synonyms[:6],
    )


# ----------------------------------------------------------------------- 牛津 API
def parse_oxford_api(data: Any, word: str, url: str) -> DictEntry:
    results = (data or {}).get("results") or []
    if not results:
        raise WordNotFound("牛津 API 没有返回该词")
    senses: list[Sense] = []
    phonetics: dict[str, str] = {}
    idioms: list[dict[str, str]] = []
    origin = ""
    for result in results:
        for lexical in result.get("lexicalEntries", []) or []:
            pos = (lexical.get("lexicalCategory") or {}).get("text", "")
            for pron in lexical.get("pronunciations", []) or []:
                # 方言字段是 "American English" / "British English"，不是 US/UK
                region = str((pron.get("dialects") or ["uk"])[0] or "").upper()
                key = "us" if ("US" in region or "AMERICAN" in region) else "uk"
                if pron.get("phoneticSpelling"):
                    phonetics.setdefault(key, f"/{pron['phoneticSpelling']}/")
            for entry in lexical.get("entries", []) or []:
                origin = origin or "\n".join(entry.get("etymologies", []) or [])
                for sense in _walk_api_senses(entry.get("senses", []) or []):
                    definition = "; ".join(sense.get("definitions", []) or [])
                    if not definition:
                        continue
                    senses.append(
                        Sense(
                            pos=pos,
                            definition=definition,
                            labels=[
                                x.get("text", "")
                                for x in sense.get("registers", []) or []
                                if isinstance(x, dict)
                            ],
                            grammar=", ".join(
                                x.get("text", "")
                                for x in sense.get("grammaticalFeatures", []) or []
                                if isinstance(x, dict)
                            ),
                            examples=[
                                x.get("text", "") for x in sense.get("examples", []) or []
                            ][:4],
                            synonyms=[
                                x.get("text", "")
                                for x in (sense.get("synonyms") or [])
                                if isinstance(x, dict)
                            ][:6],
                        )
                    )
    if not senses:
        raise WordNotFound("牛津 API 没有可用释义")
    return DictEntry(
        word=word,
        provider="oxford_api",
        source_label="牛津词典 · Oxford Dictionaries API",
        source_url=url,
        lang="en",
        phonetics=phonetics,
        senses=senses,
        idioms=idioms,
        origin=origin[:600],
        note="通过牛津官方 API 获取。",
    )


def _walk_api_senses(senses: list[dict[str, Any]]):
    for sense in senses:
        yield sense
        yield from _walk_api_senses(sense.get("subsenses", []) or [])


# ------------------------------------------------------------------------ 备用
def parse_fallback_api(data: Any, word: str) -> DictEntry:
    if not isinstance(data, list) or not data:
        raise WordNotFound("备用词典没有收录该词")
    senses: list[Sense] = []
    phonetics: dict[str, str] = {}
    origin = ""
    for entry in data:
        for phon in entry.get("phonetics", []) or []:
            text = phon.get("text")
            if text and "uk" not in phonetics:
                phonetics["uk"] = text
        origin = origin or entry.get("origin", "")
        for meaning in entry.get("meanings", []) or []:
            pos = meaning.get("partOfSpeech", "")
            for definition in meaning.get("definitions", []) or []:
                text = definition.get("definition", "")
                if not text:
                    continue
                senses.append(
                    Sense(
                        pos=pos,
                        definition=text,
                        examples=[definition["example"]] if definition.get("example") else [],
                        synonyms=(definition.get("synonyms") or [])[:6],
                    )
                )
    if not senses:
        raise WordNotFound("备用词典没有可用释义")
    return DictEntry(
        word=word,
        provider="dictionaryapi",
        source_label="备用词典 · Wiktionary（非牛津）",
        source_url=f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}",
        lang="en",
        phonetics=phonetics,
        senses=senses,
        origin=origin[:600],
        note="牛津不可用时的备用释义，仅供参考。",
    )


class LocalGlossary:
    def __init__(self, path=None):
        self.path = path or paths.resource_path("data", "glossary.json")
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def lookup(self, word: str) -> DictEntry | None:
        data = self._load().get(word.lower())
        if not data:
            return None
        senses = [
            Sense(
                pos=item.get("pos", ""),
                definition=item.get("def", ""),
                examples=item.get("examples", []),
            )
            for item in data.get("senses", [])
            if item.get("def")
        ]
        return DictEntry(
            word=word,
            provider="local",
            source_label="内置词表 · 离线备用",
            source_url="",
            lang="en",
            phonetics={"uk": data.get("phonetic", "")} if data.get("phonetic") else {},
            senses=senses,
            note="离线词表，仅覆盖示例文章中的常见词。",
        )


# ---------------------------------------------------------------------- 服务层
class DictionaryService:
    """两条独立链路：lang="zh" 查剑桥英汉，lang="en" 查牛津英英。

    英英只在用户点界面上的圆形按钮时才查，不点不发任何请求。
    """

    def __init__(
        self,
        http: HttpClient,
        store,
        config,
        *,
        glossary: LocalGlossary | None = None,
    ):
        self.http = http
        self.store = store
        self.config = config
        self.glossary = glossary or LocalGlossary()
        self._memory: dict[tuple[str, str], DictEntry] = {}

    def lookup(self, raw_word: str, *, force: bool = False, lang: str = "zh") -> DictEntry:
        word = normalize_word(raw_word)
        if not word or len(word) > 64:
            raise WordNotFound("请选择一个英文单词")
        lang = "en" if str(lang).lower().startswith("en") else "zh"
        key = (lang, word)

        if key in self._memory and not force:
            entry = DictEntry.from_dict(self._memory[key].to_dict())
            entry.cached = True
            return entry

        if not force:
            ttl = self.config.cache_ttl("word") if hasattr(self.config, "cache_ttl") else None
            cached = self.store.cache_get("word", word, scope=lang, max_age=ttl)
            if cached:
                entry = DictEntry.from_dict(cached["payload"])
                entry.cached = True
                return entry

        errors: list[str] = []
        for provider in self._providers(lang):
            try:
                entry = provider(word)
            except WordNotFound as exc:
                errors.append(str(exc))
                continue
            except NetworkError as exc:
                errors.append(exc.message)
                continue
            except Exception as exc:  # 解析异常不应导致程序崩溃
                errors.append(f"词典解析失败：{exc}")
                continue
            if entry and entry.senses:
                self.store.cache_put(
                    "word", word, entry.to_dict(), provider=entry.provider, scope=lang
                )
                self._memory[key] = entry
                return entry
        message = "没查到这个词的释义"
        if errors:
            message = f"{message}（{errors[0]}）"
        raise WordNotFound(message)

    def lookup_en(self, raw_word: str, *, force: bool = False) -> DictEntry:
        """英英释义（牛津）；界面上的圆形按钮才会调到这里。"""
        return self.lookup(raw_word, force=force, lang="en")

    def _providers(self, lang: str):
        oxford = self.config.get("oxford", default={}) or {}
        if lang == "zh":
            yield self._cambridge_web
            if oxford.get("allow_fallback", True):
                yield self._fallback_as_chinese
            yield self._glossary
            return
        if oxford.get("app_id") and oxford.get("app_key"):
            yield lambda word: self._oxford_api(word, oxford["app_id"], oxford["app_key"])
        if oxford.get("allow_web", True):
            yield self._oxford_web
        if oxford.get("allow_fallback", True):
            yield self._fallback_api
        yield lambda word: self._glossary(word)

    # ------------------------------------------------------------------ 英汉
    def _cambridge_web(self, word: str) -> DictEntry:
        slug = urllib.parse.quote(word.replace(" ", "-"))
        url = CAMBRIDGE_WEB.format(word=slug)
        resp = self.http.get_text(
            url,
            headers={
                "Referer": "https://dictionary.cambridge.org/",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        return parse_cambridge_html(resp, word, url)

    def _fallback_as_chinese(self, word: str) -> DictEntry:
        entry = self._fallback_api(word)
        entry.note = "剑桥英汉没查到，以下是英英释义（备用词典），仅供参考。"
        return entry

    # ------------------------------------------------------------------ 英英
    def _oxford_web(self, word: str) -> DictEntry:
        url = OXFORD_WEB.format(word=urllib.parse.quote(word.replace(" ", "-")))
        resp = self.http.get_text(
            url,
            headers={
                "Referer": "https://www.oxfordlearnersdictionaries.com/",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        return parse_oxford_html(resp, word, url)

    def _oxford_api(self, word: str, app_id: str, app_key: str) -> DictEntry:
        url = OXFORD_API.format(word=urllib.parse.quote(word))
        data = self.http.get_json(
            url,
            headers={"app_id": app_id, "app_key": app_key, "Accept": "application/json"},
        )
        return parse_oxford_api(data, word, url)

    def _fallback_api(self, word: str) -> DictEntry:
        url = FALLBACK_API.format(word=urllib.parse.quote(word))
        data = self.http.get_json(url)
        return parse_fallback_api(data, word)

    def _glossary(self, word: str) -> DictEntry:
        entry = self.glossary.lookup(word)
        if not entry:
            raise WordNotFound("离线词表也未收录")
        return entry
