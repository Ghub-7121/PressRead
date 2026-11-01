"""极简 HTML 树。

标准库没有 DOM，用 HTMLParser 搭一棵够用的小树：按 class/id/标签检索、
取文本、找祖先。抓正文和解析牛津词典页都靠它。容错优先：标签不闭合也能用。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable, Iterable, Iterator

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "dd", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}

MAX_NODES = 120_000


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "raw")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list["Node | str"] = []
        self.parent = parent
        # script/style 的内容不进正文，但要留给 JSON-LD 之类的结构化数据用
        self.raw = ""

    # ---------------------------------------------------------------- 属性
    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    @property
    def id(self) -> str:
        return self.attrs.get("id", "")

    def has_class(self, name: str) -> bool:
        return name in self.classes

    # ---------------------------------------------------------------- 文本
    def own_text(self) -> str:
        return "".join(c for c in self.children if isinstance(c, str))

    def text(self, sep: str = " ") -> str:
        parts: list[str] = []

        def walk(node: "Node") -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag == "br":
                    parts.append(sep)
                else:
                    block = child.tag in BLOCK_TAGS
                    if block:
                        parts.append(sep)
                    walk(child)
                    if block:
                        parts.append(sep)

        walk(self)
        text = "".join(parts)
        text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
        text = re.sub(r" *\n+ *", "\n", text)
        return re.sub(r"\s+", " ", text).strip() if sep == " " else text.strip()

    def text_len(self) -> int:
        return len(self.text())

    # ---------------------------------------------------------------- 遍历
    def walk(self) -> Iterator["Node"]:
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            for child in reversed(node.children):
                if isinstance(child, Node):
                    stack.append(child)

    def find_all(
        self,
        tag: str | Iterable[str] | None = None,
        *,
        cls: str | None = None,
        id: str | None = None,  # noqa: A002 - 与 HTML 属性同名更直观
        pred: Callable[["Node"], bool] | None = None,
        limit: int | None = None,
    ) -> list["Node"]:
        tags = {tag} if isinstance(tag, str) else (set(tag) if tag else None)
        out: list[Node] = []
        for node in self.walk():
            if node is self:
                continue
            if tags and node.tag not in tags:
                continue
            if cls and not node.has_class(cls):
                continue
            if id and node.id != id:
                continue
            if pred and not pred(node):
                continue
            out.append(node)
            if limit and len(out) >= limit:
                break
        return out

    def first(self, tag: str | Iterable[str] | None = None, **kwargs) -> "Node | None":
        found = self.find_all(tag, limit=1, **kwargs)
        return found[0] if found else None

    def ancestors(self) -> Iterator["Node"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def remove(self) -> None:
        if self.parent:
            self.parent.children = [
                c for c in self.parent.children if not (isinstance(c, Node) and c is self)
            ]
            self.parent = None

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default) or default

    # ---------------------------------------------------------------- 链接密度
    def link_density(self) -> float:
        total = len(self.text())
        if total <= 0:
            return 0.0
        link_text = sum(len(a.text()) for a in self.find_all("a"))
        return min(1.0, link_text / total)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        cls = ".".join(sorted(self.classes)[:2])
        return f"<{self.tag}{'.' + cls if cls else ''} {len(self.text())}c>"


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]
        self.skip_depth = 0
        self.raw_sink: Node | None = None
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.count > MAX_NODES:
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        self.count += 1
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            if tag == "script":
                self.raw_sink = node
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.count > MAX_NODES:
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        self.count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            if tag == "script":
                self.raw_sink = None
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.skip_depth:
            # 内联脚本可能很大，只留前 300KB（JSON-LD 远小于这个量级）
            if self.raw_sink is not None and len(self.raw_sink.raw) < 300_000:
                self.raw_sink.raw += data
            return
        self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    try:
        builder.feed(html)
        builder.close()
    except Exception:
        # HTMLParser 对畸形内容偶尔会抛断言，保留已解析部分
        pass
    return builder.root


def meta_content(tree: Node, *, name: str | None = None, prop: str | None = None) -> str:
    for node in tree.find_all("meta"):
        if name and node.attr("name").lower() == name.lower():
            return node.attr("content")
        if prop and node.attr("property").lower() == prop.lower():
            return node.attr("content")
    return ""


def html_to_text(fragment: str) -> str:
    """去掉标签、还原实体，用于 RSS 摘要。"""
    import html as _html

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
