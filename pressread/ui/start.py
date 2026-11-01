"""开始页：今日任务、连续天数、最近练习与来源偏好。"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .. import __version__
from ..feeds import SOURCE_MAP
from . import theme
from .widgets import ProgressDots, ScrollFrame


class StartPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.ctx = app.ctx
        self._build()
        self.refresh()

    # ================================================================== 构建
    def _build(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", side="top")
        tk.Frame(header, bg=theme.LINE, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(header, bg=theme.SURFACE)
        bar.pack(fill="x", padx=30, pady=12)

        mark = tk.Canvas(bar, width=34, height=34, bg=theme.SURFACE, bd=0, highlightthickness=0)
        mark.pack(side="left")
        mark.create_rectangle(1, 1, 33, 33, fill=theme.ACCENT, outline=theme.ACCENT)
        mark.create_text(17, 17, text="P", fill="#FFFFFF", font=theme.FONTS["ui_lg"])

        title_box = tk.Frame(bar, bg=theme.SURFACE)
        title_box.pack(side="left", padx=(10, 0))
        tk.Label(
            title_box, text="外刊精读", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_lg"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            title_box, text="PressRead · 每天五段，读懂世界", bg=theme.SURFACE,
            fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w")

        ttk.Button(
            bar, text="设置", style="SurfaceGhost.TButton", command=self.app.open_settings
        ).pack(side="right")
        ttk.Button(
            bar, text="练习历史", style="SurfaceGhost.TButton", command=self.app.go_history
        ).pack(side="right", padx=(0, 4))

        self.scroll = ScrollFrame(self, bg=theme.BG)
        self.scroll.pack(fill="both", expand=True)
        self.content = tk.Frame(self.scroll.inner, bg=theme.BG)
        self.content.pack(fill="both", expand=True, padx=30, pady=(20, 24))

        self._build_hero()
        self.stats_row = tk.Frame(self.content, bg=theme.BG)
        self.stats_row.pack(fill="x", pady=(16, 0))
        self._build_stats()

        lower = tk.Frame(self.content, bg=theme.BG)
        lower.pack(fill="both", expand=True, pady=(16, 0))
        self.recent_card = theme.card(lower)
        self.recent_card.pack(side="left", fill="both", expand=True)
        self.source_card = theme.card(lower, width=326)
        self.source_card.pack(side="right", fill="y", padx=(16, 0))

        footer = tk.Frame(self.content, bg=theme.BG)
        footer.pack(fill="x", pady=(18, 0))
        self.footer_left = tk.Label(
            footer, text="", bg=theme.BG, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="w"
        )
        self.footer_left.pack(side="left")
        self.footer_right = tk.Label(
            footer, text="", bg=theme.BG, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="e"
        )
        self.footer_right.pack(side="right")

    def _build_hero(self) -> None:
        hero = theme.card(self.content)
        hero.pack(fill="x")
        left = tk.Frame(hero, bg=theme.SURFACE)
        left.pack(side="left", fill="both", expand=True, padx=34, pady=28)

        tk.Label(
            left, text="DAILY  READING   ·   每 日 精 读", bg=theme.SURFACE, fg=theme.ACCENT,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            left, text="把世界，读成自己的句子。", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["hero"], anchor="w",
        ).pack(anchor="w", pady=(10, 6))
        self.hero_desc = tk.Label(
            left,
            text="实时抓取 Science News、WIRED、Quanta 等外刊原文，截一段给你翻译；"
                 "单词查牛津，句子看真实站点译文，逐句对照，最后导出成册。",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"],
            anchor="w", justify="left", wraplength=560,
        )
        self.hero_desc.pack(anchor="w")

        cta = tk.Frame(left, bg=theme.SURFACE)
        cta.pack(anchor="w", pady=(20, 0))
        self.primary_btn = ttk.Button(
            cta, text="开始今日练习", style="Accent.TButton", command=self._primary_action
        )
        self.primary_btn.pack(side="left")
        ttk.Button(
            cta, text="换一篇外刊", style="TButton", command=self.app.start_practice
        ).pack(side="left", padx=(10, 0))
        self.hero_hint = tk.Label(
            cta, text="① 选一段　→　② 自己译　→　③ 逐句对照",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
        )
        self.hero_hint.pack(side="left", padx=(14, 0))

        right = tk.Frame(hero, bg=theme.SURFACE_ALT, width=286)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        pad = tk.Frame(right, bg=theme.SURFACE_ALT)
        pad.pack(fill="both", expand=True, padx=26, pady=24)

        tk.Label(
            pad, text="今 日 任 务", bg=theme.SURFACE_ALT, fg=theme.MUTED,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w")
        number_row = tk.Frame(pad, bg=theme.SURFACE_ALT)
        number_row.pack(anchor="w", pady=(4, 0))
        self.done_number = tk.Label(
            number_row, text="0", bg=theme.SURFACE_ALT, fg=theme.ACCENT,
            font=theme.FONTS["num"],
        )
        self.done_number.pack(side="left")
        self.goal_number = tk.Label(
            number_row, text="/ 5 段", bg=theme.SURFACE_ALT, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"],
        )
        self.goal_number.pack(side="left", padx=(4, 0), pady=(8, 0))

        self.hero_dots = ProgressDots(pad, total=5, done=0, size=11, gap=9, bg=theme.SURFACE_ALT)
        self.hero_dots.pack(anchor="w", pady=(10, 10))
        self.streak_label = tk.Label(
            pad, text="", bg=theme.SURFACE_ALT, fg=theme.INK_SOFT,
            font=theme.FONTS["ui_sm"], anchor="w", justify="left",
        )
        self.streak_label.pack(anchor="w")
        self.hero_state = tk.Label(
            pad, text="", bg=theme.SURFACE_ALT, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"], anchor="w", justify="left", wraplength=220,
        )
        self.hero_state.pack(anchor="w", pady=(6, 0))

    def _build_stats(self) -> None:
        self.stat_values: dict[str, tk.Label] = {}
        specs = [
            ("streak", "连续达成", "天"),
            ("paragraphs", "累计完成", "段"),
            ("queries", "查词 / 翻译", "次"),
            ("hit_rate", "缓存命中", "%"),
        ]
        for index, (key, label, unit) in enumerate(specs):
            if index:
                tk.Frame(self.stats_row, bg=theme.LINE, width=1).pack(side="left", fill="y", pady=10)
            cell = tk.Frame(self.stats_row, bg=theme.BG)
            cell.pack(side="left", fill="x", expand=True, padx=(18 if index else 4, 8))
            row = tk.Frame(cell, bg=theme.BG)
            row.pack(anchor="w")
            value = tk.Label(
                row, text="0", bg=theme.BG, fg=theme.INK, font=theme.FONTS["num"]
            )
            value.pack(side="left")
            tk.Label(
                row, text=unit, bg=theme.BG, fg=theme.FAINT, font=theme.FONTS["ui_xs"]
            ).pack(side="left", padx=(3, 0), pady=(10, 0))
            tk.Label(
                cell, text=label, bg=theme.BG, fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w"
            ).pack(anchor="w")
            self.stat_values[key] = value

    # ================================================================== 刷新
    def refresh(self) -> None:
        summary = self.ctx.daily.summary()
        goal = summary["goal"]
        done = summary["done"]
        self.done_number.configure(text=str(done))
        self.goal_number.configure(text=f"/ {goal} 段")
        self.hero_dots.set_total(goal)
        self.hero_dots.update_progress(done)

        if summary["complete"]:
            self.primary_btn.configure(text="导出今日成果", command=self._export_today)
            self.hero_state.configure(text="今日任务已完成，导出后可以继续加练。")
        else:
            self.primary_btn.configure(text="开始今日练习", command=self._primary_action)
            self.hero_state.configure(text=f"还差 {summary['remaining']} 段完成今日任务。")

        streak = summary["streak"]
        self.streak_label.configure(
            text=f"连续 {streak} 天达成目标" if streak else "今天开始，连续记录从第一段算起"
        )
        stats = summary["stats"]
        self.stat_values["streak"].configure(text=str(streak))
        self.stat_values["paragraphs"].configure(text=str(stats["paragraphs"]))
        self.stat_values["queries"].configure(text=str(stats["queries"]))
        self.stat_values["hit_rate"].configure(text=f"{stats['hit_rate'] * 100:.0f}")

        self._refresh_recent()
        self._refresh_sources()

        cache = self.ctx.store.cache_stats()
        self.footer_left.configure(
            text=f"数据目录：{self.ctx.paths['root']}　·　缓存 {cache['total']} 条 / 复用 {cache['hits']} 次"
        )
        self.footer_right.configure(text=f"v{__version__}　·　原文版权归原媒体所有，仅供个人学习")

    def _refresh_recent(self) -> None:
        for child in self.recent_card.winfo_children():
            child.destroy()
        head = tk.Frame(self.recent_card, bg=theme.SURFACE)
        head.pack(fill="x", padx=22, pady=(16, 8))
        tk.Label(
            head, text="最近练习", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(side="left")
        tk.Button(
            head, text="全部历史 →", command=self.app.go_history, bg=theme.SURFACE,
            fg=theme.MUTED, activebackground=theme.SURFACE, relief="flat", bd=0,
            font=theme.FONTS["ui_xs"], cursor="hand2",
        ).pack(side="right")

        records = self.ctx.store.recent_practices(5)
        if not records:
            tk.Label(
                self.recent_card,
                text="还没有练习记录。\n点击「开始今日练习」，先读第一段。",
                bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"],
                anchor="w", justify="left",
            ).pack(anchor="w", padx=22, pady=(10, 24))
            return

        for record in records:
            self._recent_row(record)
        tk.Frame(self.recent_card, bg=theme.SURFACE, height=8).pack(fill="x")

    def _recent_row(self, record: dict[str, Any]) -> None:
        row = tk.Frame(self.recent_card, bg=theme.SURFACE, cursor="hand2")
        row.pack(fill="x", padx=12, pady=1)
        inner = tk.Frame(row, bg=theme.SURFACE)
        inner.pack(fill="x", padx=10, pady=8)
        left = tk.Frame(inner, bg=theme.SURFACE)
        left.pack(side="left", fill="x", expand=True)
        title = record.get("title") or "未命名文章"
        tk.Label(
            left, text=title if len(title) <= 62 else title[:61] + "…",
            bg=theme.SURFACE, fg=theme.INK, font=theme.FONTS["ui"], anchor="w",
        ).pack(anchor="w")
        stamp = record.get("completed_at") or record.get("updated_at") or 0
        when = time.strftime("%m-%d %H:%M", time.localtime(float(stamp))) if stamp else "—"
        sentences = len(record.get("refs") or []) or len(
            [line for line in (record.get("text") or "").split(".") if line.strip()]
        )
        tk.Label(
            left, text=f"{record.get('source_name', '—')}　·　{sentences} 句　·　{when}",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        done = record.get("status") == "done"
        tk.Label(
            inner, text="已完成" if done else "未完成",
            bg=theme.TEAL_SOFT if done else theme.GOLD_SOFT,
            fg=theme.TEAL if done else theme.GOLD,
            font=theme.FONTS["ui_xs"], padx=8, pady=2,
        ).pack(side="right")
        for widget in (row, inner, left):
            widget.bind("<Button-1>", lambda _e, r=record: self._open_record(r))
        for child in left.winfo_children():
            child.bind("<Button-1>", lambda _e, r=record: self._open_record(r))
        row.bind("<Enter>", lambda _e: self._hover_row(row, inner, left, theme.SURFACE_ALT))
        row.bind("<Leave>", lambda _e: self._hover_row(row, inner, left, theme.SURFACE))

    def _hover_row(self, row, inner, left, color: str) -> None:
        for widget in (row, inner, left, *left.winfo_children()):
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass

    def _open_record(self, record: dict[str, Any]) -> None:
        review = record.get("status") == "done"
        self.app.show_reader(load=lambda: self.app.reader.load_record(record, review=review))

    def _refresh_sources(self) -> None:
        for child in self.source_card.winfo_children():
            child.destroy()
        head = tk.Frame(self.source_card, bg=theme.SURFACE)
        head.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(
            head, text="优先来源", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(side="left")
        tk.Button(
            head, text="管理", command=self.app.open_settings, bg=theme.SURFACE,
            fg=theme.MUTED, activebackground=theme.SURFACE, relief="flat", bd=0,
            font=theme.FONTS["ui_xs"], cursor="hand2",
        ).pack(side="right")
        tk.Label(
            self.source_card,
            text="抓取时优先从选中的外刊找文章，其余源作为备份。",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left", wraplength=270,
        ).pack(anchor="w", padx=20, pady=(2, 10))

        chips = tk.Frame(self.source_card, bg=theme.SURFACE)
        chips.pack(fill="x", padx=18)
        chips.columnconfigure(0, weight=1)
        chips.columnconfigure(1, weight=1)
        self._chip(chips, "全部来源", None, 0, 0)
        # 启用来源多时只展示前几个，避免卡片被撑得很长；其余去设置里看
        enabled = [sid for sid in self.ctx.config.enabled_sources if sid in SOURCE_MAP]
        shown = enabled[:7]
        for index, sid in enumerate(shown, start=1):
            self._chip(chips, SOURCE_MAP[sid].name, sid, index // 2, index % 2)
        if len(enabled) > len(shown):
            index = len(shown) + 1
            self._chip(
                chips,
                f"还有 {len(enabled) - len(shown)} 个来源…",
                "__more__",
                index // 2,
                index % 2,
            )
        tk.Label(
            self.source_card,
            text="双击正文句子或拖选文字，都能在右侧看到译文。",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left", wraplength=270,
        ).pack(anchor="w", padx=20, pady=(10, 16), side="bottom")

    def _chip(self, parent: tk.Misc, text: str, source_id: str | None, row: int, col: int) -> None:
        is_more = source_id == "__more__"
        active = self.app.preferred_source == source_id
        label = tk.Label(
            parent,
            text=text,
            bg=theme.ACCENT_SOFT if active else theme.SURFACE_ALT,
            fg=theme.ACCENT if (active or is_more) else theme.INK_SOFT,
            font=theme.FONTS["ui_xs"],
            padx=9,
            pady=5,
            cursor="hand2",
            bd=0,
            anchor="w",
        )
        label.grid(row=row, column=col, sticky="w", padx=2, pady=3)
        if is_more:
            label.bind("<Button-1>", lambda _e: self.app.open_settings())
        else:
            label.bind("<Button-1>", lambda _e: self._set_source(source_id))

    def _set_source(self, source_id: str | None) -> None:
        self.app.preferred_source = source_id
        self._refresh_sources()

    # ================================================================== 动作
    def _primary_action(self) -> None:
        if self.ctx.daily.is_complete():
            self._export_today()
        else:
            self.app.start_practice()

    def _export_today(self) -> None:
        try:
            result = self.ctx.exporter.export(self.ctx.daily.today())
        except Exception as exc:
            self.app.toast(f"导出失败：{exc}")
            return
        self.app.toast(f"已导出 v{result.version}")
        self.app.open_folder(result.files[0].parent)
