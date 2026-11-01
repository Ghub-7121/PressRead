"""外刊源目录与 RSS/Atom 解析。

源列表是「目录」不是「硬编码可用性」：程序启动后按需拉取，哪个源在当前网络
下不通就跳过，并把错误收集给 UI 展示。

目录分两条线：
- 新闻/科普线（科学、科技、文化、国际、商业），练长难句与信息类阅读；
- B 篇线（随笔、教育、人物、成长），取材特征是「有人物、有场景、中段有转折、
  结尾能提炼出道理」，多来自中小刊物而非大报名刊。
"""

from __future__ import annotations

import email.utils
import html as html_lib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from . import htmltree
from .textproc import clean_paragraph, normalize_space, strip_tags


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    chinese_name: str
    category: str
    feed_url: str
    homepage: str
    blurb: str
    # kind="html" 表示该站没有可用 RSS：feed_url 是栏目列表页，
    # 由 parse_listing() 按 link_pattern 抓文章链接。
    kind: str = "rss"
    link_pattern: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}（{self.chinese_name}）"


SOURCES: list[Source] = [
    Source(
        "sciencenews", "Science News", "科学新闻", "科学",
        "https://www.sciencenews.org/feed", "https://www.sciencenews.org",
        "美国老牌科学新闻杂志，生命科学与地球科学见长，句式规整。",
    ),
    Source(
        "smithsonian", "Smithsonian Magazine", "史密森尼", "文化",
        "https://www.smithsonianmag.com/rss/latest_articles/", "https://www.smithsonianmag.com",
        "史密森学会出品，历史、艺术与自然史题材，叙事性强。",
    ),
    Source(
        "wired", "WIRED", "连线", "科技",
        "https://www.wired.com/feed/rss", "https://www.wired.com",
        "科技文化杂志，长句与观点表达多，适合进阶阅读。",
    ),
    Source(
        "quanta", "Quanta Magazine", "量子杂志", "科学",
        "https://www.quantamagazine.org/feed/", "https://www.quantamagazine.org",
        "数学与物理科普，逻辑链条长，练长难句的好材料。",
    ),
    Source(
        "technologyreview", "MIT Technology Review", "麻省理工科技评论", "科技",
        "https://www.technologyreview.com/feed/", "https://www.technologyreview.com",
        "技术趋势与产业分析，术语密度高。",
    ),
    Source(
        "npr", "NPR", "美国国家公共广播", "国际",
        "https://feeds.npr.org/1001/rss.xml", "https://www.npr.org",
        "美国公共广播新闻，社会与国际报道，语言平实。",
    ),
    Source(
        "nationalgeographic", "National Geographic", "国家地理", "科学",
        "https://www.nationalgeographic.com/", "https://www.nationalgeographic.com",
        "黄色方框的经典科普杂志，动物、环境与探险报道，图文叙事见长。",
        kind="html", link_pattern=r"/article/",
    ),
    Source(
        "latimes", "Los Angeles Times", "洛杉矶时报", "国际",
        "https://www.latimes.com/world-nation/rss2.0.xml", "https://www.latimes.com",
        "美国西海岸大报，国际与本土议题并重，报道文体规整。",
    ),
    Source(
        "cbsnews", "CBS News", "哥伦比亚广播公司新闻", "国际",
        "https://www.cbsnews.com/latest/rss/main", "https://www.cbsnews.com",
        "美国三大电视网新闻，短句多、信息密度高。",
    ),
    Source(
        "politico", "Politico", "政治报", "国际",
        "https://rss.politico.com/politics-news.xml", "https://www.politico.com",
        "美国政治与政策报道，适合积累政治、法律语汇。",
    ),
    Source(
        "popsci", "Popular Science", "大众科学", "科技",
        "https://www.popsci.com/feed/", "https://www.popsci.com",
        "美国老牌科普杂志，科技与自然题材，篇幅适中。",
    ),
    Source(
        "cnet", "CNET", "科技资讯网", "科技",
        "https://www.cnet.com/rss/news/", "https://www.cnet.com",
        "消费电子与数字生活报道，产品描述多。",
    ),
    Source(
        "sciencedaily", "ScienceDaily", "每日科学", "科学",
        "https://www.sciencedaily.com/rss/all.xml", "https://www.sciencedaily.com",
        "高校与期刊新闻稿汇总，句式标准、术语密集。",
    ),
    Source(
        "aeon", "Aeon", "万古", "文化",
        "https://aeon.co/feed.rss", "https://aeon.co",
        "哲学与科学长文，论证型写作，练复杂句的好材料。",
    ),
    Source(
        "nautilus", "Nautilus", "鹦鹉螺", "科学",
        "https://nautil.us/feed/", "https://nautil.us",
        "科学与人文交叉的专题杂志，文笔讲究。",
    ),
    Source(
        "cnbc", "CNBC", "消费者新闻与商业频道", "商业",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html", "https://www.cnbc.com",
        "美国财经电视网，市场与宏观经济报道，数字与图表表述多。",
    ),
    Source(
        "fortune", "Fortune", "财富", "商业",
        "https://fortune.com/feed/", "https://fortune.com",
        "商业与公司报道，财经表达集中。源站偶发很慢，默认关闭。",
    ),
    Source(
        "astronomy", "Astronomy", "天文学", "科学",
        "https://www.astronomy.com/feed/", "https://www.astronomy.com",
        "天文与观星科普，术语和场景描写多。",
    ),
    Source(
        "sciencealert", "ScienceAlert", "科学警报", "科学",
        "https://www.sciencealert.com/feed", "https://www.sciencealert.com",
        "大众科学新闻，选题轻快，适合日常阅读。",
    ),
    # ---- 以下源受网络环境影响较大，默认关闭，可在设置里勾选 ----
    Source(
        "time", "TIME", "时代", "国际",
        "https://time.com/feed/", "https://time.com",
        "美国新闻周刊，报道与评论并重。当前网络多不可达，换网络环境可试。",
    ),
    Source(
        "nytimes", "The New York Times", "纽约时报", "国际",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "https://www.nytimes.com",
        "美国大报，国际报道与深度调查。当前网络多不可达，需可用网络。",
    ),
    Source(
        "nature", "Nature", "自然", "科学",
        "https://www.nature.com/nature.rss", "https://www.nature.com",
        "顶级科学期刊的新闻与观点，学术表达密集。",
    ),
    Source(
        "science", "Science", "科学", "科学",
        "https://www.science.org/rss/news_current.xml", "https://www.science.org",
        "AAAS 出品的科学新闻。",
    ),
    Source(
        "guardian", "The Guardian", "卫报", "国际",
        "https://www.theguardian.com/world/rss", "https://www.theguardian.com",
        "英国老牌日报，国际报道与评论。",
    ),
    Source(
        "economist", "The Economist", "经济学人", "商业",
        "https://www.economist.com/international/rss.xml", "https://www.economist.com",
        "政经评论，句式考究，难度偏高。",
    ),
    Source(
        "bbc", "BBC News", "英国广播公司", "国际",
        "https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.bbc.com/news",
        "国际新闻，用词中性。",
    ),
    Source(
        "wsj", "The Wall Street Journal", "华尔街日报", "商业",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "https://www.wsj.com",
        "商业与国际新闻，正文可能有订阅墙。",
    ),
    Source(
        "france24", "France 24", "法国24台", "国际",
        "https://www.france24.com/en/rss", "https://www.france24.com/en/",
        "法国国际新闻，欧洲视角。",
    ),
    Source(
        "un", "UN News", "联合国新闻", "国际",
        "https://news.un.org/feed/subscribe/en/news/all/rss.xml", "https://news.un.org",
        "联合国官方新闻，议题以发展与人道为主。",
    ),
    # ---- B 篇向：叙事 / 教育 / 人物 / 成长 ----
    # 取材特征是「有人物、有具体场景、中段有转折、结尾能提炼出道理」，
    # 多来自专业领域的中小刊物，而不是《经济学人》《卫报》这类大报名刊。
    Source(
        "americanscholar", "The American Scholar", "美国学者", "随笔",
        "https://theamericanscholar.org/feed/", "https://theamericanscholar.org",
        "Phi Beta Kappa 学会的人文季刊，Essays 栏目多是「生活小事引出人生思考」的记叙文。",
    ),
    Source(
        "narratively", "Narratively", "叙事", "随笔",
        "https://narratively.com/feed/", "https://narratively.com",
        "纽约非虚构叙事媒体，第一人称真实故事，短、集中、转折清楚。",
    ),
    Source(
        "brevity", "Brevity", "极短篇非虚构", "随笔",
        "https://brevitymag.com/feed/", "https://brevitymag.com",
        "极短篇非虚构专刊（flash nonfiction），一篇一个场景、一个顿悟。",
    ),
    Source(
        "lithub", "Literary Hub", "文学中心", "随笔",
        "https://lithub.com/feed/", "https://lithub.com",
        "文学评论与作家随笔，人物细节多，适合练叙述性长句。",
    ),
    Source(
        "yesmag", "Yes! Magazine", "是的！杂志", "随笔",
        "https://www.yesmagazine.org/feed", "https://www.yesmagazine.org",
        "解决方案导向的社会议题报道，通常从某个具体的人讲起。",
    ),
    Source(
        "orion", "Orion Magazine", "猎户座", "随笔",
        "https://orionmagazine.org/", "https://orionmagazine.org",
        "自然与地方生活随笔，观察细致，常见「人与自然相处」的题材。",
        kind="html", link_pattern=r"/article/",
    ),
    Source(
        "hippocampus", "Hippocampus Magazine", "海马", "随笔",
        "https://www.hippocampusmagazine.com/feed/", "https://www.hippocampusmagazine.com",
        "回忆录式非虚构杂志，人生片段与家庭记忆题材集中。",
    ),
    Source(
        "csmonitor", "The Christian Science Monitor", "基督教科学箴言报", "随笔",
        "https://www.csmonitor.com/rss/Home-Forum", "https://www.csmonitor.com",
        "Home Forum 是个人随笔专版，篇幅短、有人情味，与 B 篇最接近。当前网络不可达，换网络可试。",
    ),
    Source(
        "edutopia", "Edutopia", "教育乌托邦", "教育",
        "https://www.edutopia.org/", "https://www.edutopia.org",
        "乔治·卢卡斯教育基金会出品，教师课堂实践与教学反思，叙事性强。",
        kind="html", link_pattern=r"/article/",
    ),
    Source(
        "insidehighered", "Inside Higher Ed", "高等教育内参", "教育",
        "https://www.insidehighered.com/rss.xml", "https://www.insidehighered.com",
        "美国高等教育新闻与观点，师生故事、教学改革题材多。",
    ),
    Source(
        "cultofpedagogy", "Cult of Pedagogy", "教学法崇拜", "教育",
        "https://www.cultofpedagogy.com/feed/", "https://www.cultofpedagogy.com",
        "一线教师的教学反思与课堂故事，常以「我改了什么、学生变了什么」展开。",
    ),
    Source(
        "facultyfocus", "Faculty Focus", "教师聚焦", "教育",
        "https://www.facultyfocus.com/feed/", "https://www.facultyfocus.com",
        "高校教师教学札记，短小，常从一个课堂细节切入。",
    ),
    Source(
        "the74", "The 74", "74 号", "教育",
        "https://www.the74million.org/feed/", "https://www.the74million.org",
        "美国 K-12 教育调查与人物报道，学生与教师的处境写得具体。",
    ),
    Source(
        "educationnext", "Education Next", "教育下一步", "教育",
        "https://www.educationnext.org/feed/", "https://www.educationnext.org",
        "教育政策与实证研究评论，观点文偏多，难度略高。",
    ),
    Source(
        "eschoolnews", "eSchool News", "校园科技新闻", "教育",
        "https://www.eschoolnews.com/feed/", "https://www.eschoolnews.com",
        "K-12 教育技术新闻，篇幅短、信息直给。",
    ),
    Source(
        "edweek", "Education Week", "教育周刊", "教育",
        "https://www.edweek.org/feed", "https://www.edweek.org",
        "教师叙事与课堂观察的经典来源，Opinion 版块最贴近 B 篇。源站有反爬，当前网络返回 403。",
    ),
    Source(
        "guideposts", "Guideposts", "路标", "人物",
        "https://guideposts.org/feed/", "https://guideposts.org",
        "励志真实故事杂志，普通人的困境与转机，结尾常有一句感悟。",
    ),
    Source(
        "storycorps", "StoryCorps", "故事团", "人物",
        "https://storycorps.org/stories/feed/", "https://storycorps.org",
        "普通人讲述亲身经历的短访谈，口语化，有人物、有场景。",
    ),
    Source(
        "bigissue", "The Big Issue", "大志", "人物",
        "https://www.bigissue.com/feed/", "https://www.bigissue.com",
        "英国街头杂志，弱势群体与普通劳动者的人物特写。",
    ),
    Source(
        "positivenews", "Positive News", "积极新闻", "人物",
        "https://www.positive.news/feed/", "https://www.positive.news",
        "建设性新闻，聚焦具体的人如何把问题解决掉。",
    ),
    Source(
        "rd", "Reader's Digest", "读者文摘", "人物",
        "https://www.rd.com/feed/", "https://www.rd.com",
        "人物特写与生活故事的经典来源。源站有反爬，当前网络返回 403。",
    ),
    Source(
        "fatherly", "Fatherly", "父性", "成长",
        "https://www.fatherly.com/feed", "https://www.fatherly.com",
        "父亲视角的育儿与家庭生活报道，生活场景具体。",
    ),
    Source(
        "motherly", "Motherly", "母性", "成长",
        "https://www.mother.ly/feed", "https://www.mother.ly",
        "母亲与家庭生活题材，第一人称经验多，语气平实。",
    ),
    Source(
        "snexplores", "Science News Explores", "青少年科学新闻", "成长",
        "https://www.snexplores.org/feed", "https://www.snexplores.org",
        "面向青少年的科学阅读，句子短、解释清楚，适合入门过渡。",
    ),
]

