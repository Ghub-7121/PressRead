"""抓取编排：从多个外刊源里挑一篇、抽正文、切成可练习的段落。

策略（v1.2 起）
- 先看「预取缓存」：上一轮并发抓取中落选但已抓完的文章，1 小时内可直接用，
  命中时零网络开销。
- 没有缓存就拉各源文章列表：多源并发，凑够竞速所需数量即开跑，不等最慢的源。
- 正文阶段是**并发竞速**：同时向 N 个源发请求，谁先返回可用正文就用谁，
  主线程立即返回，不必等其余请求。
- 落选请求继续跑完，结果写进 prefetch_cache 热存 1 小时；下次换文章直接命中。
- 竞速有单篇超时和整批预算，全失败/超时退回离线示例，保证程序可用。
"""

from __future__ import annotations

import json
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .article import Article, extract_article
from .fetch import HttpClient
from .feeds import SOURCE_MAP, FeedItem, Source, filter_items, parse_feed, parse_listing
from . import paths


@dataclass
class PracticeUnit:
    article: Article
    index: int
    text: str
    sentences: list[str]
    # 本次练习覆盖的连续段数：设置「一次显示 3 段」时为 3，
    # 文章剩余不足 3 段时按实际段数记。
    span: int = 1
    # 每段第一句在 sentences 里的下标（[0, 4, 7] 表示第 4、7 句起是新的一段）。
    # 阅读页据此换行并做首行缩进；缺省视为整屏只有一段。
    para_starts: list[int] = field(default_factory=lambda: [0])
    bundled_translations: list[str] = field(default_factory=list)
    offline: bool = False

    @property
    def key(self) -> str:
        return f"{self.article.url}#{self.index}"


class AllSourcesFailed(Exception):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []


