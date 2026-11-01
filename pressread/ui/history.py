"""练习历史：按天回看已完成段落，并支持按天导出（版本号命名）。"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from . import theme
from .widgets import ScrollFrame


class HistoryPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.ctx = app.ctx
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", side="top")
        tk.Frame(header, bg=theme.LINE, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(header, bg=theme.SURFACE)
        bar.pack(fill="x", padx=24, pady=12)
        ttk.Button(
            bar, text="← 返回", style="SurfaceGhost.TButton", command=self.app.go_start
        ).pack(side="left")
        tk.Label(
            bar, text="练习历史", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_xl"],
        ).pack(side="left", padx=(14, 0))
        self.summary = tk.Label(
            bar, text="", bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"]
        )
        self.summary.pack(side="left", padx=(14, 0))

        self.scroll = ScrollFrame(self, bg=theme.BG)
        self.scroll.pack(fill="both", expand=True)
        self.content = tk.Frame(self.scroll.inner, bg=theme.BG)
        self.content.pack(fill="both", expand=True, padx=30, pady=20)

    def refresh(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        days = self.ctx.store.active_days()[:30]
        total = sum(self.ctx.store.day_done_count(day) for day in days)
        self.summary.configure(text=f"共 {len(days)} 天 · {total} 段")

        if not days:
            card = theme.card(self.content)
            card.pack(fill="x")
            tk.Label(
                card,
                text="还没有完成过段落。\n\n完成一段的流程：逐句看完参考译文 → 点「标记本段完成」。",
                bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"],
                anchor="w", justify="left", padx=24, pady=24,
            ).pack(anchor="w")
            return

        for day in days:
            self._day_card(day)

    def _day_card(self, day: str) -> None:
        records = self.ctx.store.day_done(day)
        card = theme.card(self.content)
        card.pack(fill="x", pady=(0, 12))

        head = tk.Frame(card, bg=theme.SURFACE)
        head.pack(fill="x", padx=22, pady=(14, 10))
        tk.Label(
            head, text=day, bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_lg"],
        ).pack(side="left")
        tk.Label(
            head, text=f"{len(records)} 段", bg=theme.TEAL_SOFT, fg=theme.TEAL,
            font=theme.FONTS["ui_xs"], padx=8, pady=2,
        ).pack(side="left", padx=(10, 0))
        if day == time.strftime("%Y-%m-%d"):
            tk.Label(
                head, text="今天", bg=theme.ACCENT_SOFT, fg=theme.ACCENT,
                font=theme.FONTS["ui_xs"], padx=8, pady=2,
            ).pack(side="left", padx=(6, 0))
        ttk.Button(
            head, text="导出这一天", style="Small.TButton",
            command=lambda d=day: self._export(d),
        ).pack(side="right")

        tk.Frame(card, bg=theme.LINE, height=1).pack(fill="x", padx=20)
        for record in records:
            self._record_row(card, record)

    def _record_row(self, parent: tk.Misc, record: dict[str, Any]) -> None:
        row = tk.Frame(parent, bg=theme.SURFACE, cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        inner = tk.Frame(row, bg=theme.SURFACE)
        inner.pack(fill="x", padx=12, pady=9)
        left = tk.Frame(inner, bg=theme.SURFACE)
        left.pack(side="left", fill="x", expand=True)

        title = record.get("title") or "未命名文章"
        tk.Label(
            left, text=title if len(title) <= 70 else title[:69] + "…",
            bg=theme.SURFACE, fg=theme.INK, font=theme.FONTS["ui"], anchor="w",
        ).pack(anchor="w")
        stamp = record.get("completed_at") or 0
        when = time.strftime("%H:%M", time.localtime(stamp)) if stamp else "—"
        refs = record.get("refs") or []
        mine = (record.get("my_translation") or "").strip()
        details = f"{record.get('source_name', '—')}　·　{when}　·　{len(refs)} 句"
        if mine:
            details += f"　·　我的译文 {len(mine)} 字"
        tk.Label(
            left, text=details, bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            inner, text="回看 →", bg=theme.SURFACE, fg=theme.ACCENT,
            font=theme.FONTS["ui_xs"],
        ).pack(side="right")

        for widget in (row, inner, left, *left.winfo_children()):
            widget.bind("<Button-1>", lambda _e, r=record: self._open(r))
        row.bind("<Enter>", lambda _e: self._hover(row, inner, left, theme.SURFACE_ALT))
        row.bind("<Leave>", lambda _e: self._hover(row, inner, left, theme.SURFACE))

    def _hover(self, row, inner, left, color: str) -> None:
        for widget in (row, inner, left, *left.winfo_children()):
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass

    def _open(self, record: dict[str, Any]) -> None:
        self.app.show_reader(load=lambda: self.app.reader.load_record(record, review=True))

    def _export(self, day: str) -> None:
        try:
            result = self.ctx.exporter.export(day)
        except Exception as exc:
            self.app.toast(f"导出失败：{exc}")
            return
        self.app.toast(f"已导出 {day} · v{result.version}")
        self.app.open_folder(result.files[0].parent)