SOURCE_MAP: dict[str, Source] = {s.id: s for s in SOURCES}

# 分类顺序 = 设置页左栏顺序。B 篇相关的四类放前面，新闻/科普类在后。
CATEGORY_ORDER = ["随笔", "教育", "人物", "成长", "科学", "科技", "文化", "国际", "商业"]

CATEGORY_BLURB = {
    "随笔": "人文叙事，一篇讲一件小事，B 篇的主力来源",
    "教育": "教师与课堂故事，命题组的高频取材地",
    "人物": "普通人的经历与转折，结尾常有一句可提炼的道理",
    "成长": "家庭生活与青少年阅读，句子短、场景具体",
    "科学": "科普与科研新闻，练信息密度与术语",
    "科技": "技术趋势与产品报道，长句与观点多",
    "文化": "历史、艺术与思想评论",
    "国际": "国际新闻与大报报道，时政语汇集中",
    "商业": "财经与公司报道",
}

# 快捷方案：(key, 名称, 说明, 来源 id)
PRESETS: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "narrative", "叙事向 · B 篇", "人物 + 场景 + 转折，接近高考 B 篇的取材口味",
        (
            "americanscholar", "narratively", "brevity", "lithub", "yesmag", "orion",
            "hippocampus", "guideposts", "storycorps", "bigissue", "positivenews",
            "edutopia", "cultofpedagogy", "facultyfocus", "the74", "fatherly",
            "motherly", "smithsonian", "nautilus", "aeon",
        ),
    ),
    (
        "education", "教育观察", "课堂、师生、教学改革，教育类媒体合集",
        (
            "edutopia", "insidehighered", "cultofpedagogy", "facultyfocus",
            "the74", "educationnext", "eschoolnews",
        ),
    ),
    (
        "balanced", "均衡默认", "叙事类与科普类各占一半，日常练习的稳妥配置",
        (
            "americanscholar", "narratively", "edutopia", "cultofpedagogy", "guideposts",
            "storycorps", "fatherly", "smithsonian", "aeon", "nautilus", "wired",
            "sciencenews", "quanta", "technologyreview", "npr", "nationalgeographic",
            "popsci", "sciencedaily", "sciencealert", "astronomy", "latimes", "cbsnews",
            "politico", "cnbc", "cnet",
        ),
    ),
    (
        "science", "科普时评 · C/D 篇", "信息密度高、长难句多，练阅读速度用",
        (
            "sciencenews", "quanta", "technologyreview", "wired", "popsci",
            "sciencedaily", "sciencealert", "astronomy", "nautilus", "nature", "science",
            "economist", "guardian", "bbc",
        ),
    ),
]


