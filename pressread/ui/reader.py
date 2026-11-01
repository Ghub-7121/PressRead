"""阅读与翻译练习页。

交互约定
- 单击单词：右侧边栏查牛津释义（不打断阅读）
- 双击句子 / 点句首编号：右侧边栏显示整句参考译文
- 拖选短语或句子：右侧边栏显示选区译文
- 底部按钮：逐句把参考译文「贴」到正文下方，看完全部才能标记本段完成
"""

from __future__ import annotations

import re
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Any

from ..article import Article
from ..service import PracticeUnit
from ..textproc import (
    level_estimate,
    normalize_word,
    paragraph_starts,
    split_sentences,
    word_count,
)
from ..translate import TranslationError
from ..dictionary import WordNotFound
from . import theme
from .widgets import ProgressDots, Tooltip

HINT_TEXT = "单击单词查词　·　双击句子翻译　·　拖选短语翻译"

# 段首缩进与段间距（像素）。英文正文按「首行缩进 2 字符左右」排版。
PARA_INDENT = 28
PARA_GAP = 14


class ReaderPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.ctx = app.ctx
        self.unit: PracticeUnit | None = None
        self.practice: dict[str, Any] | None = None
        self.article: Article | None = None
        self.sentences: list[str] = []
        self.revealed: dict[int, dict[str, Any]] = {}
        self.bundled: list[str] = []
        self.review_mode = False

        self._word_job = None
        self._sel_job = None
        self._article_job = None
        self._reveal_job = None
        self._press_xy: tuple[int, int] | None = None
        self._dragged = False
        self._chip_ts = 0.0
        self._double_ts = 0.0
        self._hover_sent: int | None = None
        self._hover_word: tuple[str, str] | None = None
        self._draft_job: str | None = None
        self._draft_guard = False
        self._zh_start = "1.0"

        self._build()
        self.bind_all("<Control-Return>", self._on_complete_shortcut)

    # ================================================================== 构建
    def _build(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", side="top")
        tk.Frame(header, bg=theme.LINE, height=1).pack(fill="x", side="bottom")

        inner = tk.Frame(header, bg=theme.SURFACE, height=60)
        inner.pack(fill="x", padx=18, pady=10)

        self.back_btn = ttk.Button(
            inner, text="← 返回", style="SurfaceGhost.TButton", command=self.app.go_start
        )
        self.back_btn.pack(side="left")

        self.source_pill = tk.Label(
            inner, text="", bg=theme.ACCENT_SOFT, fg=theme.ACCENT,
            font=theme.FONTS["ui_xs"], padx=8, pady=3,
        )
        self.source_pill.pack(side="left", padx=(10, 12))

        self.title_label = tk.Label(
            inner, text="", bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        self.title_label.pack(side="left", fill="x", expand=True)

        self.daily_box = tk.Frame(inner, bg=theme.SURFACE)
        self.daily_box.pack(side="right")
        self.daily_label = tk.Label(
            self.daily_box, text="今日 0/5", bg=theme.SURFACE, fg=theme.INK_SOFT,
            font=theme.FONTS["ui_sm"],
        )
        self.daily_label.pack(side="left", padx=(0, 8))
        self.daily_dots = ProgressDots(
            self.daily_box, total=5, done=0, size=9, gap=6, bg=theme.SURFACE
        )
        self.daily_dots.pack(side="left")

        self.new_btn = ttk.Button(
            inner, text="换一篇", style="Small.TButton", command=self.load_new_article
        )
        self.new_btn.pack(side="right", padx=(12, 0))

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, side="top")

        from .sidebar import SidePanel

        self.sidebar = SidePanel(body, self.app, width=self.ctx.config.sidebar_width)
        self.sidebar.pack(side="right", fill="y")

        self.main = tk.Frame(body, bg=theme.BG)
        self.main.pack(side="left", fill="both", expand=True)

        meta = tk.Frame(self.main, bg=theme.BG)
        meta.pack(fill="x", padx=24, pady=(16, 8))
        self.meta_title = tk.Label(
            meta, text="", bg=theme.BG, fg=theme.INK,
            font=theme.FONTS["hero_sm"], anchor="w", justify="left",
        )
        self.meta_title.pack(anchor="w", fill="x")
        # 信息行：左边来源/作者/词数，右边「打开原文」，先占右侧再让左边吃余量
        line_row = tk.Frame(meta, bg=theme.BG)
        line_row.pack(fill="x", pady=(4, 0))
        self.open_btn = tk.Button(
            line_row, text="打开原文 ↗", command=self.open_source,
            bg=theme.BG, fg=theme.ACCENT, activebackground=theme.BG,
            activeforeground=theme.ACCENT_DARK, disabledforeground=theme.FAINT,
            relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2",
            padx=0, takefocus=0,
        )
        self.open_btn.pack(side="right", padx=(14, 0))
        self.open_tip = Tooltip(self.open_btn, "在浏览器中打开这篇文章的原网页")
        self.meta_line = tk.Label(
            line_row, text="", bg=theme.BG, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        self.meta_line.pack(side="left", fill="x", expand=True)
        self.offline_bar = tk.Frame(meta, bg=theme.GOLD_SOFT)
        self.offline_label = tk.Label(
            self.offline_bar, text="", bg=theme.GOLD_SOFT, fg=theme.GOLD,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        self.offline_label.pack(side="left", padx=(10, 6), pady=5)
        self.offline_retry = tk.Button(
            self.offline_bar, text="重试联网", command=self.load_new_article,
            bg=theme.GOLD_SOFT, fg=theme.GOLD, activebackground=theme.GOLD_SOFT,
            relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2",
        )

        card = theme.card(self.main)
        self.text_wrap = tk.Frame(card, bg=theme.SURFACE)
        self.text_wrap.pack(fill="both", expand=True, padx=2, pady=2)

        self.text = tk.Text(
            self.text_wrap,
            wrap="word",
            bg=theme.SURFACE,
            fg=theme.INK,
            bd=0,
            highlightthickness=0,
            padx=36,
            pady=24,
            font=theme.FONTS["article"],
            spacing1=2,
            spacing2=7,
            spacing3=2,
            cursor="arrow",
            insertwidth=0,
            selectbackground=theme.SELECT_BG,
            selectforeground=theme.INK,
            undo=False,
        )
        self.scroll = ttk.Scrollbar(self.text_wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

        self.text.tag_configure("chip", foreground=theme.ACCENT, font=theme.FONTS["ui_sm"])
        self.text.tag_configure("sent", foreground=theme.INK)
        self.text.tag_configure("hover_sent", background=theme.SENT_HL)
        self.text.tag_configure("hover_word", underline=True, background=theme.WORD_HL)
        self.text.tag_configure(
            "section", foreground=theme.MUTED, font=theme.FONTS["ui_sm"],
            spacing1=26, spacing3=4,
        )
        self.text.tag_configure("zh_num", foreground=theme.TEAL, font=theme.FONTS["ui_bold"])
        self.text.tag_configure(
            "zh", foreground=theme.INK_SOFT, font=theme.FONTS["zh"],
            lmargin1=26, lmargin2=26, rmargin=16, spacing1=8, spacing3=2,
        )
        self.text.tag_configure("hint", foreground=theme.FAINT, font=theme.FONTS["ui_sm"], spacing1=8)
        self.text.tag_configure("review", foreground=theme.GOLD, font=theme.FONTS["ui_xs"])

        self.text.tag_bind("chip", "<Button-1>", self._on_chip_click)
        self.text.bind("<Button-1>", self._on_press)
        self.text.bind("<B1-Motion>", self._on_drag)
        self.text.bind("<ButtonRelease-1>", self._on_release)
        self.text.bind("<Double-Button-1>", self._on_double)
        self.text.bind("<Motion>", self._on_motion)
        self.text.bind("<Leave>", lambda _e: self._clear_hover())
        self.text.bind("<<Selection>>", lambda _e: None)
        self.text.configure(state="disabled")

        hint_row = tk.Frame(self.main, bg=theme.BG)
        tk.Label(
            hint_row, text=HINT_TEXT, bg=theme.BG, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(side="left")

        self.my_panel = tk.Frame(self.main, bg=theme.BG)
        my_head = tk.Frame(self.my_panel, bg=theme.BG)
        my_head.pack(fill="x")
        self.my_toggle = tk.Button(
            my_head, text="▾  我的译文（可选）", command=self._toggle_my_panel,
            bg=theme.BG, fg=theme.INK_SOFT, activebackground=theme.BG,
            relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2", padx=0,
        )
        self.my_toggle.pack(side="left")
        self.my_count = tk.Label(
            my_head, text="自动保存", bg=theme.BG, fg=theme.FAINT, font=theme.FONTS["ui_xs"]
        )
        self.my_count.pack(side="right")

        self.my_box = tk.Frame(self.my_panel, bg=theme.BG)
        self.my_text = tk.Text(
            self.my_box, height=3, wrap="word", bg=theme.SURFACE,
            fg=theme.INK, font=theme.FONTS["zh"], bd=0,
            highlightthickness=1, highlightbackground=theme.LINE,
            highlightcolor=theme.ACCENT, padx=12, pady=10,
            spacing2=4, undo=True,
        )
        self.my_text.pack(fill="x")
        self.my_text.bind("<<Modified>>", self._on_draft_modified)
        self.my_text.bind("<FocusIn>", lambda _e: self._sync_placeholder())
        self.my_text.bind("<FocusOut>", lambda _e: self._sync_placeholder())
        self.my_placeholder = tk.Label(
            self.my_box,
            text="先自己译一遍，再看参考译文；留空也可以。",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
        )
        self.my_placeholder.place(x=14, y=13)
        self.my_box.pack(fill="x", pady=(4, 0))

        action = tk.Frame(self.main, bg=theme.BG)
        tk.Frame(action, bg=theme.LINE, height=1).pack(fill="x", pady=(0, 10))

        self.status = tk.Label(
            action, text="", bg=theme.BG, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        self.status.pack(side="left", fill="x", expand=True)

        self.complete_btn = ttk.Button(
            action, text="标记本段完成", style="Accent.TButton", command=self.mark_complete
        )
        self.complete_btn.pack(side="right")
        self.next_btn = ttk.Button(
            action, text="换一段", style="TButton", command=self.next_paragraph
        )
        self.next_btn.pack(side="right", padx=(0, 8))
        self.all_btn = ttk.Button(
            action, text="全部显示", style="TButton", command=self.reveal_all
        )
        self.all_btn.pack(side="right", padx=(0, 8))
        self.reveal_btn = ttk.Button(
            action, text="显示下一句译文", style="Teal.TButton", command=self.reveal_next
        )
        self.reveal_btn.pack(side="right", padx=(0, 8))

        # 先给底部控件分配空间，正文卡片再吃掉剩余高度
        action.pack(side="bottom", fill="x", padx=24, pady=(0, 16))
        self.my_panel.pack(side="bottom", fill="x", padx=24, pady=(6, 10))
        hint_row.pack(side="bottom", fill="x", padx=24, pady=(4, 2))
        card.pack(side="top", fill="both", expand=True, padx=24, pady=(0, 6))

    # ================================================================== 数据
    def refresh_progress(self) -> None:
        summary = self.ctx.daily.summary()
        goal = summary["goal"]
        self.daily_dots.set_total(goal)
        self.daily_dots.update_progress(summary["done"])
        self.daily_label.configure(text=f"今日 {summary['done']}/{goal}")

    def load_new_article(self, preferred_source: str | None = None) -> None:
        if self._article_job:
            self._article_job.cancel()
        self._set_loading("正在从多个外刊源并发抓取…")
        self.complete_btn.state(["disabled"])
        source = preferred_source or self.app.preferred_source

        def work():
            return self.ctx.articles.next_unit(
                today=self.ctx.daily.today(), preferred_source=source
            )

        self._article_job = self.app.runner.run(
            work, self._on_article_loaded, self._on_article_error, name="article"
        )

    def _on_article_loaded(self, unit: PracticeUnit) -> None:
        self.display_unit(unit)
        errors = self.ctx.articles.last_errors()
        if errors:
            self.app.toast(f"部分外刊源不可用，已自动切换（{len(errors)} 条提示）", 3200)
        if unit.offline:
            self.app.toast("当前网络抓不到外刊，已切到离线示例", 3200)

    def _on_article_error(self, exc: Exception) -> None:
        message = getattr(exc, "message", str(exc))
        self._set_loading("")
        self.status.configure(text=f"抓取失败：{message}")
        self.app.toast("抓取失败，可稍后重试")
        if self.article is None:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", f"暂时拿不到文章。\n\n{message}\n\n请检查网络，或点右上角「换一篇」重试。", ("sent",))
            self.text.configure(state="disabled")

    def display_unit(self, unit: PracticeUnit, *, open_record: bool = True) -> None:
        self.unit = unit
        self.article = unit.article
        self.sentences = list(unit.sentences) or split_sentences(unit.text)
        self.bundled = list(unit.bundled_translations)
        self.review_mode = False
        self.revealed = {}
        self.practice = self.ctx.daily.open_unit(unit) if open_record else None

        title = unit.article.title
        self.title_label.configure(text=title if len(title) <= 54 else title[:53] + "…")
        self.source_pill.configure(
            text=f"{unit.article.source_name} · {unit.article.category or '综合'}"
        )
        self.meta_title.configure(text=unit.article.title)
        bits = [unit.article.source_name]
        if unit.article.author:
            bits.append(unit.article.author)
        if unit.article.published:
            bits.append(unit.article.published)
        if unit.span > 1:
            # 一次显示多段时明确告诉用户：本屏是连续的第 X–Y 段
            bits.append(f"连续 {unit.span} 段（第 {unit.index + 1}–{unit.index + unit.span} 段）")
        level, how = level_estimate(unit.text)
        bits.append(f"{word_count(unit.text)} 词 · {len(self.sentences)} 句 · {level}（{how}）")
        self.meta_line.configure(text="　·　".join(bits))
        self._sync_open_button()

        if unit.offline:
            self.offline_label.configure(
                text="离线示例：网络暂时不可用，这篇是内置原创文章，译文随程序提供。"
            )
            self.offline_bar.pack(fill="x", pady=(8, 0))
            self.offline_retry.pack(side="left", pady=4, padx=(0, 8))
        else:
            self.offline_bar.pack_forget()

        self.complete_btn.configure(
            text=f"标记本 {unit.span} 段完成" if unit.span > 1 else "标记本段完成"
        )
        self._render_text()
        self._load_draft()
        self._update_action_state()
        self.refresh_progress()
        self.status.configure(text="")
        self.text.yview_moveto(0)

    # ------------------------------------------------------------------ 原文
    def _original_url(self, record: dict[str, Any]) -> str:
        """历史记录只存 article_id，回查文章表才能拿到当时的原文链接。"""
        aid = str(record.get("article_id") or "")
        row = None
        if aid:
            try:
                row = self.ctx.store.get_article(aid)
            except Exception:
                row = None
        url = str((row or {}).get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            return url
        return f"review://{record.get('id')}"

    def _sync_open_button(self) -> None:
        """只有真正的 http(s) 原文才有「打开原文」；离线示例和历史占位链接禁用。"""
        url = (self.article.url if self.article else "").strip()
        usable = url.lower().startswith(("http://", "https://"))
        self.open_btn.configure(
            state="normal" if usable else "disabled",
            cursor="hand2" if usable else "arrow",
        )
        self.open_tip.text = url if usable else "这篇没有可打开的原文链接"

    def open_source(self) -> None:
        """用系统默认浏览器打开当前文章的原网页。"""
        url = (self.article.url if self.article else "").strip()
        if not url.lower().startswith(("http://", "https://")):
            self.app.toast("这篇没有可打开的原文链接")
            return
        try:
            opened = webbrowser.open(url, new=2)
        except Exception:
            opened = False
        if opened:
            self.status.configure(text="已在浏览器中打开原文")
            return
        # 找不到可用浏览器时，至少把链接交给用户，不至于白点一次
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.app.toast("没能唤起浏览器，原文链接已复制到剪贴板")
        except tk.TclError:
            self.app.toast("没能唤起浏览器，请手动复制链接")

    def load_record(self, record: dict[str, Any], *, review: bool = True) -> None:
        """历史回看 / 继续未完成段落：不联网，数据全部来自本地。"""
        if self._article_job:
            self._article_job.cancel()
        text = record.get("text", "")
        sentences = split_sentences(text)
        refs = record.get("refs") or []
        if refs and isinstance(refs[0], str):
            refs = [{"en": "", "zh": r} for r in refs]
        zh_map: dict[int, dict[str, Any]] = {}
        for idx, ref in enumerate(refs):
            if idx < len(sentences):
                zh_map[idx] = {
                    "zh": ref.get("zh", ""),
                    "source": ref.get("source", "历史记录"),
                }
        stamp = record.get("completed_at") or record.get("updated_at") or time.time()
        article = Article(
            url=self._original_url(record),
            source_id="review",
            source_name=record.get("source_name", "历史记录"),
            category="",
            title=record.get("title", "历史段落"),
            paragraphs=[text],
            published=time.strftime("%Y-%m-%d", time.localtime(stamp)),
        )
        bundled = [zh_map[i]["zh"] if i in zh_map else "" for i in range(len(sentences))]
        unit = PracticeUnit(
            article=article,
            index=int(record.get("para_index", 0)),
            text=text,
            sentences=sentences,
            span=max(1, int(record.get("para_count") or 1)),
            para_starts=paragraph_starts(text, sentences),
            bundled_translations=bundled,
        )
        self.display_unit(unit, open_record=False)
        self.practice = record
        self.review_mode = review
        self.revealed = dict(zh_map) if review else {}
        self._draft_guard = True
        self.my_text.delete("1.0", "end")
        self.my_text.insert("1.0", record.get("my_translation", ""))
        self.my_text.edit_modified(False)
        self._draft_guard = False
        self._update_draft_count()
        self._render_text()

        if review:
            self.source_pill.configure(text="历史回看")
            self.complete_btn.state(["disabled"])
            self.all_btn.state(["disabled"])
            self.reveal_btn.state(["disabled"])
            self.next_btn.configure(text="返回历史", command=self.app.go_history)
            self.status.configure(
                text=f"历史回看 · {time.strftime('%Y-%m-%d %H:%M', time.localtime(stamp))} 完成"
            )
        else:
            self.source_pill.configure(text="继续未完成")
            self.next_btn.configure(text="换一段", command=self.next_paragraph)
            self._update_action_state()
            self.status.configure(text="已恢复未完成的段落，继续逐句看译文吧")

    def _load_draft(self) -> None:
        draft = (self.practice or {}).get("my_translation", "") if self.practice else ""
        self._draft_guard = True
        self.my_text.delete("1.0", "end")
        if draft:
            self.my_text.insert("1.0", draft)
        self.my_text.edit_modified(False)
        self._draft_guard = False
        self._update_draft_count()

    # ================================================================== 渲染
    def _paragraph_starts(self) -> set[int]:
        """本屏哪几句是新一段的第一句；数据缺失时当作一整段。"""
        unit = getattr(self, "unit", None)
        raw = list(getattr(unit, "para_starts", None) or [0])
        total = len(self.sentences)
        starts = {i for i in raw if isinstance(i, int) and 0 <= i < total}
        starts.add(0)
        return starts

    def _mark_paragraph(self, number: int, start: str, end: str) -> None:
        """给一段打上「首行缩进 + 段前间距」的标记。

        lmargin1 也只对这一段的第一行生效，续行用 lmargin2=0 回到左边，
        所以看上去就是传统排版里的首行缩进。
        """
        if not start or not end or self.text.compare(start, ">=", end):
            return
        tag = f"para{number}"
        self.text.tag_configure(
            tag,
            lmargin1=PARA_INDENT,
            lmargin2=0,
            spacing1=0 if number <= 1 else PARA_GAP,
        )
        self.text.tag_add(tag, start, end)
        # 这个 tag 只带边距，不碰字体与颜色；提到最上层，免得被逐句标签盖掉
        self.text.tag_raise(tag)

    def _render_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        starts = self._paragraph_starts()
        number = 0
        start_index = ""
        for index, sentence in enumerate(self.sentences):
            if index in starts:
                if index > 0:
                    # 收口上一段：先补换行，再把整段标上缩进
                    self.text.insert("end", "\n")
                    self._mark_paragraph(number, start_index, self.text.index("end-1c"))
                number += 1
                start_index = self.text.index("end-1c")
            self.text.insert("end", f"[{index + 1}] ", ("chip", f"chip{index}"))
            self.text.insert("end", sentence + " ", (f"s{index}", "sent"))
        if self.sentences:
            self._mark_paragraph(number, start_index, self.text.index("end-1c"))
        self.text.insert("end", "\n\n")
        self.text.insert("end", "参考译文\n", ("section",))
        if self.review_mode:
            self.text.insert("end", "以下为当时提交的参考译文。\n", ("review",))
        self._zh_start = self.text.index("end-1c")
        self._render_translations_locked()
        self.text.configure(state="disabled")

    def _render_translations(self) -> None:
        self.text.configure(state="normal")
        self._render_translations_locked()
        self.text.configure(state="disabled")

    def _render_translations_locked(self) -> None:
        try:
            self.text.delete(self._zh_start, "end-1c")
        except tk.TclError:
            return
        if not self.revealed:
            self.text.insert("end", "（逐句译文会在这里出现）", ("hint",))
            return
        starts = self._paragraph_starts()
        for index in sorted(self.revealed):
            item = self.revealed[index]
            zh = item.get("zh", "")
            if not zh:
                continue
            if index in starts and index > 0:
                # 译文也按段分组，别把三段翻成一整坨
                self.text.insert("end", "\n")
            self.text.insert("end", f"{index + 1}  ", ("zh_num",))
            self.text.insert("end", zh + "\n", ("zh",))

    def _set_loading(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if message:
            self.text.insert("1.0", message, ("hint",))
            self.text.insert("end", "\n\n正在联网抓取正文，通常需要 2–8 秒。", ("sent",))
        self.text.configure(state="disabled")
        self.meta_title.configure(text="")
        self.meta_line.configure(text="")
        self.open_btn.configure(state="disabled", cursor="arrow")
        self.offline_bar.pack_forget()

    # ================================================================== 交互
    def _index_at(self, event: tk.Event) -> str:
        return self.text.index(f"@{event.x},{event.y}")

    def _tags_at(self, index: str) -> tuple[str, ...]:
        try:
            return tuple(self.text.tag_names(index))
        except tk.TclError:
            return ()

    def _sentence_at(self, index: str) -> int | None:
        for tag in self._tags_at(index):
            if tag.startswith("s") and tag[1:].isdigit():
                return int(tag[1:])
            if tag.startswith("chip") and tag[4:].isdigit():
                return int(tag[4:])
        return None

    def _word_at(self, index: str) -> str:
        try:
            start = self.text.index(f"{index} wordstart")
            end = self.text.index(f"{index} wordend")
            raw = self.text.get(start, end).strip()
        except tk.TclError:
            return ""
        if not raw or not re.search(r"[A-Za-z]", raw):
            return ""
        word = normalize_word(raw)
        if not word or word.isdigit():
            return ""
        return word

    def _on_press(self, event: tk.Event) -> None:
        self._press_xy = (event.x, event.y)
        self._dragged = False
        self.text.configure(cursor="arrow")

    def _on_drag(self, event: tk.Event) -> None:
        if self._press_xy:
            dx = abs(event.x - self._press_xy[0])
            dy = abs(event.y - self._press_xy[1])
            if dx > 4 or dy > 4:
                self._dragged = True

    def _on_release(self, event: tk.Event) -> None:
        if time.time() - self._chip_ts < 0.4:
            return
        if time.time() < self._double_ts:
            return
        if self._dragged:
            self._translate_selection()
            return
        index = self._index_at(event)
        word = self._word_at(index)
        if word:
            self._lookup_word(word)
            return
        sentence = self._sentence_at(index)
        if sentence is not None:
            self._translate_sentence(sentence)

    def _on_double(self, event: tk.Event) -> None:
        index = self._index_at(event)
        sentence = self._sentence_at(index)
        self._double_ts = time.time() + 0.5
        if sentence is not None:
            self._select_sentence(sentence)
            self._translate_sentence(sentence)

    def _on_chip_click(self, event: tk.Event) -> None:
        self._chip_ts = time.time()
        sentence = self._sentence_at(self._index_at(event))
        if sentence is not None:
            self._select_sentence(sentence)
            self._translate_sentence(sentence)

    def _translation_js(self, index: int) -> str:
        return f"t{index}"

    def _select_sentence(self, index: int) -> None:
        try:
            self.text.tag_remove("hover_sent", "1.0", "end")
            start = f"s{index}.first"
            end = f"s{index}.last"
            self.text.tag_add("hover_sent", start, end)
            self.text.see(start)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 查词
    def _lookup_word(self, word: str) -> None:
        if self._word_job:
            self._word_job.cancel()
        self.app.sidebar.show_word_loading(word)
        started = time.time()

        def work():
            return self.ctx.dictionary.lookup(word)

        def ok(entry):
            self.app.sidebar.show_word(
                entry,
                elapsed_ms=int((time.time() - started) * 1000),
                cached=entry.cached,
            )

        def err(exc):
            self.app.sidebar.show_word_error(word, getattr(exc, "message", str(exc)))

        self._word_job = self.app.runner.run(work, ok, err, name=f"word:{word}")

    def _translate_sentence(self, index: int) -> None:
        if index < 0 or index >= len(self.sentences):
            return
        self.app.sidebar.show_translation_loading(self.sentences[index])
        started = time.time()
        text = self.sentences[index]
        bundled = self.bundled[index] if index < len(self.bundled) else ""

        def work():
            if bundled:
                from ..translate import TranslationResult

                return TranslationResult(
                    text=bundled,
                    provider="bundled",
                    source_label="内置示例译文",
                    note="离线示例文章自带参考译文。",
                )
            return self.ctx.translator.translate(text)

        def ok(result):
            self.app.sidebar.show_translation(
                text, result, elapsed_ms=int((time.time() - started) * 1000)
            )

        def err(exc):
            self.app.sidebar.show_translation_error(text, getattr(exc, "message", str(exc)))

        self._sel_job = self.app.runner.run(work, ok, err, name=f"sent:{index}")

    def _translate_selection(self) -> None:
        try:
            selected = self.text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            return
        if len(selected) < 2:
            return
        letters = sum(ch.isalpha() and ch.isascii() for ch in selected)
        if letters < 2:
            self.app.toast("请选择英文短语或句子")
            return
        self.app.sidebar.show_translation_loading(selected)
        started = time.time()

        def work():
            return self.ctx.translator.translate(selected)

        def ok(result):
            self.app.sidebar.show_translation(
                selected, result, elapsed_ms=int((time.time() - started) * 1000)
            )

        def err(exc):
            self.app.sidebar.show_translation_error(selected, getattr(exc, "message", str(exc)))

        if self._sel_job:
            self._sel_job.cancel()
        self._sel_job = self.app.runner.run(work, ok, err, name="selection")

    # ------------------------------------------------------------------ 悬停
    def _on_motion(self, event: tk.Event) -> None:
        if self._dragged:
            self._clear_hover()
            return
        index = self._index_at(event)
        tags = self._tags_at(index)
        if "chip" in tags:
            self.text.configure(cursor="hand2")
            self._clear_hover()
            return
        word = self._word_at(index)
        if word:
            self.text.configure(cursor="hand2")
            try:
                start = self.text.index(f"{index} wordstart")
                end = self.text.index(f"{index} wordend")
            except tk.TclError:
                return
            if self._hover_word != (start, end):
                self._clear_hover()
                self.text.tag_add("hover_word", start, end)
                self._hover_word = (start, end)
            sentence = self._sentence_at(index)
            if sentence is not None and sentence != self._hover_sent:
                self._clear_sent_hover()
                try:
                    self.text.tag_add("hover_sent", f"s{sentence}.first", f"s{sentence}.last")
                    self._hover_sent = sentence
                except tk.TclError:
                    pass
            return
        self.text.configure(cursor="arrow")
        self._clear_hover()

    def _clear_sent_hover(self) -> None:
        try:
            self.text.tag_remove("hover_sent", "1.0", "end")
        except tk.TclError:
            pass
        self._hover_sent = None

    def _clear_hover(self) -> None:
        try:
            self.text.tag_remove("hover_word", "1.0", "end")
        except tk.TclError:
            pass
        self._hover_word = None
        self._clear_sent_hover()

    # ------------------------------------------------------------------ 逐句
    def _next_hidden(self) -> int | None:
        for index in range(len(self.sentences)):
            if index not in self.revealed:
                return index
        return None

    def _update_action_state(self) -> None:
        total = len(self.sentences)
        done = len([i for i in self.revealed if self.revealed[i].get("zh")])
        self.reveal_btn.configure(
            text=f"显示下一句译文（{done}/{total}）" if total else "显示下一句译文"
        )
        if self.review_mode:
            self.complete_btn.state(["disabled"])
            self.reveal_btn.state(["disabled"])
            return
        if done >= total and total:
            self.complete_btn.state(["!disabled"])
            self.reveal_btn.state(["disabled"])
            self.status.configure(text=f"全部 {total} 句译文已显示，可以标记完成了。")
        else:
            self.complete_btn.state(["disabled"])
            self.reveal_btn.state(["!disabled"] if total else ["disabled"])
            self.status.configure(text=f"已看 {done}/{total} 句译文 · 看完全部后可标记完成")

    def reveal_next(self) -> None:
        index = self._next_hidden()
        if index is None:
            self.app.toast("这一段已经全部显示完了")
            return
        if self._reveal_job:
            self._reveal_job.cancel()
        sentence = self.sentences[index]
        self.reveal_btn.state(["disabled"])
        self.status.configure(text=f"正在翻译第 {index + 1} 句…")
        bundled = self.bundled[index] if index < len(self.bundled) else ""

        def work():
            if bundled:
                return {"zh": bundled, "source": "内置示例译文"}
            result = self.ctx.translator.translate(sentence)
            return {"zh": result.text, "source": result.source_label}

        def ok(payload):
            self.revealed[index] = payload
            self._render_translations()
            self._update_action_state()
            try:
                self.text.see(f"{self._zh_start} linestart")
            except tk.TclError:
                pass
            self.reveal_btn.state(["!disabled"])

        def err(exc):
            message = getattr(exc, "message", str(exc))
            self.status.configure(text=f"第 {index + 1} 句翻译失败：{message}")
            self.app.toast("翻译失败，可再点一次重试")
            self.reveal_btn.state(["!disabled"])

        self._reveal_job = self.app.runner.run(work, ok, err, name="reveal")

    def reveal_all(self) -> None:
        missing = [i for i in range(len(self.sentences)) if i not in self.revealed]
        if not missing:
            return
        if not messagebox.askyesno(
            "一次显示全部译文",
            f"还有 {len(missing)} 句未显示。\n\n一次显示会跳过逐句思考的过程，确定继续吗？",
            parent=self,
        ):
            return
        if self._reveal_job:
            self._reveal_job.cancel()
        self.all_btn.state(["disabled"])
        self.reveal_btn.state(["disabled"])
        self.status.configure(text=f"正在翻译 {len(missing)} 句…")

        def work():
            out = []
            errors = 0
            for index in missing:
                bundled = self.bundled[index] if index < len(self.bundled) else ""
                if bundled:
                    out.append((index, {"zh": bundled, "source": "内置示例译文"}))
                    continue
                try:
                    result = self.ctx.translator.translate(self.sentences[index])
                    out.append((index, {"zh": result.text, "source": result.source_label}))
                except Exception:
                    errors += 1
            return out, errors

        def ok(payload):
            out, errors = payload
            for index, item in out:
                self.revealed[index] = item
            self._render_translations()
            self._update_action_state()
            self.all_btn.state(["!disabled"])
            if errors:
                self.app.toast(f"{errors} 句翻译失败，可逐句重试")
            else:
                self.app.toast("全部译文已显示")

        def err(exc):
            self.all_btn.state(["!disabled"])
            self.status.configure(text=getattr(exc, "message", str(exc)))

        self._reveal_job = self.app.runner.run(work, ok, err, name="reveal-all")

    # ------------------------------------------------------------------ 完成
    def mark_complete(self) -> None:
        if not self.practice or not self.unit:
            return
        total = len(self.sentences)
        done = len([i for i in self.revealed if self.revealed[i].get("zh")])
        if done < total:
            self.app.toast(f"还有 {total - done} 句译文没看，先点「显示下一句译文」")
            return
        refs = [
            {
                "en": self.sentences[i],
                "zh": self.revealed[i].get("zh", ""),
                "source": self.revealed[i].get("source", ""),
            }
            for i in range(total)
        ]
        my_text = self.my_text.get("1.0", "end-1c").strip()
        newly = self.ctx.daily.complete(self.practice["id"], my_text, refs)
        self.complete_btn.state(["disabled"])
        self.refresh_progress()
        self.app.refresh_daily_badge()
        if self.ctx.daily.is_complete():
            self.app.toast("今日任务完成！")
            self._show_completion()
        else:
            span = max(1, self.unit.span)
            done = self.ctx.daily.done_count()
            remain = self.ctx.daily.remaining()
            what = f"{span} 段" if span > 1 else "1 段"
            self.app.toast(f"本次完成 {what}，今日累计 {done} 段，还差 {remain} 段")
            self.status.configure(
                text=f"本{'组' if span > 1 else '段'}已完成 · 今日 {done}/{self.ctx.daily.goal}"
            )

    def _on_complete_shortcut(self, _event=None) -> str:
        if self.winfo_ismapped() and not self.review_mode:
            self.mark_complete()
        return "break"

    def _show_completion(self) -> None:
        dialog = CompletionDialog(self, self.app)
        dialog.show()

    def next_paragraph(self) -> None:
        if self.review_mode:
            self.app.go_history()
            return
        if not self.unit or not self.article:
            self.load_new_article()
            return
        # 当前单元可能一次显示多段：从「起始段 + 实际段数」往后取，避免重复
        start = self.unit.index + max(1, self.unit.span)
        unit = self.ctx.articles.unit_at(self.article, start)
        if unit is not None:
            self.display_unit(unit)
            return
        # 这篇已经读到末尾（或索引对不上），换一篇新文章
        self.app.toast("这篇已经读到末尾，正在换一篇新的")
        self.load_new_article()

    # ------------------------------------------------------------------ 草稿
    def _on_draft_modified(self, _event=None) -> None:
        if not self.my_text.edit_modified():
            return
        self.my_text.edit_modified(False)
        if self._draft_guard or not self.practice:
            return
        self._update_draft_count()
        if self._draft_job:
            try:
                self.after_cancel(self._draft_job)
            except tk.TclError:
                pass
        self._draft_job = self.after(800, self._save_draft)

    def _save_draft(self) -> None:
        self._draft_job = None
        if not self.practice:
            return
        text = self.my_text.get("1.0", "end-1c").strip()
        try:
            self.ctx.daily.save_draft(self.practice["id"], text)
        except Exception:
            pass

    def _update_draft_count(self) -> None:
        text = self.my_text.get("1.0", "end-1c").strip()
        self.my_count.configure(text=f"{len(text)} 字 · 自动保存")
        self._sync_placeholder()

    def _sync_placeholder(self) -> None:
        try:
            has_text = bool(self.my_text.get("1.0", "end-1c").strip())
            focused = self.focus_get() is self.my_text
        except tk.TclError:
            return
        if has_text or focused:
            self.my_placeholder.place_forget()
        else:
            self.my_placeholder.place(x=14, y=13)

    def _toggle_my_panel(self) -> None:
        if self.my_box.winfo_manager():
            self.my_box.pack_forget()
            self.my_toggle.configure(text="▸  我的译文（可选）")
        else:
            self.my_box.pack(fill="x", pady=(4, 0))
            self.my_toggle.configure(text="▾  我的译文（可选）")

    def on_hide(self) -> None:
        for job in (self._word_job, self._sel_job, self._reveal_job):
            if job:
                job.cancel()
        if self._draft_job:
            self._save_draft()


class CompletionDialog(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("今日任务完成")
        self.configure(bg=theme.SURFACE)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        width, height = 460, 360
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
        self.geometry(f"{width}x{height}+{x}+{y}")

        tk.Frame(self, bg=theme.ACCENT, height=6).pack(fill="x")
        body = tk.Frame(self, bg=theme.SURFACE, padx=34, pady=24)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text="今日五段，完成。", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["hero_sm"], anchor="w",
        ).pack(anchor="w")
        summary = self.app.ctx.daily.summary()
        tk.Label(
            body,
            text=f"连续 {summary['streak']} 天达成目标　·　累计 {summary['stats']['paragraphs']} 段",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"], anchor="w",
        ).pack(anchor="w", pady=(8, 0))

        card = tk.Frame(body, bg=theme.SURFACE_ALT, padx=16, pady=14)
        card.pack(fill="x", pady=18)
        tk.Label(
            card, text="导出今日成果", bg=theme.SURFACE_ALT, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        self.version_label = tk.Label(
            card,
            text=f"将生成 v{self.app.ctx.exporter.current_version()} 的 Markdown 与 JSON 文件",
            bg=theme.SURFACE_ALT, fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w",
            justify="left",
        )
        self.version_label.pack(anchor="w", pady=(4, 0))
        self.path_label = tk.Label(
            card, text="", bg=theme.SURFACE_ALT, fg=theme.TEAL,
            font=theme.FONTS["ui_xs"], anchor="w", justify="left",
        )
        self.path_label.pack(anchor="w", pady=(4, 0))

        buttons = tk.Frame(body, bg=theme.SURFACE)
        buttons.pack(fill="x", side="bottom")
        self.export_btn = ttk.Button(
            buttons, text="导出成果", style="Accent.TButton", command=self._export
        )
        self.export_btn.pack(side="left")
        ttk.Button(
            buttons, text="再练一段", style="TButton",
            command=lambda: (self.destroy(), self.app.reader.load_new_article()),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            buttons, text="回首页", style="Ghost.TButton", command=lambda: (self.destroy(), self.app.go_start())
        ).pack(side="right")

    def _export(self) -> None:
        try:
            result = self.app.ctx.exporter.export(self.app.ctx.daily.today())
        except Exception as exc:
            self.path_label.configure(text=f"导出失败：{exc}", fg=theme.ACCENT)
            return
        paths = "\n".join(str(p) for p in result.files)
        self.path_label.configure(text=f"已导出 v{result.version}\n{paths}", fg=theme.TEAL)
        self.export_btn.state(["disabled"])
        self.app.toast(f"已导出 v{result.version}")
        self.app.open_folder(result.files[0].parent)

    def show(self) -> None:
        self.wait_window()
