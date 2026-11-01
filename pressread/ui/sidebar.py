"""右侧边栏：查词（英汉默认 / 英英按需）与选区翻译。

查词区默认只请求剑桥英汉；标题右侧有一个圆形 EN 钮，点了才去查牛津英英，
不点不发任何请求。两个标签页共享一块滚动区，按状态
（idle / loading / result / error）重绘。来源永远写在明面上。
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Any

from ..dictionary import DictEntry
from ..translate import TONES, TranslationResult
from . import theme
from .widgets import LoadingDots, ScrollFrame, Tooltip, section_label

# 牛津学习者词典的英英词条页，用于「英英」查不到时的官网兜底链接
OXFORD_WORD = "https://www.oxfordlearnersdictionaries.com/definition/english/{word}"


class TabButton(tk.Frame):
    def __init__(self, parent: tk.Misc, text: str, command):
        super().__init__(parent, bg=theme.SURFACE)
        self.command = command
        self.active = False
        self.label = tk.Label(
            self,
            text=text,
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.FONTS["ui_bold"],
            padx=14,
            pady=10,
            cursor="hand2",
        )
        self.label.pack()
        self.underline = tk.Frame(self, bg=theme.SURFACE, height=2)
        self.underline.pack(fill="x")
        for widget in (self, self.label):
            widget.bind("<Button-1>", lambda _e: self.command())

    def set_active(self, active: bool) -> None:
        self.active = active
        self.label.configure(fg=theme.ACCENT if active else theme.MUTED)
        self.underline.configure(bg=theme.ACCENT if active else theme.SURFACE)


class SidePanel(tk.Frame):
    def __init__(self, parent: tk.Misc, app, width: int = 392):
        super().__init__(
            parent, bg=theme.SURFACE, width=width, bd=0, highlightthickness=0
        )
        self.pack_propagate(False)
        self.app = app
        self.ctx = app.ctx
        self._wrap_labels: list[tk.Label] = []
        self._tab = "dict"
        self._dict_view: tuple[str, Any] = ("idle", None)
        self._trans_view: tuple[str, Any] = ("idle", None)
        self._loading: LoadingDots | None = None
        # 英英释义是「按需加载」：只有切到「英英」才会有状态
        self._dict_lang = "zh"  # 当前显示哪种释义：zh 英汉 / en 英英
        self._en_state = "idle"  # idle / loading / ready / error
        self._en_entry: DictEntry | None = None
        self._en_word = ""
        self._en_message = ""
        self._en_ms = 0
        self._word = ""

        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x", padx=20, pady=(16, 0))
        tk.Label(
            header, text="查词与翻译", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_xl"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            header, text="英汉 / 英英一键切换 · 全部本地缓存",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w", pady=(3, 0))

        tabs = tk.Frame(self, bg=theme.SURFACE)
        tabs.pack(fill="x", padx=20, pady=(10, 0))
        self.tab_dict = TabButton(tabs, "词典", lambda: self.set_tab("dict"))
        self.tab_trans = TabButton(tabs, "翻译", lambda: self.set_tab("trans"))
        self.tab_dict.pack(side="left")
        self.tab_trans.pack(side="left", padx=(6, 0))
        tk.Frame(tabs, bg=theme.LINE, height=1).pack(side="bottom", fill="x")

        self.body = ScrollFrame(self, bg=theme.SURFACE, padding=20)
        self.body.pack(fill="both", expand=True)

        footer = tk.Frame(self, bg=theme.SURFACE)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=theme.LINE, height=1).pack(fill="x")
        self.footer = tk.Label(
            footer,
            text="",
            bg=theme.SURFACE,
            fg=theme.FAINT,
            font=theme.FONTS["ui_xs"],
            anchor="w",
            padx=20,
            pady=8,
        )
        self.footer.pack(fill="x")

        self.bind("<Configure>", self._on_resize)
        self.set_tab("dict")
        self.refresh_footer()

    # ------------------------------------------------------------------ 布局
    def _on_resize(self, event: tk.Event) -> None:
        self._reflow()

    def _reflow(self) -> None:
        wrap = max(200, self.body.canvas.winfo_width() - 100)
        for label in self._wrap_labels:
            try:
                label.configure(wraplength=wrap)
            except tk.TclError:
                pass

    def _wrap(self, label: tk.Label) -> tk.Label:
        self._wrap_labels.append(label)
        try:
            label.configure(wraplength=max(200, self.body.canvas.winfo_width() - 100))
        except tk.TclError:
            pass
        return label

    def _clear(self) -> None:
        if self._loading:
            self._loading.stop()
            self._loading = None
        self.body.clear()
        self._wrap_labels.clear()

    def set_tab(self, tab: str) -> None:
        self._tab = tab
        self.tab_dict.set_active(tab == "dict")
        self.tab_trans.set_active(tab == "trans")
        self._render()

    def refresh_footer(self) -> None:
        stats = self.ctx.store.cache_stats()
        self.footer.configure(
            text=f"本地缓存 {stats['total']} 条 · 已复用 {stats['hits']} 次"
        )

    # ------------------------------------------------------------------ 状态
    def show_word_loading(self, word: str) -> None:
        self._dict_view = ("loading", word)
        if self._tab != "dict":
            self.set_tab("dict")
        else:
            self._render()

    def show_word(self, entry: DictEntry, *, elapsed_ms: int = 0, cached: bool = False) -> None:
        if entry.word != self._word:
            self._reset_en()
            self._dict_lang = "zh"  # 换了个词，先给英汉；要英英再自己切
            self._word = entry.word
        self._dict_view = ("result", (entry, elapsed_ms, cached))
        if self._tab != "dict":
            self.set_tab("dict")
        else:
            self._render()
        self.refresh_footer()

    def show_word_error(self, word: str, message: str) -> None:
        self._word = word
        self._reset_en()
        self._dict_lang = "zh"
        self._dict_view = ("error", (word, message))
        if self._tab != "dict":
            self.set_tab("dict")
        else:
            self._render()

    def show_translation_loading(self, text: str) -> None:
        self._trans_view = ("loading", text)
        if self._tab != "trans":
            self.set_tab("trans")
        else:
            self._render()

    def show_translation(
        self, source: str, result: TranslationResult, *, elapsed_ms: int = 0
    ) -> None:
        self._trans_view = ("result", (source, result, elapsed_ms))
        if self._tab != "trans":
            self.set_tab("trans")
        else:
            self._render()
        self.refresh_footer()

    def show_translation_error(self, source: str, message: str) -> None:
        self._trans_view = ("error", (source, message))
        if self._tab != "trans":
            self.set_tab("trans")
        else:
            self._render()

    def reset(self) -> None:
        self._dict_view = ("idle", None)
        self._trans_view = ("idle", None)
        self._reset_en()
        self._dict_lang = "zh"
        self._word = ""
        self._render()

    def _reset_en(self) -> None:
        self._en_state = "idle"
        self._en_entry = None
        self._en_word = ""
        self._en_message = ""

    # ------------------------------------------------------------------ 渲染
    def _render(self) -> None:
        self._clear()
        parent = self.body.inner
        if self._tab == "dict":
            self._render_dict(parent, *self._dict_view)
        else:
            self._render_trans(parent, *self._trans_view)
        self.after_idle(self._reflow)

    def _render_dict(self, parent: tk.Misc, state: str, data: Any) -> None:
        if state == "idle":
            self._render_idle(
                parent,
                glyph="汉",
                title="点一个词，查英汉",
                lines=[
                    "在左侧正文里单击任意英文单词，这里显示剑桥英汉双解的释义：",
                    "音标、词性、中文译词、英文原释义与例句。",
                    "",
                    "词条右上角可在「英汉 / 英英」之间切换，第一次切到英英才向牛津请求。",
                    "双击句子可看整句译文；拖选一段文字可翻译选区。",
                ],
            )
            return
        if state == "loading":
            self._render_loading(parent, f"正在查询剑桥英汉 · {data}")
            return
        if state == "error":
            word, message = data
            self._render_error(
                parent,
                title=f"没查到 “{word}”",
                message=message,
                link=("在剑桥词典官网打开", lambda: self.app.open_url(
                    "https://dictionary.cambridge.org/dictionary/english-chinese-simplified/"
                    + word.replace(" ", "-")
                )),
            )
            return
        entry, elapsed_ms, cached = data
        self._render_entry(parent, entry, elapsed_ms, cached)

    def _render_trans(self, parent: tk.Misc, state: str, data: Any) -> None:
        if state == "idle":
            self._render_idle(
                parent,
                glyph="译",
                title="选中，就有译文",
                lines=[
                    "用鼠标拖选正文中的短语或句子，松开鼠标即可翻译。",
                    "双击句子、或点击句首编号 [1]，也能整句翻译。",
                    "",
                    "译文来自真实翻译站点，逐句结果会写入本地缓存。",
                ],
            )
            return
        if state == "loading":
            preview = data if len(data) <= 60 else data[:60] + "…"
            self._render_loading(parent, f"正在翻译 · {preview}")
            return
        if state == "error":
            source, message = data
            self._render_error(parent, title="这次没翻成", message=message)
            return
        source, result, elapsed_ms = data
        self._render_translation(parent, source, result, elapsed_ms)

    # ------------------------------------------------------------------ 通用块
    def _render_idle(self, parent: tk.Misc, *, glyph: str, title: str, lines: list[str]) -> None:
        canvas = tk.Canvas(
            parent, width=76, height=76, bg=theme.SURFACE, bd=0, highlightthickness=0
        )
        canvas.pack(anchor="w", pady=(6, 14))
        canvas.create_oval(2, 2, 74, 74, outline=theme.LINE_STRONG, width=1, fill=theme.SURFACE_ALT)
        canvas.create_text(38, 36, text=glyph, fill=theme.ACCENT, font=theme.FONTS["hero_sm"])
        tk.Label(
            parent, text=title, bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_xl"], anchor="w",
        ).pack(anchor="w")
        for line in lines:
            self._wrap(
                tk.Label(
                    parent, text=line, bg=theme.SURFACE, fg=theme.MUTED,
                    font=theme.FONTS["ui_sm"], anchor="w", justify="left",
                )
            ).pack(anchor="w", pady=(6 if line else 0, 0))

    def _render_loading(self, parent: tk.Misc, text: str) -> None:
        box = tk.Frame(parent, bg=theme.SURFACE_ALT, padx=16, pady=18)
        box.pack(fill="x", pady=(4, 0))
        tk.Label(
            box, text="查询中", bg=theme.SURFACE_ALT, fg=theme.ACCENT,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        self._loading = LoadingDots(box, text, bg=theme.SURFACE_ALT)
        self._loading.pack(anchor="w", pady=(6, 0))
        self._loading.start()
        self._wrap(
            tk.Label(
                box, text="首次查询需要联网，之后会直接命中本地缓存。",
                bg=theme.SURFACE_ALT, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
                justify="left", anchor="w",
            )
        ).pack(anchor="w", pady=(10, 0))

    def _render_error(self, parent: tk.Misc, *, title: str, message: str, link=None) -> None:
        box = tk.Frame(parent, bg=theme.ACCENT_SOFT, padx=16, pady=16)
        box.pack(fill="x", pady=(4, 0))
        tk.Label(
            box, text=title, bg=theme.ACCENT_SOFT, fg=theme.ACCENT_DARK,
            font=theme.FONTS["ui_bold"], anchor="w", justify="left",
        ).pack(anchor="w")
        self._wrap(
            tk.Label(
                box, text=message, bg=theme.ACCENT_SOFT, fg=theme.INK_SOFT,
                font=theme.FONTS["ui_sm"], anchor="w", justify="left",
            )
        ).pack(anchor="w", pady=(6, 0))
        if link:
            text, command = link
            btn = tk.Label(
                box, text=text, bg=theme.ACCENT_SOFT, fg=theme.ACCENT,
                font=theme.FONTS["ui_sm"], cursor="hand2", anchor="w",
            )
            btn.pack(anchor="w", pady=(8, 0))
            btn.bind("<Button-1>", lambda _e: command())

    def _render_entry(self, parent: tk.Misc, entry: DictEntry, elapsed_ms: int, cached: bool) -> None:
        head = tk.Frame(parent, bg=theme.SURFACE)
        head.pack(fill="x")
        # 双状态按钮先 pack 到右边，标题再长也不会把它挤掉
        self._lang_toggle(head).pack(side="right", padx=(10, 0), pady=(2, 0))
        tk.Label(
            head, text=entry.word, bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["hero_sm"], anchor="w",
        ).pack(side="left")
        if self._dict_lang == "zh":
            badge_bg, badge_fg, badge_text = theme.TEAL_SOFT, theme.TEAL, "剑桥 · 英汉"
        else:
            badge_bg, badge_fg, badge_text = theme.ACCENT_SOFT, theme.ACCENT, "牛津 · 英英"
        tk.Label(
            head, text=badge_text,
            bg=badge_bg, fg=badge_fg, font=theme.FONTS["ui_xs"], padx=7, pady=2,
        ).pack(side="left", padx=(8, 0), pady=(8, 0))

        if self._dict_lang == "zh":
            self._entry_body(parent, entry, elapsed_ms=elapsed_ms, cached=cached)
        else:
            self._entry_body_en(parent)

    # ------------------------------------------------------- 英汉 / 英英 切换
    def _lang_toggle(self, parent: tk.Misc) -> tk.Frame:
        """「英汉 | 英英」双状态按钮：切换的是同一块区域显示哪一种释义。"""
        box = tk.Frame(parent, bg=theme.LINE_STRONG, padx=1, pady=1, bd=0)
        inner = tk.Frame(box, bg=theme.SURFACE_ALT)
        inner.pack()
        for lang, text in (("zh", "英汉"), ("en", "英英")):
            active = self._dict_lang == lang
            label = tk.Label(
                inner, text=text, font=theme.FONTS["ui_xs"], padx=10, pady=3,
                bg=theme.ACCENT if active else theme.SURFACE_ALT,
                fg=theme.SURFACE if active else theme.MUTED,
                cursor="hand2",
            )
            label.pack(side="left")
            label.bind("<Button-1>", lambda _e, lang=lang: self._set_dict_lang(lang))
        Tooltip(box, "同一块区域切换英汉 / 英英释义；第一次切到英英才会向牛津请求")
        return box

    def _set_dict_lang(self, lang: str) -> None:
        if lang not in ("zh", "en") or lang == self._dict_lang:
            return
        self._dict_lang = lang
        if lang == "en" and self._en_state in ("idle", "error"):
            # 按需加载：只有真的切过来才去查牛津，英汉那一侧不受影响
            self._load_en(force=self._en_state == "error")
            return
        self._render()

    # ------------------------------------------------------------- 释义主体
    def _phonetics(self, parent: tk.Misc, entry: DictEntry) -> None:
        phon = []
        if entry.phonetics.get("uk"):
            phon.append(("英", entry.phonetics["uk"]))
        if entry.phonetics.get("us"):
            phon.append(("美", entry.phonetics["us"]))
        if not phon:
            return
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=(4, 0))
        for label, value in phon:
            tk.Label(
                row, text=label, bg=theme.SURFACE, fg=theme.FAINT,
                font=theme.FONTS["ui_xs"],
            ).pack(side="left")
            tk.Label(
                row, text=value, bg=theme.SURFACE, fg=theme.INK_SOFT,
                font=theme.FONTS["mono"],
            ).pack(side="left", padx=(3, 12))

    def _entry_body(
        self, parent: tk.Misc, entry: DictEntry, *, elapsed_ms: int, cached: bool
    ) -> None:
        """英汉与英英共用一套排版：音标 → 义项 → 习语 → 词源 → 操作 → 来源。"""
        self._phonetics(parent, entry)

        count = len(entry.senses)
        tk.Label(
            parent,
            text=f"{count} 个义项" + (f" · {len(entry.idioms)} 条习语" if entry.idioms else ""),
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w", pady=(6, 2))

        number = 0
        shown = 0
        limit = 12
        for pos, senses in entry.grouped():
            if pos and shown < limit:
                tk.Label(
                    parent, text=pos.upper(), bg=theme.SURFACE, fg=theme.ACCENT,
                    font=theme.FONTS["ui_xs"], anchor="w",
                ).pack(anchor="w", pady=(12, 4))
            for sense in senses:
                if shown >= limit:
                    break
                number += 1
                shown += 1
                self._render_sense(parent, number, sense)
        if count > shown:
            tk.Label(
                parent,
                text=f"还有 {count - shown} 个义项，点下方官网链接看完整词条",
                bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="w",
            ).pack(anchor="w", pady=(8, 0))

        if entry.idioms:
            self._section(parent, "习语与短语")
            for idiom in entry.idioms[:8]:
                self._wrap(
                    tk.Label(
                        parent, text=idiom.get("phrase", ""), bg=theme.SURFACE,
                        fg=theme.INK, font=theme.FONTS["ui_bold"], anchor="w", justify="left",
                    )
                ).pack(anchor="w", pady=(6, 0))
                if idiom.get("definition"):
                    self._wrap(
                        tk.Label(
                            parent, text=idiom["definition"], bg=theme.SURFACE,
                            fg=theme.INK_SOFT, font=theme.FONTS["def"], anchor="w", justify="left",
                        )
                    ).pack(anchor="w", padx=(10, 0), pady=(2, 0))

        if entry.origin:
            self._section(parent, "词源")
            self._wrap(
                tk.Label(
                    parent, text=entry.origin, bg=theme.SURFACE, fg=theme.MUTED,
                    font=theme.FONTS["ui_sm"], anchor="w", justify="left",
                )
            ).pack(anchor="w")

        self._actions(parent, entry)
        self._meta(
            parent,
            source=entry.source_label,
            right=f"{elapsed_ms / 1000:.2f}s" if not cached else "来自本地缓存",
            cached=cached,
            note=entry.note,
        )

    def _entry_body_en(self, parent: tk.Misc) -> None:
        """英英那一侧：只显示牛津的结果，加载中/失败也在这块区域里。"""
        word = self._word or self._en_word
        if self._en_state == "loading":
            self._render_loading(parent, f"正在查询牛津英英 · {word}")
            return
        if self._en_state != "ready" or self._en_entry is None:
            self._render_error(
                parent,
                title=f"没拿到 “{word}” 的英英释义",
                message=self._en_message or "牛津暂时没有返回结果，可以稍后再试。",
                link=("在牛津官网打开", lambda: self.app.open_url(
                    OXFORD_WORD.format(word=word.replace(" ", "-"))
                )),
            )
            return
        entry = self._en_entry
        self._entry_body(parent, entry, elapsed_ms=self._en_ms, cached=entry.cached)

    def _actions(self, parent: tk.Misc, entry: DictEntry) -> None:
        actions = tk.Frame(parent, bg=theme.SURFACE)
        actions.pack(fill="x", pady=(16, 6))
        saved = self.ctx.store.is_saved_word(entry.word)
        star = tk.Button(
            actions,
            text="★ 已收藏" if saved else "☆ 收藏",
            command=lambda: self._toggle_word(entry),
            bg=theme.SURFACE,
            fg=theme.GOLD if saved else theme.INK_SOFT,
            activebackground=theme.SURFACE_ALT,
            activeforeground=theme.GOLD,
            relief="flat",
            bd=0,
            font=theme.FONTS["ui_sm"],
            cursor="hand2",
            padx=6,
            pady=4,
        )
        star.pack(side="left")
        copy_btn = tk.Button(
            actions, text="复制释义", command=lambda: self._copy_entry(entry),
            bg=theme.SURFACE, fg=theme.INK_SOFT, activebackground=theme.SURFACE_ALT,
            relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2", padx=6, pady=4,
        )
        copy_btn.pack(side="left", padx=(6, 0))
        if entry.source_url:
            site = tk.Button(
                actions,
                text="剑桥官网 ↗" if entry.provider == "cambridge" else "官网 ↗",
                command=lambda: self.app.open_url(entry.source_url),
                bg=theme.SURFACE, fg=theme.INK_SOFT, activebackground=theme.SURFACE_ALT,
                relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2", padx=6, pady=4,
            )
            site.pack(side="left", padx=(6, 0))

    def _render_sense(self, parent: tk.Misc, number: int, sense) -> None:
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(
            row, text=str(number), bg=theme.SURFACE, fg=theme.FAINT,
            font=theme.FONTS["ui_sm"], width=2, anchor="ne",
        ).pack(side="left", anchor="n")
        col = tk.Frame(row, bg=theme.SURFACE)
        col.pack(side="left", fill="x", expand=True)
        definition = sense.definition
        self._wrap(
            tk.Label(
                col, text=definition, bg=theme.SURFACE, fg=theme.INK,
                font=theme.FONTS["def"], anchor="w", justify="left",
            )
        ).pack(anchor="w")
        # 英汉条目：中文译词下面附英文原释义，方便对照
        gloss = getattr(sense, "gloss", "")
        if gloss and gloss != definition:
            self._wrap(
                tk.Label(
                    col, text=gloss, bg=theme.SURFACE, fg=theme.MUTED,
                    font=theme.FONTS["example"], anchor="w", justify="left",
                )
            ).pack(anchor="w", pady=(1, 0))
        extras = " · ".join(x for x in [sense.grammar, *sense.labels] if x)
        if extras:
            self._wrap(
                tk.Label(
                    col, text=extras, bg=theme.SURFACE, fg=theme.FAINT,
                    font=theme.FONTS["ui_xs"], anchor="w", justify="left",
                )
            ).pack(anchor="w", pady=(1, 0))
        for example in sense.examples[:2]:
            self._wrap(
                tk.Label(
                    col, text=f"— {example}", bg=theme.SURFACE, fg=theme.MUTED,
                    font=theme.FONTS["example"], anchor="w", justify="left",
                )
            ).pack(anchor="w", padx=(0, 2), pady=(2, 0))

    # ------------------------------------------------------------ 英英（按需）
    def _current_entry(self) -> DictEntry | None:
        state, data = self._dict_view
        if state != "result":
            return None
        return data[0]

    def _load_en(self, *, force: bool = False) -> None:
        """切到「英英」时查牛津；不切就不发请求。"""
        entry = self._current_entry()
        if entry is None or self._en_state == "loading":
            return
        word = entry.word
        self._en_state = "loading"
        self._en_word = word
        self._en_message = ""
        self._render()
        started = time.time()

        def work():
            return self.ctx.dictionary.lookup_en(word, force=force)

        def ok(en_entry):
            if self._word != word:
                return  # 用户已经点了别的词，迟到的结果直接丢弃
            self._en_entry = en_entry
            self._en_state = "ready"
            self._en_ms = int((time.time() - started) * 1000)
            if self._dict_lang == "en":
                self._render()

        def err(exc):
            if self._word != word:
                return
            self._en_state = "error"
            self._en_message = getattr(exc, "message", str(exc))
            if self._dict_lang == "en":
                self._render()

        self.app.runner.run(work, ok, err, name=f"en:{word}")

    def _render_translation(
        self, parent: tk.Misc, source: str, result: TranslationResult, elapsed_ms: int
    ) -> None:
        head = tk.Frame(parent, bg=theme.SURFACE)
        head.pack(fill="x")
        tk.Label(
            head, text="参考译文", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_xl"], anchor="w",
        ).pack(side="left")
        bundled = result.provider == "bundled"
        tk.Label(
            head, text="离线示例" if bundled else "机器翻译",
            bg=theme.GOLD_SOFT if bundled else theme.TEAL_SOFT,
            fg=theme.GOLD if bundled else theme.TEAL,
            font=theme.FONTS["ui_xs"], padx=7, pady=2,
        ).pack(side="left", padx=(8, 0), pady=(4, 0))

        source_box = tk.Frame(parent, bg=theme.SURFACE_ALT, padx=12, pady=10)
        source_box.pack(fill="x", pady=(12, 14))
        section_label(source_box, "原文", bg=theme.SURFACE_ALT).pack(anchor="w")
        text = source if len(source) <= 320 else source[:320] + "…"
        self._wrap(
            tk.Label(
                source_box, text=text, bg=theme.SURFACE_ALT, fg=theme.INK_SOFT,
                font=theme.FONTS["example"], anchor="w", justify="left",
            )
        ).pack(anchor="w", pady=(4, 0))

        self._wrap(
            tk.Label(
                parent, text=result.text, bg=theme.SURFACE, fg=theme.INK,
                font=theme.FONTS["zh_lg"], anchor="w", justify="left",
            )
        ).pack(anchor="w")

        if not bundled:
            self._render_tone_row(parent, result)

        if result.alternatives:
            self._section(parent, "其他译法")
            for alt in result.alternatives:
                self._wrap(
                    tk.Label(
                        parent, text=f"· {alt}", bg=theme.SURFACE, fg=theme.MUTED,
                        font=theme.FONTS["zh_sm"], anchor="w", justify="left",
                    )
                ).pack(anchor="w", pady=(3, 0))

        actions = tk.Frame(parent, bg=theme.SURFACE)
        actions.pack(fill="x", pady=(16, 4))
        tk.Button(
            actions, text="复制译文", command=lambda: self._copy_text(result.text),
            bg=theme.SURFACE, fg=theme.INK_SOFT, activebackground=theme.SURFACE_ALT,
            relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2", padx=6, pady=4,
        ).pack(side="left")
        if result.source_url:
            tk.Button(
                actions, text="打开翻译站点 ↗", command=lambda: self.app.open_url(result.source_url),
                bg=theme.SURFACE, fg=theme.INK_SOFT, activebackground=theme.SURFACE_ALT,
                relief="flat", bd=0, font=theme.FONTS["ui_sm"], cursor="hand2", padx=6, pady=4,
            ).pack(side="left", padx=(6, 0))

        if bundled:
            right = "程序内置"
        elif result.cached:
            right = "来自本地缓存"
        else:
            right = f"{elapsed_ms / 1000:.2f}s"
        self._meta(
            parent,
            source=result.source_label,
            right=right,
            cached=result.cached and not bundled,
            note=result.note,
        )

    # ------------------------------------------------------------ 语气档
    def _render_tone_row(self, parent: tk.Misc, result: TranslationResult) -> None:
        """正式 / 非正式语气切换。必应免 Key 支持，其余源如实说明。"""
        capable = result.tone_capable or self.ctx.translator.supports_tone()
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=(14, 0))
        tk.Label(
            row, text="语气", bg=theme.SURFACE, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"],
        ).pack(side="left")
        for tone, label in TONES.items():
            active = result.tone == tone
            available = capable or tone == "default"
            chip = tk.Label(
                row,
                text=label,
                bg=theme.ACCENT_SOFT if active else theme.SURFACE_ALT,
                fg=theme.ACCENT if active else (theme.INK_SOFT if available else theme.FAINT),
                font=theme.FONTS["ui_xs"],
                padx=9,
                pady=3,
                cursor="hand2" if available else "arrow",
            )
            chip.pack(side="left", padx=(6, 0))
            chip.bind("<Button-1>", lambda _e, t=tone: self._retranslate(t))
        if not capable:
            self._wrap(
                tk.Label(
                    parent,
                    text="当前翻译源不区分语气。在设置 → 词典与翻译里勾选「必应翻译」"
                         "（微软 Translator，免 Key）即可切「正式 / 非正式」。",
                    bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
                    anchor="w", justify="left",
                )
            ).pack(anchor="w", pady=(6, 0))

    def _retranslate(self, tone: str) -> None:
        state, data = self._trans_view
        if state != "result":
            return
        source, result, _elapsed = data
        if tone == result.tone:
            return
        if tone != "default" and not self.ctx.translator.supports_tone():
            self.app.toast("语气档需要必应翻译：设置 → 词典与翻译")
            return
        self.show_translation_loading(source)
        started = time.time()

        def work():
            return self.ctx.translator.translate(source, tone=tone)

        def ok(new_result):
            self.show_translation(
                source, new_result, elapsed_ms=int((time.time() - started) * 1000)
            )

        def err(exc):
            self.show_translation_error(source, getattr(exc, "message", str(exc)))

        self.app.runner.run(work, ok, err, name=f"tone:{tone}")

    def _section(self, parent: tk.Misc, title: str) -> None:
        tk.Frame(parent, bg=theme.LINE, height=1).pack(fill="x", pady=(16, 8))
        tk.Label(
            parent, text=title, bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w")

    def _meta(self, parent: tk.Misc, *, source: str, right: str, cached: bool, note: str = "") -> None:
        tk.Frame(parent, bg=theme.LINE, height=1).pack(fill="x", pady=(14, 8))
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x")
        # 来源名只取「 · 」之前的主名，否则会和右侧的耗时/缓存徽标挤在一起
        short = (source or "").split(" · ")[0]
        if len(short) > 24:
            short = short[:23] + "…"
        # 先 pack 右侧徽标，再放左侧文字，避免被长文本挤出可视区
        tk.Label(
            row, text=right, bg=theme.TEAL_SOFT if cached else theme.SURFACE,
            fg=theme.TEAL if cached else theme.FAINT,
            font=theme.FONTS["ui_xs"], padx=6 if cached else 0, pady=1,
        ).pack(side="right")
        tk.Label(
            row, text=short,
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        if note:
            self._wrap(
                tk.Label(
                    parent, text=note, bg=theme.SURFACE, fg=theme.FAINT,
                    font=theme.FONTS["ui_xs"], anchor="w", justify="left",
                )
            ).pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------------ 动作
    def _toggle_word(self, entry: DictEntry) -> None:
        store = self.ctx.store
        if store.is_saved_word(entry.word):
            store.remove_word(entry.word)
            self.app.toast("已取消收藏")
        else:
            store.add_word(entry.word, entry.to_dict(), entry.source_label)
            self.app.toast("已加入生词本")
        self._render()

    def _copy_entry(self, entry: DictEntry) -> None:
        lines = [entry.word]
        if entry.phonetics:
            lines.append(" ".join(f"/{v.strip('/')}/" for v in entry.phonetics.values()))
        for pos, senses in entry.grouped():
            if pos:
                lines.append(f"[{pos}]")
            for sense in senses:
                lines.append(f"- {sense.definition}")
        self._copy_text("\n".join(lines))

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.toast("已复制到剪贴板")