def filter_sources(
    sources: list[Source] | None = None, *, query: str = "", category: str = ""
) -> list[Source]:
    """按分类与关键词筛选来源。

    关键词按空格拆成多个词，全部命中才算匹配（AND），匹配范围是英文名、
    中文名、id、简介与分类。分类传空或「全部」表示不限制。
    """
    items = list(SOURCES if sources is None else sources)
    cat = (category or "").strip()
    if cat and cat not in ("全部", "all"):
        items = [s for s in items if s.category == cat]
    terms = [t for t in (query or "").lower().split() if t]
    if terms:
        kept: list[Source] = []
        for source in items:
            haystack = " ".join(
                (source.name, source.chinese_name, source.id, source.blurb, source.category)
            ).lower()
            if all(term in haystack for term in terms):
                kept.append(source)
        items = kept
    return items

SKIP_URL_PATTERNS = [
    r"/podcast", r"/video", r"/videos/", r"/gallery/", r"/live/", r"/liveblog",
    r"/audio", r"/newsletters?/", r"/subscribe", r"/tag/", r"/author/",
    r"/shop", r"/store", r"/reviews?/best-", r"\.pdf$",
]

SKIP_TITLE_PATTERNS = [
    r"^(podcast|video|watch|listen|in pictures|live updates?)\b",
    r"newsletter",
    r"^daily briefing",
    r"^the download\b",
]