class ArticleService:
    def __init__(self, config, store, http: HttpClient):
        self.config = config
        self.store = store
        self.http = http
        self.rng = random.Random()
        # _seen：本次会话已经展示过的文章；_dead：抓取失败/正文为空的链接。
        # 落选但抓成功的文章两个都不进——它躺在预取缓存里，下次还要用。
        self._seen: set[str] = set()
        self._dead: set[str] = set()
        self._source_failures: dict[str, int] = {}
        self._last_errors: list[str] = []
        self._demo_cache: dict[str, Any] | None = None
        # 连点「换一篇」会叠加多个竞速批次，这里给正文请求设一个全局上限，
        # 避免把本机连接数或对方站点打满。
        self._net_slots = threading.Semaphore(12)

    # ------------------------------------------------------------- 抓取参数
    def _num(self, key: str, default: float, low: float, high: float) -> float:
        reader = getattr(self.config, "fetch_number", None)
        if callable(reader):
            return float(reader(key, default, low=low, high=high))
        # 配置对象没有该方法时（测试里的轻量替身）直接用默认值
        return default

    @property
    def race_width(self) -> int:
        return int(self._num("race_width", 5, low=1, high=12))

    @property
    def article_timeout(self) -> float:
        return self._num("article_timeout", 15, low=5, high=60)

    @property
    def race_budget(self) -> float:
        return self._num("race_budget_seconds", 16, low=3, high=120)

    @property
    def feed_budget(self) -> float:
        return self._num("feed_budget_seconds", 24, low=3, high=180)

    def prefetch_ttl(self) -> float:
        """预取结果的存活时长（默认 1 小时），与词典/翻译缓存无关。"""
        try:
            minutes = float(self.config.get("cache", "prefetch_ttl_minutes", default=60))
        except (TypeError, ValueError):
            minutes = 60
        return max(1.0, minutes) * 60

    # ------------------------------------------------------------------ 源列表
    def sources(self, only_enabled: bool = True) -> list[Source]:
        enabled = set(self.config.enabled_sources)
        items = [SOURCE_MAP[sid] for sid in enabled if sid in SOURCE_MAP]
        if not items:
            items = [SOURCE_MAP[sid] for sid in ("sciencenews", "wired", "npr")]
        return items

    def feed_ttl(self) -> float:
        try:
            minutes = float(self.config.get("cache", "feed_ttl_minutes", default=10))
        except (TypeError, ValueError):
            minutes = 10
        return max(1.0, minutes) * 60

    # ------------------------------------------------------------------ Feed
    def _load_feed(self, source: Source) -> list[FeedItem]:
        cached = self.store.get_feed(source.id, self.feed_ttl())
        if cached is not None:
            return [FeedItem.from_dict(x) for x in cached]
        if source.kind == "html":
            # 没有 RSS 的站点：抓栏目页，按链接规则挑文章
            text = self.http.get_text(
                source.feed_url,
                headers={"Accept": "text/html,application/xhtml+xml"},
                timeout=20,
            )
            items = parse_listing(text, source)
        else:
            text = self.http.get_text(
                source.feed_url,
                headers={"Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"},
                timeout=16,
            )
            items = filter_items(parse_feed(text, source))
        self.store.save_feed(source.id, [i.to_dict() for i in items])
        return items

    def fetch_all_feeds(
        self,
        only_enabled: bool = True,
        source_ids: list[str] | None = None,
        *,
        enough: int | None = None,
    ) -> tuple[dict[str, list[FeedItem]], list[str]]:
        """并发拉各源文章列表。

        `enough` 表示「已经拿到这么多个源就够用了」，命中即可先返回，不必等
        最慢的源；没跑完的线程仍在后台收尾并写入 feed 缓存，下次打开即命中。
        用 daemon 线程而不是线程池：程序退出时不会被卡住的请求拖住。
        """
        if source_ids:
            sources = [SOURCE_MAP[sid] for sid in source_ids if sid in SOURCE_MAP]
        else:
            sources = self.sources(only_enabled)
        if not sources:
            return {}, []

        workers = max(1, min(8, len(sources)))
        slots = threading.Semaphore(workers)
        box: queue.Queue = queue.Queue()
        budget = self.feed_budget

        def run(source: Source) -> None:
            if not slots.acquire(timeout=budget):
                box.put((source, None, ""))  # 没排上槽位，静默放弃
                return
            try:
                box.put((source, self._load_feed(source), None))
            except Exception as exc:  # 单源失败不影响整体
                box.put((source, None, getattr(exc, "message", str(exc))))
            finally:
                slots.release()

        for source in sources:
            threading.Thread(
                target=run, args=(source,), name=f"feed-{source.id}", daemon=True
            ).start()

        results: dict[str, list[FeedItem]] = {}
        errors: list[str] = []
        want = len(sources) if enough is None else max(1, min(int(enough), len(sources)))
        deadline = time.time() + budget
        received = 0
        while received < len(sources):
            remaining = deadline - time.time()
            if remaining <= 0:
                errors.append("部分外刊源列表响应超时，已先用先返回的结果")
                break
            try:
                source, items, error = box.get(timeout=remaining)
            except queue.Empty:
                errors.append("部分外刊源列表响应超时，已先用先返回的结果")
                break
            received += 1
            if error:
                errors.append(f"{source.name}：{error}")
                self._source_failures[source.id] = self._source_failures.get(source.id, 0) + 1
            elif items is not None:
                results[source.id] = items
            if len(results) >= want:
                break
        return results, errors

    # ------------------------------------------------------------------ 文章
    def _pick_items(
        self, feeds: dict[str, list[FeedItem]], preferred_source: str | None = None
    ) -> list[FeedItem]:
        pool: list[FeedItem] = []
        for source_id, items in feeds.items():
            if self._source_failures.get(source_id, 0) >= 3:
                continue
            candidates = [
                item for item in items
                if item.url not in self._seen and item.url not in self._dead
                and not self.store.article_known(item.url)
            ]
            if not candidates:
                candidates = [
                    item for item in items
                    if item.url not in self._seen and item.url not in self._dead
                ]
            self.rng.shuffle(candidates)
            pool.extend(candidates[:4])
        if preferred_source:
            preferred = [i for i in pool if i.source_id == preferred_source]
            others = [i for i in pool if i.source_id != preferred_source]
            self.rng.shuffle(others)
            pool = preferred + others
        else:
            self.rng.shuffle(pool)
        return pool

    def _extract(self, item: FeedItem, html_text: str) -> Article:
        source = SOURCE_MAP.get(item.source_id)
        article = extract_article(
            html_text,
            url=item.url,
            source_id=item.source_id,
            source_name=source.name if source else item.source_id,
            category=source.category if source else item.category,
            fallback_title=item.title,
            fallback_summary=item.summary,
        )
        article.fetched_at = time.time()
        return article

    def _commit(self, article: Article) -> None:
        """胜出的文章「转正」：进历史文章库，可在历史页回看。"""
        self._seen.add(article.url)
        try:
            self.store.save_article(article.to_dict())
        except Exception:
            pass  # 入库失败不该让本次阅读失败，正文已经在内存里

    def _cache_prefetch(self, article: Article) -> None:
        """抓完的文章先进预取缓存，1 小时内可直接复用。

        胜出的那篇也会写一条，与正式文章库重复；读取时按 article_known
        跳过，1 小时后随整表清理，代价只有几十 KB。
        """
        try:
            self.store.save_prefetch(article.to_dict())
        except Exception:
            pass  # 程序退出时数据库可能已关闭，忽略

    def _race_worker(self, item: FeedItem, box: queue.Queue) -> None:
        """单个候选的抓取线程：只抓取与解析，取舍交给主线程。

        无论是否被选中，只要拿到可用正文就写预取缓存——这正是「其余请求
        得到的部分保留 1 小时」的落地点。
        """
        if not self._net_slots.acquire(timeout=self.race_budget):
            box.put(("skip", "", item))
            return
        try:
            html_text = self.http.get_text(
                item.url,
                headers={"Accept": "text/html,application/xhtml+xml"},
                timeout=self.article_timeout,
            )
        except Exception as exc:
            self._dead.add(item.url)
            self._source_failures[item.source_id] = (
                self._source_failures.get(item.source_id, 0) + 1
            )
            message = getattr(exc, "message", str(exc))
            box.put(("err", f"{item.source_label}《{item.title[:26]}…》：{message}", item))
            return
        finally:
            self._net_slots.release()
        try:
            article = self._extract(item, html_text)
        except Exception:
            self._dead.add(item.url)
            box.put(("err", f"{item.source_label}《{item.title[:26]}…》：正文解析失败", item))
            return
        if not article.paragraphs:
            self._dead.add(item.url)
            box.put(("err", f"{item.source_label}《{item.title[:26]}…》：正文为空", item))
            return
        box.put(("ok", article, item))
        self._cache_prefetch(article)

    def _race(
        self, items: list[FeedItem], today: str
    ) -> tuple[PracticeUnit | None, list[str]]:
        """并发竞速抓正文：谁先返回可用结果就用谁。

        每个候选一个 daemon 线程，主线程按完成顺序取结果，第一个能切成练习
        段的立即返回（不等其余请求）。整批受 race_budget 约束，到点仍无可用
        结果就放弃，由上层走兜底。剩余线程仍在后台跑完并写预取缓存。
        """
        batch = items[: self.race_width]
        if not batch:
            return None, []

        box: queue.Queue = queue.Queue()
        for item in batch:
            threading.Thread(
                target=self._race_worker,
                args=(item, box),
                name=f"race-{item.source_id}",
                daemon=True,
            ).start()

        errors: list[str] = []
        budget = int(self.race_budget)
        deadline = time.time() + self.race_budget
        received = 0
        while received < len(batch):
            remaining = deadline - time.time()
            if remaining <= 0:
                errors.append(f"并发抓取 {budget} 秒内没有源返回可用正文")
                break
            try:
                kind, payload, item = box.get(timeout=remaining)
            except queue.Empty:
                errors.append(f"并发抓取 {budget} 秒内没有源返回可用正文")
                break
            received += 1
            if kind == "skip":
                errors.append("并发请求已达上限，部分候选被跳过")
                continue
            if kind != "ok":
                errors.append(str(payload))
                continue
            article: Article = payload
            unit = self._unit_from_article(article, today)
            if unit is None:
                errors.append(f"{item.source_label}《{item.title[:26]}…》：正文段落过短，跳过")
                continue
            self._commit(article)
            return unit, errors
        return None, errors

    def _unit_from_prefetch(self, today: str) -> PracticeUnit | None:
        """从预取缓存取一篇：上一轮竞速落选、但已经抓完的文章。

        命中即零网络开销。超过 TTL 的条目由 store.prefetch_recent 顺手清掉，
        只影响 prefetch_cache 这一张表。
        """
        try:
            rows = self.store.prefetch_recent(self.prefetch_ttl(), limit=16)
        except Exception:
            return None
        if not rows:
            return None
        self.rng.shuffle(rows)
        for row in rows:
            url = str(row.get("url") or "")
            if not url or url in self._seen or url in self._dead:
                continue
            try:
                if self.store.article_known(url):
                    continue
                article = Article.from_row(row)
            except Exception:
                continue
            article.fetched_at = time.time()
            unit = self._unit_from_article(article, today)
            if unit is None:
                continue
            self._seen.add(url)
            self._commit(article)
            return unit
        return None

    def _unit_from_article(self, article: Article, today: str) -> PracticeUnit | None:
        units = article.practice_units()
        if not units:
            return None
        size = self.group_size()
        aid = self._article_id(article)
        done = {
            (p["article_id"], p["para_index"])
            for p in self.store.day_practices(today)
            if p.get("status") == "done"
        }
        fresh = [
            pos for pos, u in enumerate(units)
            if (aid, int(u["index"])) not in done
        ]
        positions = fresh or list(range(len(units)))
        # 起点要留够 size 段，否则从中间开始永远凑不满；文章本来就短时取全部
        limit = max(0, len(units) - size)
        usable = [pos for pos in positions if pos <= limit] or positions
        best_pos = max(usable, key=lambda pos: units[pos]["score"])
        group = self._merge_units(units, best_pos, size)
        if group is None:
            return None
        return PracticeUnit(
            article=article,
            index=group["index"],
            text=group["text"],
            sentences=group["sentences"],
            span=group["span"],
            para_starts=group["para_starts"],
        )

    def group_size(self) -> int:
        """一次练习抽取的连续段数（设置项，1–10）。"""
        try:
            return int(self.config.paragraphs_per_unit)
        except AttributeError:  # 测试里的假配置可能只实现了 get()
            try:
                return max(
                    1, min(10, int(self.config.get("practice", "paragraphs_per_unit", default=1)))
                )
            except (TypeError, ValueError):
                return 1

    @staticmethod
    def _merge_units(units: list[dict], position: int, count: int) -> dict | None:
        """从 units[position] 起合并 count 段，走到末尾不足时取剩下的全部。

        合并后的 index 是起始段序号，span 是实际覆盖段数——「若不足这些段
        则取最多段」就落在这个 span 上。
        """
        if not 0 <= position < len(units):
            return None
        take = units[position: position + max(1, count)]
        if not take:
            return None
        # 合并后每段第一句的序号，阅读页靠它换行 + 首行缩进
        starts: list[int] = []
        cursor = 0
        for unit in take:
            starts.append(cursor)
            cursor += len(unit.get("sentences") or [])
        return {
            "index": int(take[0]["index"]),
            "span": len(take),
            "text": "\n\n".join(str(u["text"]) for u in take),
            "sentences": [s for u in take for s in (u["sentences"] or [])],
            "translations": [t for u in take for t in (u.get("translations") or [])],
            "para_starts": starts or [0],
        }

    def unit_at(self, article: Article, index: int) -> PracticeUnit | None:
        """按起始段序号拼一个练习单元；序号越界返回 None（调用方换文章）。

        「换一段」按钮走这里：index 是当前段的起始序号 + span，因此一次显示
        多段时不会重复显示已经看过的段。
        """
        if article.offline or article.url.startswith("pressread://"):
            return self.demo_unit_at(index)
        units = article.practice_units()
        position = next(
            (pos for pos, u in enumerate(units) if int(u["index"]) == int(index)), None
        )
        if position is None:
            return None
        group = self._merge_units(units, position, self.group_size())
        if group is None:
            return None
        return PracticeUnit(
            article=article,
            index=group["index"],
            text=group["text"],
            sentences=group["sentences"],
            span=group["span"],
            para_starts=group["para_starts"],
        )

    @staticmethod
    def _article_id(article: Article) -> str:
        from .store import make_id

        return make_id(article.url)

    def next_unit(
        self,
        *,
        today: str,
        preferred_source: str | None = None,
        offline_first: bool = False,
    ) -> PracticeUnit:
        if offline_first:
            return self.load_demo_unit()

        errors: list[str] = []

        # ① 上一轮竞速的落选结果还在有效期内：直接用，零网络
        cached = self._unit_from_prefetch(today)
        if cached is not None:
            self._last_errors = errors
            return cached

        # ② 拉各源文章列表；够竞速用的源数就先开跑，不等最慢的源
        feeds, feed_errors = self.fetch_all_feeds(enough=max(3, self.race_width))
        errors.extend(feed_errors)
        items = self._pick_items(feeds, preferred_source)
        if not items:
            errors.append(
                "所有外刊源都没返回文章列表" if not feeds else "所有源的候选文章都已读过"
            )
            return self._fallback(errors)

        # ③ 并发竞速：谁先返回可用正文就用谁
        unit, race_errors = self._race(items, today)
        errors.extend(race_errors)
        if unit is not None:
            self._last_errors = errors
            return unit

        # ④ 竞速超时后，后台线程可能刚好把结果写进预取缓存，再看一眼
        cached = self._unit_from_prefetch(today)
        if cached is not None:
            self._last_errors = errors
            return cached

        return self._fallback(errors)

    def _fallback(self, errors: list[str]) -> PracticeUnit:
        if self.config.get("ui", "show_demo_on_failure", default=True):
            unit = self.load_demo_unit()
            unit.offline = True
            self._last_errors = errors
            return unit
        raise AllSourcesFailed("暂时抓不到新文章，请检查网络后重试", errors)

    def _demo_data(self) -> dict[str, Any]:
        if self._demo_cache is None:
            path = paths.resource_path("data", "demo_article.json")
            self._demo_cache = json.loads(path.read_text(encoding="utf-8"))
        return self._demo_cache

    def _demo_article(self, data: dict[str, Any]) -> Article:
        return Article(
            url="pressread://demo",
            source_id="demo",
            source_name=data.get("source_name", "离线示例"),
            category=data.get("category", "科学"),
            title=data.get("title", "离线示例文章"),
            paragraphs=[p["text"] for p in data["paragraphs"]],
            author=data.get("author", ""),
            published=data.get("published", ""),
            offline=True,
        )

    def load_demo_unit(self) -> PracticeUnit:
        return self.demo_unit_at(0)

    def demo_unit_at(self, index: int, *, size: int | None = None) -> PracticeUnit:
        data = self._demo_data()
        units = [
            {
                "index": pos,
                "text": p["text"],
                "sentences": list(p.get("sentences") or []),
                "translations": list(p.get("translations") or []),
            }
            for pos, p in enumerate(data["paragraphs"])
        ]
        position = max(0, min(int(index), len(units) - 1))
        group = self._merge_units(
            units, position, self.group_size() if size is None else size
        )
        if group is None:  # 空文件之类的异常情况，退回第一段
            group = self._merge_units(units, 0, 1)
        article = self._demo_article(data)
        return PracticeUnit(
            article=article,
            index=group["index"],
            text=group["text"],
            sentences=group["sentences"],
            span=group["span"],
            para_starts=group["para_starts"],
            bundled_translations=group["translations"],
            offline=True,
        )

    def load_by_url(self, url: str) -> Article | None:
        """历史记录回看：从库里找已抓过的文章。"""
        from .store import make_id

        row = self.store.get_article(make_id(url))
        if not row:
            return None
        return Article.from_row(row)

    def last_errors(self) -> list[str]:
        return list(self._last_errors)
