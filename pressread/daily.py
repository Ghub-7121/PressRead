"""每日任务：完成任意 N 段（默认 5 段）的翻译练习。

「完成」的判定写在界面层：一段里的参考译文全部逐句看过 + 点击「标记本段完成」。
这里只负责记录、草稿保存、统计。
"""

from __future__ import annotations

import time
from typing import Any

from .service import PracticeUnit
from .store import make_id


def today_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class DailyTask:
    def __init__(self, store, config):
        self.store = store
        self.config = config

    @property
    def goal(self) -> int:
        return self.config.daily_goal

    def today(self) -> str:
        return today_key()

    def done_count(self, day: str | None = None) -> int:
        return self.store.day_done_count(day or self.today())

    def is_complete(self, day: str | None = None) -> bool:
        return self.done_count(day) >= self.goal

    def remaining(self, day: str | None = None) -> int:
        return max(0, self.goal - self.done_count(day))

    def streak(self) -> int:
        return self.store.streak(self.goal)

    def summary(self) -> dict[str, Any]:
        day = self.today()
        done = self.done_count(day)
        return {
            "day": day,
            "goal": self.goal,
            "done": done,
            "remaining": max(0, self.goal - done),
            "complete": done >= self.goal,
            "streak": self.streak(),
            "stats": self.store.stats(),
        }

    # ------------------------------------------------------------------ 段落
    def open_unit(self, unit: PracticeUnit) -> dict[str, Any]:
        article_id = make_id(unit.article.url)
        record = self.store.ensure_practice(
            self.today(),
            article_id,
            unit.index,
            unit.text,
            source_name=unit.article.source_name,
            title=unit.article.title,
            para_count=unit.span,
        )
        return record

    def save_draft(self, practice_id: int, text: str) -> None:
        self.store.update_practice(practice_id, my_translation=text)

    def complete(
        self, practice_id: int, my_translation: str, refs: list[dict[str, Any]]
    ) -> bool:
        before = self.done_count()
        self.store.complete_practice(practice_id, my_translation, refs)
        after = self.done_count()
        return after > before

    def mark_active(self, practice_id: int) -> None:
        self.store.update_practice(practice_id, status="active")

    def today_paragraphs(self) -> list[dict[str, Any]]:
        return self.store.day_done(self.today())