def _extract_link(node: ET.Element) -> str:
    """RSS 的 <link> 是文本，Atom 的是属性，还可能有 rel=self 的干扰项。"""
    fallback = ""
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        text = (child.text or "").strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        value = href or text
        if not value:
            continue
        if rel in ("alternate", "") and value.startswith("http"):
            return value
        if not fallback and value.startswith("http"):
            fallback = value
    for name in ("guid", "id"):
        for child in node:
            if _local(child.tag) == name and (child.text or "").startswith("http"):
                return child.text.strip()
    return fallback


@dataclass
class FeedItem:
    source_id: str
    title: str
    url: str
    summary: str = ""
    published: str = ""
    category: str = ""
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source(self) -> Source | None:
        return SOURCE_MAP.get(self.source_id)

    @property
    def source_label(self) -> str:
        source = self.source
        return source.name if source else self.source_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published": self.published,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeedItem":
        return cls(
            source_id=data.get("source_id", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            summary=data.get("summary", ""),
            published=data.get("published", ""),
            category=data.get("category", ""),
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, *names: str) -> str:
    for name in names:
        for child in node:
            if _local(child.tag) == name:
                if (child.text or "").strip():
                    return child.text.strip()
                href = child.attrib.get("href")
                if href:
                    return href.strip()
    return ""


def _parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        pass
    cleaned = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned).strftime("%Y-%m-%d")
    except ValueError:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        return match.group(1) if match else ""


