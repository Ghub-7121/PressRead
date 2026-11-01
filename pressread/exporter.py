"""成果导出：Markdown + JSON，文件名带版本号。

版本号按「补丁位自增」走：v1.0.0 → v1.0.1 → …，保存在数据库 meta 里，
同一天多次导出也不会覆盖旧文件。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, paths


@dataclass
class ExportResult:
    version: str
    files: list[Path] = field(default_factory=list)
    paragraphs: int = 0


def bump_version(version: str) -> str:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        return "1.0.0"
    major, minor, patch = (int(x) for x in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def safe_filename(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    return text.strip(" ._")[:80] or "未命名"


class Exporter:
    def __init__(self, store, config):
        self.store = store
        self.config = config

    # ------------------------------------------------------------------ 版本
    def current_version(self) -> str:
        """下一个将被使用的版本号（meta 里存的是「下一个」，导出后立即自增）。"""
        return self.store.meta_get("export_version", __version__) or __version__

    def _allocate_version(self) -> str:
        version = self.current_version()
        self.store.meta_set("export_version", bump_version(version))
        return version

    def preview_name(self, day: str) -> str:
        return f"外刊精读_每日成果_{day.replace('-', '')}_v{self.current_version()}.md"

    # ------------------------------------------------------------------ 导出
    def export(
        self,
        day: str,
        *,
        fmt: str = "both",
        target_dir: str | Path | None = None,
    ) -> ExportResult:
        paragraphs = self.store.day_done(day)
        if not paragraphs:
            raise ValueError("这一天还没有完成的段落")
        version = self._allocate_version()
        out_dir = Path(target_dir) if target_dir else paths.ensure_data_dirs()["exports"]
        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = time.strftime("%Y%m%d", time.localtime())
        base = f"外刊精读_每日成果_{stamp}_v{version}"
        files: list[Path] = []
        if fmt in ("md", "both"):
            path = out_dir / f"{base}.md"
            path.write_text(self._markdown(day, version, paragraphs), encoding="utf-8")
            files.append(path)
        if fmt in ("json", "both"):
            path = out_dir / f"{base}.json"
            path.write_text(
                json.dumps(self._json(day, version, paragraphs), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            files.append(path)
        self.store.record_export(version, day, str(files[0]), fmt, len(paragraphs))
        return ExportResult(version=version, files=files, paragraphs=len(paragraphs))

    # ------------------------------------------------------------------ 内容
    @staticmethod
    def _paragraph_total(paragraphs: list[dict[str, Any]]) -> int:
        """一次练习可能覆盖多段（设置「一次显示 N 段」），按实际段数统计。"""
        total = 0
        for item in paragraphs:
            try:
                total += max(1, int(item.get("para_count") or 1))
            except (TypeError, ValueError):
                total += 1
        return total

    def _markdown(self, day: str, version: str, paragraphs: list[dict[str, Any]]) -> str:
        goal = self.config.daily_goal
        completed = self._paragraph_total(paragraphs)
        lines: list[str] = [
            "# 外刊精读 · 每日成果",
            "",
            f"- 日期：{day}",
            f"- 导出时间：{time.strftime('%Y-%m-%d %H:%M')}",
            f"- 版本：v{version}",
            f"- 完成段落：{completed} / {goal}",
            "- 工具：PressRead 外刊精读",
            "",
            "---",
            "",
        ]
        for idx, item in enumerate(paragraphs, start=1):
            refs = item.get("refs") or []
            if refs and isinstance(refs[0], str):
                refs = [{"en": "", "zh": r} for r in refs]
            span = max(1, int(item.get("para_count") or 1))
            heading = f"## {idx:02d}｜{item.get('title') or '未命名文章'}"
            if span > 1:
                heading += f"（连续 {span} 段）"
            lines.extend(
                [
                    heading,
                    "",
                    f"- 来源：{item.get('source_name') or '—'}",
                    f"- 完成时间：{self._fmt_time(item.get('completed_at'))}",
                    "",
                    "### 原文",
                    "",
                    f"> {item.get('text', '').strip()}",
                    "",
                    "### 我的译文",
                    "",
                    (item.get("my_translation") or "").strip() or "_（本次未填写）_",
                    "",
                    "### 参考译文（逐句）",
                    "",
                ]
            )
            for n, ref in enumerate(refs, start=1):
                en = (ref.get("en") or "").strip()
                zh = (ref.get("zh") or "").strip()
                if en:
                    lines.append(f"**{n}.** {en}")
                lines.append(f"　　{zh}")
                lines.append("")
            lines.extend(["---", ""])
        lines.extend(
            [
                "> 原文版权归原媒体所有，本文件仅用于个人语言学习。",
                "",
            ]
        )
        return "\n".join(lines)

    def _json(self, day: str, version: str, paragraphs: list[dict[str, Any]]) -> dict[str, Any]:
        out = []
        for item in paragraphs:
            refs = item.get("refs") or []
            if refs and isinstance(refs[0], str):
                refs = [{"en": "", "zh": r} for r in refs]
            out.append(
                {
                    "title": item.get("title"),
                    "source": item.get("source_name"),
                    "text": item.get("text"),
                    "my_translation": item.get("my_translation"),
                    "references": refs,
                    "para_count": max(1, int(item.get("para_count") or 1)),
                    "completed_at": item.get("completed_at"),
                }
            )
        return {
            "app": "PressRead",
            "app_version": __version__,
            "export_version": version,
            "date": day,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "goal": self.config.daily_goal,
            "completed": self._paragraph_total(paragraphs),
            "paragraphs": out,
        }

    @staticmethod
    def _fmt_time(ts: Any) -> str:
        try:
            return time.strftime("%H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            return "—"
