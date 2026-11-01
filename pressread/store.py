"""SQLite 存储：查询缓存、文章、每日练习、导出记录、生词本。

设计要点
- 单连接 + 可重入锁：tkinter 主线程与后台抓取线程都会访问，sqlite3 连接本身
  不是线程安全的，这里用锁把每次读写包住；库很小，串行足够快。
- 缓存以「规范化后的查询串」为主键，命中时累加 hits，UI 可展示「已复用」。
- 每日练习以 (day, article_id, para_index) 唯一，重复打开只更新不新增。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
# 词典/译文缓存格式版本。改词典源或翻译链路后 +1，启动时会自动清空
# 「查词 + 译文 + 预取正文」三类缓存，避免旧结果继续冒充新来源。
# 只动这三张表相关的行，练习记录、生词本、导出记录、历史文章都不受影响。
CACHE_VERSION = 3


def _now() -> float:
    return time.time()


def _day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts if ts is not None else _now()))


def make_id(text: str, size: int = 14) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:size]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()

    # ------------------------------------------------------------------ 基础
    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS cache (
                   key TEXT PRIMARY KEY,
                   kind TEXT NOT NULL,
                   query TEXT NOT NULL,
                   provider TEXT NOT NULL DEFAULT '',
                   payload TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   last_used REAL NOT NULL,
                   hits INTEGER NOT NULL DEFAULT 0
               )"""
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cache_kind ON cache(kind)")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS articles (
                   id TEXT PRIMARY KEY,
                   url TEXT UNIQUE NOT NULL,
                   source_id TEXT NOT NULL,
                   source_name TEXT NOT NULL,
                   title TEXT NOT NULL,
                   author TEXT DEFAULT '',
                   published TEXT DEFAULT '',
                   category TEXT DEFAULT '',
                   paragraphs TEXT NOT NULL,
                   word_count INTEGER DEFAULT 0,
                   fetched_at REAL NOT NULL,
                   last_used REAL DEFAULT 0
               )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS feed_cache (
                   source_id TEXT PRIMARY KEY,
                   fetched_at REAL NOT NULL,
                   body TEXT NOT NULL
               )"""
        )
        # 并发抓取正文时的「落选」结果：谁先返回谁被采用，其余仍可能抓完，
        # 先放这里热存 1 小时。刻意单独建表——清理时只需 DELETE 本表，
        # 绝不会碰到 cache（词典/翻译）、articles（历史文章）、feed_cache
        # 或 practice（每日练习）。
        cur.execute(
            """CREATE TABLE IF NOT EXISTS prefetch_cache (
                   url TEXT PRIMARY KEY,
                   source_id TEXT NOT NULL DEFAULT '',
                   source_name TEXT NOT NULL DEFAULT '',
                   category TEXT NOT NULL DEFAULT '',
                   title TEXT NOT NULL DEFAULT '',
                   payload TEXT NOT NULL,
                   fetched_at REAL NOT NULL
               )"""
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_prefetch_time ON prefetch_cache(fetched_at)"
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS practice (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   day TEXT NOT NULL,
                   article_id TEXT NOT NULL,
                   para_index INTEGER NOT NULL,
                   para_count INTEGER NOT NULL DEFAULT 1,
                   source_name TEXT DEFAULT '',
                   title TEXT DEFAULT '',
                   text TEXT NOT NULL,
                   my_translation TEXT DEFAULT '',
                   refs TEXT DEFAULT '[]',
                   status TEXT NOT NULL DEFAULT 'active',
                   created_at REAL NOT NULL,
                   updated_at REAL NOT NULL,
                   completed_at REAL DEFAULT 0,
                   UNIQUE(day, article_id, para_index)
               )"""
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_practice_day ON practice(day)")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS exports (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   version TEXT NOT NULL,
                   day TEXT NOT NULL,
                   path TEXT NOT NULL,
                   fmt TEXT NOT NULL,
                   paragraphs INTEGER NOT NULL,
                   created_at REAL NOT NULL
               )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS words (
                   word TEXT PRIMARY KEY,
                   entry TEXT NOT NULL,
                   source TEXT DEFAULT '',
                   added_at REAL NOT NULL
               )"""
        )
        # v1.5 起一次练习可以覆盖多段，按实际段数计进度。
        # 老库没有这一列，补上并默认 1（历史记录都是单段）。
        columns = {row["name"] for row in cur.execute("PRAGMA table_info(practice)")}
        if "para_count" not in columns:
            cur.execute(
                "ALTER TABLE practice ADD COLUMN para_count INTEGER NOT NULL DEFAULT 1"
            )
        cur.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        # 缓存格式升级：旧词典（英英）与旧译文（换翻译源之前）全部作废。
        # 这里只 DELETE cache 与 prefetch_cache，练习/生词/导出/文章一律不动。
        row = cur.execute("SELECT value FROM meta WHERE key='cache_version'").fetchone()
        current = row["value"] if row else ""
        if current != str(CACHE_VERSION):
            cur.execute("DELETE FROM cache")
            cur.execute("DELETE FROM prefetch_cache")
            cur.execute(
                "INSERT INTO meta(key, value) VALUES('cache_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(CACHE_VERSION),),
            )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ meta
    def meta_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ----------------------------------------------------------------- cache
    @staticmethod
    def cache_key(kind: str, query: str, scope: str = "") -> str:
        norm = " ".join(query.split()).strip().lower()
        raw = f"{kind}|{scope}|{norm}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def cache_get(
        self, kind: str, query: str, scope: str = "", max_age: float | None = None
    ) -> dict[str, Any] | None:
        key = self.cache_key(kind, query, scope)
        with self._lock:
            row = self._conn.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            if max_age is not None and _now() - float(row["created_at"]) > max_age:
                return None  # 过期条目留着给「清理过期」删，但不再当作命中
            self._conn.execute(
                "UPDATE cache SET hits=hits+1, last_used=? WHERE key=?", (_now(), key)
            )
            self._conn.commit()
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            return None
        return {
            "payload": payload,
            "provider": row["provider"],
            "created_at": row["created_at"],
            "hits": row["hits"] + 1,
        }

    def cache_put(
        self,
        kind: str,
        query: str,
        payload: dict[str, Any],
        provider: str = "",
        scope: str = "",
    ) -> None:
        key = self.cache_key(kind, query, scope)
        now = _now()
        body = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO cache(key, kind, query, provider, payload, created_at, last_used, hits) "
                "VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(key) DO UPDATE SET "
                "payload=excluded.payload, provider=excluded.provider, created_at=excluded.created_at, "
                "last_used=excluded.last_used",
                (key, kind, query, provider, body, now, now),
            )
            self._conn.commit()

    def cache_purge(self, max_age_seconds: int | None = None) -> int:
        with self._lock:
            if max_age_seconds is None:
                cur = self._conn.execute("DELETE FROM cache")
            else:
                cutoff = _now() - max_age_seconds
                cur = self._conn.execute(
                    "DELETE FROM cache WHERE created_at < ?", (cutoff,)
                )
            self._conn.commit()
            return cur.rowcount

    def cache_stats(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n, SUM(hits) AS hits FROM cache GROUP BY kind"
            ).fetchall()
        counts = {r["kind"]: r["n"] for r in rows}
        hits = sum((r["hits"] or 0) for r in rows)
        return {
            "by_kind": counts,
            "total": sum(counts.values()),
            "hits": hits,
            "words": counts.get("word", 0),
            "texts": counts.get("translation", 0),
        }

    # -------------------------------------------------------------- articles
    def save_article(self, article: dict[str, Any]) -> str:
        aid = make_id(article["url"])
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO articles(id, url, source_id, source_name, title, author, published,
                                        category, paragraphs, word_count, fetched_at, last_used)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                       title=excluded.title, author=excluded.author, published=excluded.published,
                       paragraphs=excluded.paragraphs, word_count=excluded.word_count,
                       fetched_at=excluded.fetched_at, last_used=excluded.last_used""",
                (
                    aid,
                    article["url"],
                    article.get("source_id", ""),
                    article.get("source_name", ""),
                    article.get("title", ""),
                    article.get("author", ""),
                    article.get("published", ""),
                    article.get("category", ""),
                    json.dumps(article.get("paragraphs", []), ensure_ascii=False),
                    int(article.get("word_count", 0)),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return aid

    def article_known(self, url: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone()
        return bool(row)

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE articles SET last_used=? WHERE id=?", (_now(), article_id)
            )
            self._conn.commit()
        data = dict(row)
        data["paragraphs"] = json.loads(data["paragraphs"])
        return data

    def article_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()
        return int(row["n"])

    # ------------------------------------------------------------ feed cache
    def save_feed(self, source_id: str, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO feed_cache(source_id, fetched_at, body) VALUES(?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET fetched_at=excluded.fetched_at, body=excluded.body",
                (source_id, _now(), json.dumps(items, ensure_ascii=False)),
            )
            self._conn.commit()

    def get_feed(self, source_id: str, max_age_seconds: float) -> list[dict[str, Any]] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM feed_cache WHERE source_id=?", (source_id,)
            ).fetchone()
        if not row:
            return None
        if _now() - row["fetched_at"] > max_age_seconds:
            return None
        try:
            return json.loads(row["body"])
        except ValueError:
            return None

    # -------------------------------------------------------- prefetch cache
    # 独立的表，与上面的通用 cache 完全隔离：任何清理动作都只作用于
    # prefetch_cache，不会影响词典/翻译缓存和其他业务数据。
    def save_prefetch(self, article: dict[str, Any], fetched_at: float | None = None) -> None:
        """保存一篇「并发抓取中落选但仍抓完」的文章，供 1 小时内直接复用。

        时间戳优先用显式参数，其次用文章自带的 fetched_at，都没有才取当下。
        """
        raw = fetched_at if fetched_at is not None else article.get("fetched_at")
        try:
            ts = float(raw)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            ts = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO prefetch_cache(url, source_id, source_name, category,
                                              title, payload, fetched_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                       payload=excluded.payload, fetched_at=excluded.fetched_at""",
                (
                    article.get("url", ""),
                    article.get("source_id", ""),
                    article.get("source_name", ""),
                    article.get("category", ""),
                    article.get("title", ""),
                    json.dumps(article, ensure_ascii=False),
                    ts,
                ),
            )
            self._conn.commit()

    def prefetch_recent(self, max_age_seconds: float, limit: int = 16) -> list[dict[str, Any]]:
        """取未过期的预取结果（新→旧），并顺手删掉过期行。

        过期删除同样只针对 prefetch_cache；不必等调用方再发一次清理请求。
        """
        cutoff = _now() - max_age_seconds
        with self._lock:
            self._conn.execute(
                "DELETE FROM prefetch_cache WHERE fetched_at < ?", (cutoff,)
            )
            rows = self._conn.execute(
                "SELECT url, payload FROM prefetch_cache "
                "WHERE fetched_at >= ? ORDER BY fetched_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            self._conn.commit()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["payload"])
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("url"):
                out.append(item)
        return out

    def prefetch_purge(self, max_age_seconds: float | None = None) -> int:
        """清理预取缓存。不传 max_age 表示清空（程序启动时用）。

        只 DELETE prefetch_cache，其它表一律不碰。
        """
        with self._lock:
            if max_age_seconds is None:
                cur = self._conn.execute("DELETE FROM prefetch_cache")
            else:
                cur = self._conn.execute(
                    "DELETE FROM prefetch_cache WHERE fetched_at < ?",
                    (_now() - max_age_seconds,),
                )
            self._conn.commit()
            return cur.rowcount

    def prefetch_stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MIN(fetched_at) AS oldest FROM prefetch_cache"
            ).fetchone()
        return {"count": int(row["n"] or 0), "oldest": row["oldest"] or 0.0}

    # -------------------------------------------------------------- practice
    def ensure_practice(
        self, day: str, article_id: str, para_index: int, text: str,
        source_name: str = "", title: str = "", para_count: int = 1,
    ) -> dict[str, Any]:
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO practice(day, article_id, para_index, para_count,
                                                   source_name, title, text, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (day, article_id, para_index, max(1, int(para_count)),
                 source_name, title, text, now, now),
            )
            # 未完成的段落被「一次显示 N 段」重新抽到时，段数与正文要跟着对齐；
            # 已完成的不动，否则历史记录会被后来的设置改写。
            self._conn.execute(
                "UPDATE practice SET para_count=?, text=? WHERE day=? AND article_id=? "
                "AND para_index=? AND status='active' AND (para_count<>? OR text<>?)",
                (max(1, int(para_count)), text, day, article_id, para_index,
                 max(1, int(para_count)), text),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM practice WHERE day=? AND article_id=? AND para_index=?",
                (day, article_id, para_index),
            ).fetchone()
        return self._practice_row(row)

    def _practice_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        try:
            data["refs"] = json.loads(data.get("refs") or "[]")
        except ValueError:
            data["refs"] = []
        return data

    def get_practice(self, practice_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM practice WHERE id=?", (practice_id,)
            ).fetchone()
        return self._practice_row(row) if row else None

    def find_practice(self, day: str, article_id: str, para_index: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM practice WHERE day=? AND article_id=? AND para_index=?",
                (day, article_id, para_index),
            ).fetchone()
        return self._practice_row(row) if row else None

    def update_practice(self, practice_id: int, **fields: Any) -> None:
        allowed = {"my_translation", "refs", "status", "completed_at", "text"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key}=?")
            values.append(json.dumps(value, ensure_ascii=False) if key == "refs" else value)
        if not sets:
            return
        sets.append("updated_at=?")
        values.append(_now())
        values.append(practice_id)
        with self._lock:
            self._conn.execute(f"UPDATE practice SET {', '.join(sets)} WHERE id=?", values)
            self._conn.commit()

    def complete_practice(self, practice_id: int, my_translation: str, refs: list[str]) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE practice SET status='done', my_translation=?, refs=?, "
                "completed_at=?, updated_at=? WHERE id=?",
                (my_translation, json.dumps(refs, ensure_ascii=False), now, now, practice_id),
            )
            self._conn.commit()

    def day_practices(self, day: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM practice WHERE day=? ORDER BY id", (day,)
            ).fetchall()
        return [self._practice_row(r) for r in rows]

    def day_done_count(self, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(para_count), 0) AS n FROM practice "
                "WHERE day=? AND status='done'",
                (day,),
            ).fetchone()
        return int(row["n"] or 0)

    def day_done(self, day: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM practice WHERE day=? AND status='done' ORDER BY completed_at",
                (day,),
            ).fetchall()
        return [self._practice_row(r) for r in rows]

    def recent_practices(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM practice ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._practice_row(r) for r in rows]

    def active_days(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT day FROM practice WHERE status='done' ORDER BY day DESC"
            ).fetchall()
        return [r["day"] for r in rows]

    # ----------------------------------------------------------------- words
    def add_word(self, word: str, entry: dict[str, Any], source: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO words(word, entry, source, added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(word) DO UPDATE SET entry=excluded.entry, source=excluded.source",
                (word.lower(), json.dumps(entry, ensure_ascii=False), source, _now()),
            )
            self._conn.commit()

    def remove_word(self, word: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM words WHERE word=?", (word.lower(),))
            self._conn.commit()

    def is_saved_word(self, word: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM words WHERE word=?", (word.lower(),)
            ).fetchone()
        return bool(row)

    def saved_words(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM words ORDER BY added_at DESC").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["entry"] = json.loads(item["entry"])
            except ValueError:
                item["entry"] = {}
            out.append(item)
        return out

    # --------------------------------------------------------------- exports
    def record_export(self, version: str, day: str, path: str, fmt: str, paragraphs: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO exports(version, day, path, fmt, paragraphs, created_at) VALUES(?,?,?,?,?,?)",
                (version, day, path, fmt, paragraphs, _now()),
            )
            self._conn.commit()

    def export_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM exports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 统计
    def stats(self) -> dict[str, Any]:
        with self._lock:
            done = self._conn.execute(
                "SELECT COALESCE(SUM(para_count), 0) AS n FROM practice WHERE status='done'"
            ).fetchone()["n"]
            days = self._conn.execute(
                "SELECT COUNT(DISTINCT day) AS n FROM practice WHERE status='done'"
            ).fetchone()["n"]
            words = self._conn.execute("SELECT COUNT(*) AS n FROM words").fetchone()["n"]
            articles = self._conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
            lookups = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cache"
            ).fetchone()["n"]
            hits = self._conn.execute("SELECT SUM(hits) AS n FROM cache").fetchone()["n"] or 0
        total = lookups + hits
        return {
            "paragraphs": int(done),
            "days": int(days),
            "saved_words": int(words),
            "articles": int(articles),
            "queries": int(total),
            "cache_hits": int(hits),
            "hit_rate": (hits / total) if total else 0.0,
        }

    def streak(self, goal: int, today: str | None = None) -> int:
        """连续完成每日目标的天数（今天没完成时从昨天往前数）。"""
        today = today or _day()
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, COALESCE(SUM(para_count), 0) AS n FROM practice "
                "WHERE status='done' GROUP BY day"
            ).fetchall()
        counts = {r["day"]: r["n"] for r in rows}
        if counts.get(today, 0) < goal:
            # 从昨天开始算，今天仍有机会
            start = time.mktime(time.strptime(today, "%Y-%m-%d")) - 86400
            cursor = time.strftime("%Y-%m-%d", time.localtime(start))
        else:
            cursor = today
        streak = 0
        for _ in range(3650):
            if counts.get(cursor, 0) >= goal:
                streak += 1
                ts = time.mktime(time.strptime(cursor, "%Y-%m-%d")) - 86400
                cursor = time.strftime("%Y-%m-%d", time.localtime(ts))
            else:
                break
        return streak