def _sanitize_xml(xml_text: str) -> str:
    """把会让 ElementTree 报错的字符修掉：裸 & 与控制字符。"""
    text = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][\w.-]*;)", "&amp;", xml_text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text


def parse_feed(xml_text: str, source: Source) -> list[FeedItem]:
    # 有些站（Guideposts、eSchool News）在 <?xml ?> 前多出空行或 BOM，
    # ElementTree 会直接报 "XML or text declaration not at start"，
    # 这里统一清掉再解析。
    xml_text = (xml_text or "").lstrip("\ufeff \t\r\n")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        # 有些站（Literary Hub）正文里带裸 & 或控制字符，XML 不合法。
        # 修一遍再试一次；仍然失败才算这个源不可用。
        cleaned = _sanitize_xml(xml_text)
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            raise ValueError(f"RSS 解析失败：{exc}") from exc

    items: list[FeedItem] = []
    seen: set[str] = set()
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = normalize_space(html_lib.unescape(_child_text(node, "title")))
        link = _extract_link(node)
        if not link and node.attrib.get("about"):
            link = node.attrib["about"]
        link = html_lib.unescape(link).strip()
        if not link.startswith("http"):
            continue
        if link in seen:
            continue
        seen.add(link)
        if any(re.search(p, link, re.I) for p in SKIP_URL_PATTERNS):
            continue
        if not title or any(re.search(p, title, re.I) for p in SKIP_TITLE_PATTERNS):
            continue
        summary = ""
        for name in ("encoded", "content", "description", "summary"):
            raw = _child_text(node, name)
            if raw:
                summary = strip_tags(raw)
                if len(summary) > 60:
                    break
        published = _parse_date(
            _child_text(node, "pubdate", "published", "updated", "dc:date", "date")
        )
        items.append(
            FeedItem(
                source_id=source.id,
                title=clean_paragraph(title)[:200],
                url=link,
                summary=summary[:600],
                published=published,
                category=source.category,
            )
        )
    return items


