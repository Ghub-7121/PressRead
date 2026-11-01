"""文章抽取：从任意新闻页里找出正文段落。

没有第三方 readability 依赖，用一个「段落长度 + 容器投票」的轻量算法：
每个 <p> 给父级和祖父级加分，得分最高的容器就是正文区；再用链接密度、
样板句过滤掉导航、订阅提示和图片说明。抽不到时退回 JSON-LD 的 articleBody。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import htmltree
from .htmltree import Node
from .textproc import (
    clean_paragraph, is_boilerplate, level_estimate, normalize_space,
    practice_score, split_long_paragraph, split_sentences, word_count,
)

DROP_ANCESTORS = {"nav", "header", "footer", "aside", "form"}


@dataclass
class Article:
    url: str
    source_id: str
    source_name: str
    category: str
    title: str
    paragraphs: list[str]
    author: str = ""
    published: str = ""
    word_count: int = 0
    fetched_at: float = 0.0
    offline: bool = False

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = sum(word_count(p) for p in self.paragraphs)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "category": self.category,
            "title": self.title,
            "paragraphs": self.paragraphs,
            "author": self.author,
            "published": self.published,
            "word_count": self.word_count,
            "fetched_at": self.fetched_at,
            "offline": self.offline,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Article":
        return cls(
            url=row.get("url", ""),
            source_id=row.get("source_id", ""),
            source_name=row.get("source_name", ""),
            category=row.get("category", ""),
            title=row.get("title", ""),
            paragraphs=list(row.get("paragraphs", [])),
            author=row.get("author", ""),
            published=row.get("published", ""),
            word_count=int(row.get("word_count", 0) or 0),
            fetched_at=float(row.get("fetched_at", 0) or 0),
        )

    def practice_units(self) -> list[dict[str, Any]]:
        """合并短段落，切成 45-130 词的练习块，更接近真实的翻译工作量。"""
        chunks: list[str] = []
        for paragraph in self.paragraphs:
            chunks.extend(split_long_paragraph(paragraph, 780))

        merged: list[str] = []
        buffer = ""
        for chunk in chunks:
            words = word_count(chunk)
            if not chunk:
                continue
            if buffer and (word_count(buffer) + words > 130 or word_count(buffer) >= 45):
                merged.append(buffer)
                buffer = chunk
            else:
                buffer = f"{buffer} {chunk}".strip()
        if buffer:
            merged.append(buffer)

        units: list[dict[str, Any]] = []
        for chunk in merged:
            if len(chunk) < 90:
                continue
            sentences = split_sentences(chunk)
            if not sentences:
                continue
            units.append(
                {
                    "index": len(units),
                    "text": chunk,
                    "sentences": sentences,
                    "score": practice_score(chunk),
                    "words": word_count(chunk),
                    "level": level_estimate(chunk),
                }
            )
        return units


CAPTION_CLASSES = {"caption", "caption-wrap", "credit-caption", "figure", "image", "img-wrap"}


def _in_drop_zone(node: Node) -> bool:
    if node.classes & CAPTION_CLASSES:  # 段落自己就带 caption/credit 类
        return True
    for ancestor in node.ancestors():
        if ancestor.tag in DROP_ANCESTORS or ancestor.tag == "figure":
            return True
        if ancestor.classes & CAPTION_CLASSES:
            return True
    return False


def _collect_paragraphs(container: Node) -> list[str]:
    out: list[str] = []
    for p in container.find_all("p"):
        text = clean_paragraph(p.text())
        if len(text) < 60 or is_boilerplate(text):
            continue
        if p.link_density() > 0.45:
            continue
        if _in_drop_zone(p):
            continue
        out.append(text)
    return out


def _score_containers(tree: Node) -> Node | None:
    scores: dict[int, float] = {}
    nodes: dict[int, Node] = {}
    for p in tree.find_all("p"):
        if _in_drop_zone(p):
            continue
        text = p.text()
        length = len(text)
        if length < 60 or is_boilerplate(text):
            continue
        node = p.parent
        weight = 1.0
        for _ in range(3):
            if node is None or node.tag in ("#document", "html"):
                break
            key = id(node)
            scores[key] = scores.get(key, 0.0) + length * weight
            nodes[key] = node
            node = node.parent
            weight *= 0.55
    best_key = None
    best_score = -1.0
    for key, score in scores.items():
        node = nodes[key]
        if node.tag in ("body", "html", "#document"):
            continue
        if node.link_density() > 0.35:
            continue
        # 段落数量太少时降权，避免选到单段落的侧栏
        count = len(node.find_all("p"))
        adjusted = score * min(1.0, 0.35 + count / 4)
        if adjusted > best_score:
            best_score = adjusted
            best_key = key
    return nodes.get(best_key) if best_key is not None else None


def _jsonld_article(tree: Node) -> tuple[str, str, str]:
    """返回 (headline, author, articleBody)。"""
    for script in tree.find_all("script"):
        if "ld+json" not in script.attr("type").lower():
            continue
        raw = (script.own_text() or script.raw).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            candidates = list(data["@graph"]) + candidates
        for item in candidates:
            if not isinstance(item, dict):
                continue
            body = item.get("articleBody") or item.get("text")
            if isinstance(body, str) and len(body) > 300:
                author = item.get("author") or ""
                if isinstance(author, list):
                    author = ", ".join(
                        a.get("name", "") if isinstance(a, dict) else str(a) for a in author
                    )
                elif isinstance(author, dict):
                    author = author.get("name", "")
                headline = item.get("headline") or item.get("name") or ""
                return str(headline), str(author), body
    return "", "", ""


def _split_body(body: str) -> list[str]:
    body = body.replace("\r\n", "\n")
    raw_parts = [p for p in re.split(r"\n\s*\n+", body) if p.strip()]
    if len(raw_parts) <= 1:
        raw_parts = [p for p in body.split("\n") if len(p.strip()) > 80]
    paragraphs: list[str] = []
    for part in raw_parts:
        for chunk in split_long_paragraph(clean_paragraph(part), 900):
            if len(chunk) >= 90 and not is_boilerplate(chunk):
                paragraphs.append(chunk)
    return paragraphs


KNOWN_SITE_SUFFIXES = {
    "npr", "wired", "science news", "smithsonian magazine", "quanta magazine",
    "mit technology review", "nature", "the guardian", "bbc news", "bbc",
    "the economist", "the wall street journal", "wsj", "france 24", "un news",
    "science", "phys.org",
}


def _clean_title(title: str, source_name: str) -> str:
    title = normalize_space(title)
    for sep in (" | ", " – ", " — ", " - "):
        if sep not in title:
            continue
        head, tail = title.rsplit(sep, 1)
        suffix = tail.strip()
        lowered = suffix.lower()
        known = (
            lowered == source_name.lower()
            or lowered in KNOWN_SITE_SUFFIXES
            or source_name.lower().startswith(lowered)
            or lowered.startswith(source_name.lower())
        )
        if known and len(head.strip()) >= 8:
            title = head.strip()
            break
    return title.strip()


def extract_article(
    html_text: str,
    *,
    url: str,
    source_id: str,
    source_name: str,
    category: str = "",
    fallback_title: str = "",
    fallback_summary: str = "",
) -> Article:
    tree = htmltree.parse(html_text)
    title = (
        htmltree.meta_content(tree, prop="og:title")
        or htmltree.meta_content(tree, name="twitter:title")
        or (tree.first("title").text() if tree.first("title") else "")
        or fallback_title
    )
    title = _clean_title(title, source_name) or fallback_title

    author = (
        htmltree.meta_content(tree, name="author")
        or htmltree.meta_content(tree, prop="article:author")
        or ""
    )
    if not author:
        for node in tree.find_all(cls="byline", limit=3):
            text = node.text()
            if 3 <= len(text) <= 80:
                author = text
                break
    author = re.sub(r"^\s*by\s+", "", author.strip(), flags=re.I)

    published = (
        htmltree.meta_content(tree, prop="article:published_time")
        or htmltree.meta_content(tree, name="publish-date")
        or htmltree.meta_content(tree, name="date")
        or ""
    )
    if not published:
        time_node = tree.first("time")
        if time_node:
            published = time_node.attr("datetime") or time_node.text()
    published = published[:10] if published else ""

    best = _score_containers(tree)
    paragraphs = _collect_paragraphs(best) if best is not None else []

    if len(paragraphs) < 2:
        headline, ld_author, body = _jsonld_article(tree)
        if body:
            paragraphs = _split_body(body)
            # 走到这里通常是 JS 渲染页，<title> 往往是站点名或栏目名，
            # 结构化数据里的 headline 更可信。
            structured = _clean_title(headline, source_name)
            if len(structured) >= 8:
                title = structured
            author = author or ld_author

    if len(paragraphs) < 2:
        all_paragraphs = _collect_paragraphs(tree)
        if len(all_paragraphs) > len(paragraphs):
            paragraphs = all_paragraphs

    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for paragraph in paragraphs:
        key = paragraph.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append(paragraph)

    if not unique and fallback_summary:
        summary = clean_paragraph(fallback_summary)
        if len(summary) >= 80:
            unique = [summary]

    if unique:
        # 结尾常混着「订阅/作者简介」，砍掉最后 1-2 段明显的样板
        while len(unique) > 2 and is_boilerplate(unique[-1]):
            unique.pop()

    return Article(
        url=url,
        source_id=source_id,
        source_name=source_name,
        category=category,
        title=title or fallback_title or "未命名文章",
        paragraphs=unique,
        author=author,
        published=published,
    )


def metadata_only(
    *, url: str, source_id: str, source_name: str, category: str,
    title: str, summary: str,
) -> Article:
    """正文抓取失败时的最小可用文章（只用 RSS 摘要）。"""
    text = clean_paragraph(summary)
    paragraphs = [text] if len(text) >= 90 and not is_boilerplate(text) else []
    return Article(
        url=url,
        source_id=source_id,
        source_name=source_name,
        category=category,
        title=title,
        paragraphs=paragraphs,
    )