def parse_listing(html_text: str, source: Source) -> list[FeedItem]:
    """从没有 RSS 的站点列表页抓文章链接。

    只认 ``link_pattern`` 命中的链接（例如国家地理的 ``/article/``），
    标题优先取 ``aria-label`` / ``data-track-story_title``——这类卡片式布局
    里 <a> 本身是空的，正文标题在属性上。
    """
    tree = htmltree.parse(html_text)
    pattern = re.compile(source.link_pattern or r"/article/", re.I)
    items: list[FeedItem] = []
    seen: set[str] = set()
    for node in tree.find_all("a"):
        href = (node.attr("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        href = urljoin(source.homepage, href).split("#", 1)[0]
        if not href.startswith("http") or not pattern.search(href):
            continue
        if href in seen or any(re.search(p, href, re.I) for p in SKIP_URL_PATTERNS):
            continue
        title = (
            node.attr("aria-label")
            or node.attr("data-track-story_title")
            or node.text()
        )
        title = normalize_space(html_lib.unescape(title))
        if not title or any(re.search(p, title, re.I) for p in SKIP_TITLE_PATTERNS):
            continue
        seen.add(href)
        items.append(
            FeedItem(
                source_id=source.id,
                title=clean_paragraph(title)[:200],
                url=href,
                category=source.category,
            )
        )
    return filter_items(items)


def filter_items(items: list[FeedItem]) -> list[FeedItem]:
    out: list[FeedItem] = []
    for item in items:
        if len(item.title) < 12 or len(item.title) > 180:
            continue
        if len(item.summary) > 20000:
            continue
        out.append(item)
    return out
