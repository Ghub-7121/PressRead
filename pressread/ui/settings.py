"""设置窗口：来源、词典与翻译、缓存、外观与每日目标。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .. import __version__
from ..config import DEFAULT_SOURCES
from ..feeds import CATEGORY_ORDER, PRESETS, SOURCES, filter_sources
from . import theme
from .widgets import CheckRow, MiniSlider, ScrollFrame, Tooltip


def _short_path(path: str, limit: int = 44) -> str:
    """路径太长会把底部说明挤到按钮上，这里只保留尾部。"""
    return path if len(path) <= limit else "…" + path[-(limit - 1):]


class SourceRow(tk.Frame):
    """紧凑的来源行：一行放下中英文名，勾选框自绘，悬停出简介。

    旧版每个来源占三行（名称 + 简介 + 间距），50 个源要滚好几屏；
    这里压到 26px 一行、两列排布，配合左侧分类与搜索能直接定位。
    """

    def __init__(
        self,
        parent: tk.Misc,
        source,
        variable: tk.BooleanVar,
        *,
        on_toggle=None,
        on_hover=None,
    ):
        super().__init__(parent, bg=theme.SURFACE, cursor="hand2")
        self.source = source
        self.variable = variable
        self._on_toggle = on_toggle
        self._on_hover = on_hover
        self._hover = False
        self.canvas = tk.Canvas(
            self, width=16, height=16, bg=theme.SURFACE, bd=0,
            highlightthickness=0, cursor="hand2",
        )
        self.canvas.pack(side="left", padx=(3, 6), pady=5)
        self.name_label = tk.Label(
            self, text=source.name, bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        self.name_label.pack(side="left")
        self.zh_label = tk.Label(
            self, text=source.chinese_name, bg=theme.SURFACE, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"], anchor="w",
        )
        self.zh_label.pack(side="left", padx=(6, 0))
        for widget in (self, self.canvas, self.name_label, self.zh_label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
        try:
            variable.trace_add("write", lambda *_a: self.refresh())
        except tk.TclError:
            pass
        self.refresh()

    def _toggle(self, _event=None) -> str:
        self.variable.set(not bool(self.variable.get()))
        if self._on_toggle:
            self._on_toggle()
        return "break"

    def _enter(self, _event=None) -> None:
        try:
            self._hover = True
            self._paint(bg=theme.SURFACE_ALT)
            if self._on_hover:
                self._on_hover(self.source)
        except tk.TclError:
            pass

    def _leave(self, event=None) -> None:
        # 指针在行内子控件之间移动也会触发 <Leave>，先确认真的离开了整行；
        # 列表重建时控件可能已被销毁，这里一并吞掉 TclError。
        try:
            if event is not None:
                under = self.winfo_containing(event.x_root, event.y_root)
                while under is not None:
                    if under is self:
                        return
                    under = getattr(under, "master", None)
            self._hover = False
            self._paint(bg=theme.SURFACE)
        except tk.TclError:
            pass

    def _paint(self, bg: str) -> None:
        for widget in (self, self.canvas, self.name_label, self.zh_label):
            if widget.winfo_exists():
                widget.configure(bg=bg)
        self._draw()

    def refresh(self) -> None:
        self._draw()

    def _draw(self) -> None:
        if not self.canvas.winfo_exists():
            return
        checked = bool(self.variable.get())
        self.canvas.delete("all")
        border = theme.ACCENT if (checked or self._hover) else theme.LINE_STRONG
        self.canvas.create_rectangle(
            0, 0, 15, 15, outline=border,
            fill=theme.ACCENT if checked else (theme.SURFACE_ALT if self._hover else theme.SURFACE),
            width=1.3,
        )
        if checked:
            self.canvas.create_line(
                3.8, 7.8, 6.6, 10.6, 12.4, 4.2,
                fill="#FFFFFF", width=1.8, capstyle="round", joinstyle="round",
            )


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.ctx = app.ctx
        self.config = app.ctx.config
        self.title("设置")
        self.configure(bg=theme.BG)
        self.transient(parent.winfo_toplevel())
        self.minsize(780, 580)
        width, height = 880, 690
        root = parent.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + max(20, (root.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.source_vars: dict[str, tk.BooleanVar] = {}
        self._build()
        self.grab_set()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        header = tk.Frame(self, bg=theme.SURFACE)
        header.pack(fill="x")
        tk.Frame(header, bg=theme.LINE, height=1).pack(fill="x", side="bottom")
        tk.Label(
            header, text="设置", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_xl"], anchor="w",
        ).pack(side="left", padx=24, pady=14)
        tk.Label(
            header, text="修改后点「保存设置」生效", bg=theme.SURFACE, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"],
        ).pack(side="left", padx=(8, 0), pady=(18, 0))

        style = ttk.Style(self)
        style.configure("Settings.TNotebook", background=theme.BG, borderwidth=0)
        style.configure(
            "Settings.TNotebook.Tab", padding=(18, 9), font=theme.FONTS["ui_bold"],
            background=theme.BG_DEEP, foreground=theme.INK_SOFT, bordercolor=theme.LINE,
        )
        style.map(
            "Settings.TNotebook.Tab",
            background=[("selected", theme.SURFACE)],
            foreground=[("selected", theme.ACCENT)],
        )

        notebook = ttk.Notebook(self, style="Settings.TNotebook")
        notebook.pack(fill="both", expand=True, padx=18, pady=(12, 0))

        self.tab_sources = tk.Frame(notebook, bg=theme.SURFACE)
        self.tab_dict = tk.Frame(notebook, bg=theme.SURFACE)
        self.tab_cache = tk.Frame(notebook, bg=theme.SURFACE)
        self.tab_ui = tk.Frame(notebook, bg=theme.SURFACE)
        notebook.add(self.tab_sources, text="外刊来源")
        notebook.add(self.tab_dict, text="词典与翻译")
        notebook.add(self.tab_cache, text="本地缓存")
        notebook.add(self.tab_ui, text="目标与外观")

        self._build_sources()
        self._build_dict()
        self._build_cache()
        self._build_ui()

        footer = tk.Frame(self, bg=theme.SURFACE)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=theme.LINE, height=1).pack(fill="x", side="top")
        inner = tk.Frame(footer, bg=theme.SURFACE)
        inner.pack(fill="x", padx=20, pady=12)
        # 按钮先于长路径文字占位，否则窄窗口下「取消」会被挤没
        buttons = tk.Frame(inner, bg=theme.SURFACE)
        buttons.pack(side="right")
        ttk.Button(buttons, text="保存设置", style="Accent.TButton", command=self._save).pack(
            side="right"
        )
        ttk.Button(buttons, text="取消", style="TButton", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        tk.Label(
            inner, text=f"PressRead v{__version__}　·　配置保存在 {_short_path(str(self.config.path))}",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(side="left", fill="x", expand=True)

    def _scroll(self, parent: tk.Misc) -> tk.Frame:
        frame = ScrollFrame(parent, bg=theme.SURFACE, padding=22, auto_hide=True)
        frame.pack(fill="both", expand=True)
        return frame.inner

    # ------------------------------------------------------------- 来源
    def _build_sources(self) -> None:
        current = self.config.get("sources", default={}) or {}
        self.source_vars = {}
        for source in SOURCES:
            self.source_vars[source.id] = tk.BooleanVar(
                value=bool(current.get(source.id, False))
            )
        self._active_category = "全部"
        self._rows: list[SourceRow] = []
        self._nav_labels: dict[str, tuple[tk.Frame, tk.Frame, tk.Label, tk.Label]] = {}

        wrap = tk.Frame(self.tab_sources, bg=theme.SURFACE)
        wrap.pack(fill="both", expand=True, padx=22, pady=(16, 0))

        head = tk.Frame(wrap, bg=theme.SURFACE)
        head.pack(fill="x")
        tk.Label(
            head, text="外刊来源", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"],
        ).pack(side="left")
        self.count_label = tk.Label(
            head, text="", bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
        )
        self.count_label.pack(side="left", padx=(10, 0))
        self.test_btn = ttk.Button(
            head, text="测试连接", style="TButton", command=self._test_sources
        )
        self.test_btn.pack(side="right", padx=(8, 0))
        ttk.Button(
            head, text="恢复默认", style="TButton", command=self._reset_sources
        ).pack(side="right")

        # 搜索 + 批量：搜索框吃满剩余宽度
        bar = tk.Frame(wrap, bg=theme.SURFACE)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="清空", style="TButton", width=6,
                   command=lambda: self._bulk(False)).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="全选", style="TButton", width=6,
                   command=lambda: self._bulk(True)).pack(side="right", padx=(6, 0))
        tk.Label(
            bar, text="搜索", bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
        ).pack(side="left", padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_a: self._refresh_list())
        self.search_entry.bind("<Escape>", lambda _e: self.search_var.set(""))

        # 快捷方案
        pre = tk.Frame(wrap, bg=theme.SURFACE)
        pre.pack(fill="x", pady=(8, 0))
        tk.Label(
            pre, text="快捷方案", bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_xs"],
        ).pack(side="left")
        for _key, label, hint, ids in PRESETS:
            chip = tk.Label(
                pre, text=label, bg=theme.BG_DEEP, fg=theme.INK_SOFT,
                font=theme.FONTS["ui_xs"], padx=9, pady=3, cursor="hand2",
            )
            chip.pack(side="left", padx=(6, 0))
            chip.bind("<Button-1>", lambda _e, ids=ids, label=label: self._apply_preset(ids, label))
            chip.bind("<Enter>", lambda _e, c=chip: c.configure(bg=theme.ACCENT_SOFT, fg=theme.ACCENT))
            chip.bind("<Leave>", lambda _e, c=chip: c.configure(bg=theme.BG_DEEP, fg=theme.INK_SOFT))
            Tooltip(chip, hint)

        # 左分类 / 右列表
        body = tk.Frame(wrap, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, pady=(10, 0))

        nav = tk.Frame(body, bg=theme.SURFACE_ALT, width=128)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(
            nav, text="分类", bg=theme.SURFACE_ALT, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 2))
        for cat in ["全部"] + CATEGORY_ORDER:
            self._nav_labels[cat] = self._nav_row(nav, cat)

        self.list_scroll = ScrollFrame(
            body, bg=theme.SURFACE, padding=12, auto_hide=True
        )
        self.list_scroll.pack(side="left", fill="both", expand=True)
        self.list_grid = self.list_scroll.inner

        self.hint_label = tk.Label(
            wrap, text="点击名称切换启用；鼠标悬停看简介。", bg=theme.SURFACE,
            fg=theme.MUTED, font=theme.FONTS["ui_xs"], anchor="w", justify="left",
        )
        self.hint_label.pack(fill="x", pady=(8, 10))
        wrap.bind("<Configure>", lambda e: self.hint_label.configure(wraplength=max(200, e.width - 20)))

        self._refresh_list()

    def _nav_row(self, parent: tk.Misc, cat: str) -> tuple[tk.Frame, tk.Frame, tk.Label, tk.Label]:
        row = tk.Frame(parent, bg=theme.SURFACE_ALT, cursor="hand2")
        row.pack(fill="x")
        bar = tk.Frame(row, bg=theme.SURFACE_ALT, width=3)
        bar.pack(side="left", fill="y")
        count = tk.Label(
            row, text="", bg=theme.SURFACE_ALT, fg=theme.FAINT,
            font=theme.FONTS["ui_xs"], anchor="e",
        )
        count.pack(side="right", padx=(0, 8))
        label = tk.Label(
            row, text=cat, bg=theme.SURFACE_ALT, fg=theme.INK_SOFT,
            font=theme.FONTS["ui_sm"], anchor="w",
        )
        label.pack(side="left", padx=(9, 0), pady=3)
        for widget in (row, label, count, bar):
            widget.bind("<Button-1>", lambda _e, c=cat: self._set_category(c))
        return row, bar, label, count

    def _set_category(self, cat: str) -> None:
        if cat == self._active_category:
            return
        self._active_category = cat
        for name, (row, bar, label, count) in self._nav_labels.items():
            active = name == cat
            bg = theme.ACCENT_SOFT if active else theme.SURFACE_ALT
            row.configure(bg=bg)
            label.configure(bg=bg, fg=theme.ACCENT if active else theme.INK_SOFT)
            count.configure(bg=bg, fg=theme.ACCENT if active else theme.FAINT)
            bar.configure(bg=theme.ACCENT if active else bg)
            label.configure(font=theme.FONTS["ui_bold"] if active else theme.FONTS["ui_sm"])
        self._refresh_list()

    def _refresh_list(self) -> None:
        if not hasattr(self, "list_grid") or not self.list_grid.winfo_exists():
            return
        for child in self.list_grid.winfo_children():
            child.destroy()
        self._rows = []
        items = filter_sources(
            SOURCES, query=self.search_var.get(), category=self._active_category
        )
        self.list_grid.columnconfigure(0, weight=1, uniform="src")
        self.list_grid.columnconfigure(1, weight=1, uniform="src")
        if not items:
            tk.Label(
                self.list_grid,
                text=f"没有匹配「{self.search_var.get()}」的来源，换个关键词或点左侧「全部」。",
                bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
                anchor="w", justify="left",
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=18)
        for index, source in enumerate(items):
            row = SourceRow(
                self.list_grid, source, self.source_vars[source.id],
                on_toggle=self._refresh_counts, on_hover=self._show_blurb,
            )
            row.grid(row=index // 2, column=index % 2, sticky="ew", pady=0, padx=(0, 4))
            self._rows.append(row)
        self.list_scroll.scroll_to_top()
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        chosen = sum(1 for s in SOURCES if self.source_vars[s.id].get())
        self.count_label.configure(text=f"已选 {chosen} / {len(SOURCES)} 个来源")
        for cat, (_row, _bar, label, count) in self._nav_labels.items():
            group = [s for s in SOURCES if cat == "全部" or s.category == cat]
            on = sum(1 for s in group if self.source_vars[s.id].get())
            count.configure(text=f"{on}/{len(group)}")
            if not group:
                label.configure(fg=theme.FAINT)

    def _bulk(self, on: bool) -> None:
        for source in filter_sources(
            SOURCES, query=self.search_var.get(), category=self._active_category
        ):
            self.source_vars[source.id].set(on)
        self._refresh_counts()
        hint = "已全部勾选" if on else "已全部取消"
        self.hint_label.configure(
            text=f"{hint}（当前筛选：{self._active_category}，"
                 f"共 {len(filter_sources(SOURCES, query=self.search_var.get(), category=self._active_category))} 个来源）",
            fg=theme.MUTED,
        )

    def _apply_preset(self, ids: tuple[str, ...], label: str) -> None:
        wanted = set(ids)
        for source in SOURCES:
            self.source_vars[source.id].set(source.id in wanted)
        self._refresh_counts()
        if hasattr(self, "_rows"):
            for row in self._rows:
                row.refresh()
        self.hint_label.configure(
            text=f"已套用「{label}」：启用 {len(wanted)} 个来源，点「保存设置」生效。",
            fg=theme.TEAL,
        )

    def _reset_sources(self) -> None:
        for source in SOURCES:
            self.source_vars[source.id].set(bool(DEFAULT_SOURCES.get(source.id, False)))
        self._refresh_counts()
        for row in self._rows:
            row.refresh()
        self.hint_label.configure(
            text=f"已恢复默认（{sum(1 for s in SOURCES if DEFAULT_SOURCES.get(s.id))} 个来源）。",
            fg=theme.TEAL,
        )

    def _show_blurb(self, source) -> None:
        self.hint_label.configure(
            text=f"{source.name}（{source.chinese_name}）· {source.category}　{source.blurb}",
            fg=theme.MUTED,
        )

    def _test_sources(self) -> None:
        ids = [sid for sid, var in self.source_vars.items() if var.get()]
        if not ids:
            self.hint_label.configure(text="至少勾选一个来源", fg=theme.ACCENT)
            return
        self.test_btn.state(["disabled"])
        self.hint_label.configure(
            text=f"正在并行拉取 {len(ids)} 个来源的文章列表…", fg=theme.MUTED
        )

        def work():
            return self.ctx.articles.fetch_all_feeds(source_ids=ids)

        def ok(payload):
            if not self.winfo_exists():
                return
            feeds, errors = payload
            total = sum(len(v) for v in feeds.values())
            ok_sources = len(feeds)
            self.hint_label.configure(
                text=f"{ok_sources}/{len(ids)} 个源可用，共取到 {total} 条文章。"
                     + (f"　失败：{'；'.join(errors[:2])}" if errors else ""),
                fg=theme.TEAL if not errors else theme.GOLD,
            )
            self.test_btn.state(["!disabled"])

        def err(exc):
            if not self.winfo_exists():
                return
            self.hint_label.configure(
                text=f"测试失败：{getattr(exc, 'message', exc)}", fg=theme.ACCENT
            )
            self.test_btn.state(["!disabled"])

        self.app.runner.run(work, ok, err, name="test-sources")

    # ------------------------------------------------------------- 词典
    def _build_dict(self) -> None:
        inner = self._scroll(self.tab_dict)
        oxford = self.config.get("oxford", default={}) or {}
        translator = self.config.get("translator", default={}) or {}

        tk.Label(
            inner, text="牛津词典（英英）", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="点词默认查剑桥英汉双解（牛津官方在线英汉需登录授权，无法直接抓取）。\n"
                 "这里的牛津配置只用于词条右上角切到「英英」时的释义——不切过去不会发请求。\n"
                 "优先使用牛津官方 API（免费申请 app_id / app_key）；未配置时解析牛津学习者词典网页。",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left", wraplength=650,
        ).pack(anchor="w", pady=(3, 10))

        form = tk.Frame(inner, bg=theme.SURFACE)
        form.pack(fill="x")
        self.app_id_var = tk.StringVar(value=oxford.get("app_id", ""))
        self.app_key_var = tk.StringVar(value=oxford.get("app_key", ""))
        self._field(form, 0, "App ID", self.app_id_var, width=32)
        self._field(form, 1, "App Key", self.app_key_var, width=32, show="•")
        link = tk.Label(
            form, text="申请地址：developer.oxforddictionaries.com ↗", bg=theme.SURFACE,
            fg=theme.ACCENT, font=theme.FONTS["ui_xs"], cursor="hand2",
        )
        link.grid(row=2, column=1, sticky="w", pady=(2, 8))
        link.bind(
            "<Button-1>",
            lambda _e: self.app.open_url("https://developer.oxforddictionaries.com/"),
        )

        self.allow_web = tk.BooleanVar(value=bool(oxford.get("allow_web", True)))
        self.allow_fallback = tk.BooleanVar(value=bool(oxford.get("allow_fallback", True)))
        CheckRow(
            inner, "允许抓取牛津学习者词典网页（无需 API Key）", self.allow_web,
            wraplength=620,
        ).pack(fill="x", pady=(2, 0))
        CheckRow(
            inner, "牛津不可用时允许使用备用词典与内置离线词表", self.allow_fallback,
            desc="界面会标注每个释义的真实来源，不会把备用释义冒充成牛津。",
            wraplength=620,
        ).pack(fill="x")

        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", pady=18)
        tk.Label(
            inner, text="翻译站点", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="按勾选顺序尝试，失败或超时会自动换下一个。译文逐句缓存，重复查看不再请求。",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left", wraplength=650,
        ).pack(anchor="w", pady=(3, 8))

        providers = translator.get("providers", ["bing", "mymemory"]) or []
        self.bing_var = tk.BooleanVar(value="bing" in providers)
        self.mymemory_var = tk.BooleanVar(value="mymemory" in providers)
        self.google_var = tk.BooleanVar(value="google" in providers)

        CheckRow(
            inner, "必应翻译（微软 Translator，免 Key）", self.bing_var,
            desc="网页接口自带「标准 / 非正式 / 正式」三档语气，不用申请任何 API Key，"
                 "国内可直连。",
            wraplength=620,
        ).pack(fill="x")

        tone_row = tk.Frame(inner, bg=theme.SURFACE)
        tone_row.pack(fill="x", padx=(30, 0), pady=(6, 0))
        tk.Label(
            tone_row, text="默认语气", bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_xs"],
        ).pack(side="left", padx=(0, 8))
        self.tone_var = tk.StringVar(value=translator.get("tone", "default") or "default")
        for value, label in (("default", "自动"), ("formal", "正式"), ("informal", "非正式")):
            tk.Radiobutton(
                tone_row, text=label, value=value, variable=self.tone_var,
                bg=theme.SURFACE, fg=theme.INK_SOFT, activebackground=theme.SURFACE,
                selectcolor=theme.ACCENT_SOFT, font=theme.FONTS["ui_xs"],
                bd=0, highlightthickness=0, cursor="hand2",
            ).pack(side="left", padx=(0, 10))

        CheckRow(
            inner, "Google 翻译", self.google_var,
            desc="多数国内网络不可达（实测连接超时），默认关闭；勾上只作为最后兜底。",
            wraplength=620,
        ).pack(fill="x", pady=(10, 0))
        CheckRow(
            inner, "MyMemory 翻译记忆库", self.mymemory_var,
            desc="国内可直连的免费公共接口，作为最后兜底。", wraplength=620,
        ).pack(fill="x")

        self.email_var = tk.StringVar(value=translator.get("mymemory_email", ""))
        email_row = tk.Frame(inner, bg=theme.SURFACE)
        email_row.pack(fill="x", pady=(6, 0))
        tk.Label(
            email_row, text="MyMemory 邮箱（可选，填了额度更高）", bg=theme.SURFACE,
            fg=theme.MUTED, font=theme.FONTS["ui_xs"],
        ).pack(side="left")
        ttk.Entry(email_row, textvariable=self.email_var, width=30).pack(
            side="left", padx=(8, 0)
        )

        self.libre_var = tk.StringVar(value=translator.get("libre_url", ""))
        libre_row = tk.Frame(inner, bg=theme.SURFACE)
        libre_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            libre_row, text="自建 LibreTranslate 地址（可选）", bg=theme.SURFACE,
            fg=theme.MUTED, font=theme.FONTS["ui_xs"],
        ).pack(side="left")
        ttk.Entry(libre_row, textvariable=self.libre_var, width=36).pack(
            side="left", padx=(8, 0)
        )
        tk.Label(
            inner,
            text="提示：机器译文只作参考，逐句对照原文才是练习的重点。",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"], anchor="w",
        ).pack(anchor="w", pady=(12, 0))

    def _field(self, parent, row: int, label: str, var: tk.StringVar, *, width: int = 30, show: str = ""):
        tk.Label(
            parent, text=label, bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_sm"],
        ).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Entry(parent, textvariable=var, width=width, show=show).grid(
            row=row, column=1, sticky="w", pady=3
        )

    # ------------------------------------------------------------- 缓存
    def _build_cache(self) -> None:
        inner = self._scroll(self.tab_cache)
        tk.Label(
            inner, text="本地缓存", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="查过的单词、短语、句子都会按规范化文本缓存；同一天重复查看不再请求网络。\n"
                 "缓存位于数据目录下的 pressread.db（SQLite）。",
            bg=theme.SURFACE, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left", wraplength=650,
        ).pack(anchor="w", pady=(3, 16))

        self.cache_box = tk.Frame(inner, bg=theme.SURFACE_ALT, padx=18, pady=14)
        self.cache_box.pack(fill="x")
        self.cache_label = tk.Label(
            self.cache_box, text="", bg=theme.SURFACE_ALT, fg=theme.INK,
            font=theme.FONTS["ui_sm"], anchor="w", justify="left",
        )
        self.cache_label.pack(anchor="w")

        row = tk.Frame(inner, bg=theme.SURFACE)
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="刷新统计", style="TButton", command=self._refresh_cache).pack(side="left")
        ttk.Button(
            row, text="清理过期", style="TButton", command=self._purge_expired
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            row, text="清空全部缓存", style="TButton", command=self._clear_cache
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            row, text="打开数据目录", style="TButton",
            command=lambda: self.app.open_folder(self.ctx.paths["root"]),
        ).pack(side="left", padx=(8, 0))
        self._refresh_cache()

    def _refresh_cache(self) -> None:
        stats = self.ctx.store.cache_stats()
        prefetch = self.ctx.store.prefetch_stats()
        self.cache_label.configure(
            text=f"单词 {stats.get('words', 0)} 条　·　句子/短语 {stats.get('texts', 0)} 条　·　"
                 f"累计复用 {stats.get('hits', 0)} 次\n"
                 f"预取正文 {prefetch.get('count', 0)} 篇（并发抓取后最多保留 1 小时，"
                 f"下次打开自动清空）\n"
                 f"数据库：{self.ctx.store.path}"
        )

    def _purge_expired(self) -> None:
        days = int(self.config.get("cache", "text_ttl_days", default=14) or 14)
        removed = self.ctx.store.cache_purge(days * 86400)
        self._refresh_cache()
        self.app.toast(f"已清理 {removed} 条过期缓存")

    def _clear_cache(self) -> None:
        if not messagebox.askyesno("清空缓存", "会删除全部词典与翻译缓存，下次查询需要重新联网。继续？", parent=self):
            return
        removed = self.ctx.store.cache_purge()
        self.ctx.store.prefetch_purge()
        self._refresh_cache()
        self.app.toast(f"已清空 {removed} 条缓存")

    # ------------------------------------------------------------- 外观
    def _build_ui(self) -> None:
        inner = self._scroll(self.tab_ui)
        tk.Label(
            inner, text="每日目标", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        row = tk.Frame(inner, bg=theme.SURFACE)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(
            row, text="每天完成段落数", bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"],
        ).pack(side="left")
        self.goal_var = tk.IntVar(value=self.config.daily_goal)
        ttk.Spinbox(row, from_=1, to=10, width=4, textvariable=self.goal_var).pack(
            side="left", padx=(8, 0)
        )
        tk.Label(
            row, text="段（默认 5 段，导出文件按完成段数生成）", bg=theme.SURFACE,
            fg=theme.FAINT, font=theme.FONTS["ui_xs"],
        ).pack(side="left", padx=(6, 0))

        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", pady=18)
        tk.Label(
            inner, text="一次显示段数", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        span_row = tk.Frame(inner, bg=theme.SURFACE)
        span_row.pack(fill="x", pady=(6, 0))
        tk.Label(
            span_row, text="每屏抽取连续段落", bg=theme.SURFACE, fg=theme.MUTED,
            font=theme.FONTS["ui_sm"],
        ).pack(side="left")
        self.span_var = tk.IntVar(value=self.config.paragraphs_per_unit)
        ttk.Spinbox(span_row, from_=1, to=10, width=4, textvariable=self.span_var).pack(
            side="left", padx=(8, 0)
        )
        tk.Label(
            span_row, text="段（默认 1 段；文章不够这么多段时取最多）", bg=theme.SURFACE,
            fg=theme.FAINT, font=theme.FONTS["ui_xs"],
        ).pack(side="left", padx=(6, 0))
        tk.Label(
            inner,
            text="设为 2 段以上时，正文、逐句译文与「标记完成」都按这一屏的连续段数整体处理，\n"
                 "完成一次按实际段数计入每日进度。",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left",
        ).pack(anchor="w", pady=(6, 0))

        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", pady=18)
        tk.Label(
            inner, text="阅读字号", bg=theme.SURFACE, fg=theme.INK,
            font=theme.FONTS["ui_bold"], anchor="w",
        ).pack(anchor="w")
        size_row = tk.Frame(inner, bg=theme.SURFACE)
        size_row.pack(fill="x", pady=(6, 0))
        self.size_var = tk.IntVar(value=self.config.article_font_size)
        # 先建 label 再建滑块：滑块初始化即回调，此时 size_label 必须已存在
        self.size_label = tk.Label(
            size_row, text=f"{self.config.article_font_size} pt", bg=theme.SURFACE,
            fg=theme.MUTED, font=theme.FONTS["ui_sm"],
        )
        self.size_slider = MiniSlider(
            size_row, from_=12, to=24, value=self.config.article_font_size,
            width=260, command=self._on_scale,
        )
        self.size_slider.pack(side="left")
        self.size_label.pack(side="left", padx=(10, 0))

        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", pady=18)
        self.demo_var = tk.BooleanVar(
            value=bool(self.config.get("ui", "show_demo_on_failure", default=True))
        )
        CheckRow(
            inner, "网络抓不到文章时，自动打开内置离线示例", self.demo_var,
            desc="保证断网时程序仍然可用，离线示例的译文由程序内置。", wraplength=620,
        ).pack(fill="x")

        tk.Label(
            inner,
            text=f"每天的目标只在本地统计。完成 {self.config.daily_goal} 段后可在完成弹窗或首页导出成果，\n"
                 f"文件名形如 外刊精读_每日成果_20261028_v{__version__}.md。",
            bg=theme.SURFACE, fg=theme.FAINT, font=theme.FONTS["ui_xs"],
            anchor="w", justify="left",
        ).pack(anchor="w", pady=(18, 0))

    def _on_scale(self, size: int) -> None:
        size = max(12, min(24, int(size)))
        self.size_var.set(size)
        self.size_label.configure(text=f"{size} pt")
        theme.article_fonts(size)

    # ------------------------------------------------------------- 保存
    def _save(self) -> None:
        ids = [sid for sid, var in self.source_vars.items() if var.get()]
        if not ids:
            messagebox.showwarning("还没有来源", "至少勾选一个外刊来源。", parent=self)
            return
        app_id = self.app_id_var.get().strip()
        app_key = self.app_key_var.get().strip()
        if bool(app_id) != bool(app_key):
            messagebox.showwarning(
                "牛津 API 配置不完整", "app_id 与 app_key 需要同时填写，或都留空（改用网页解析）。", parent=self
            )
            return
        if not (
            self.bing_var.get()
            or self.mymemory_var.get()
            or self.google_var.get()
            or self.libre_var.get().strip()
        ):
            messagebox.showwarning(
                "没有翻译源", "至少启用一个翻译站点，否则逐句译文无法生成。", parent=self
            )
            return

        data = self.config.data
        data["sources"] = {source.id: bool(self.source_vars.get(source.id, tk.BooleanVar()).get()) for source in SOURCES}
        data.setdefault("oxford", {})
        data["oxford"]["app_id"] = app_id
        data["oxford"]["app_key"] = app_key
        data["oxford"]["allow_web"] = bool(self.allow_web.get())
        data["oxford"]["allow_fallback"] = bool(self.allow_fallback.get())
        data.setdefault("translator", {})
        providers = []
        if self.bing_var.get():
            providers.append("bing")
        if self.mymemory_var.get():
            providers.append("mymemory")
        if self.google_var.get():
            providers.append("google")
        data["translator"]["providers"] = providers or ["bing", "mymemory"]
        data["translator"]["tone"] = self.tone_var.get()
        data["translator"]["mymemory_email"] = self.email_var.get().strip()
        data["translator"]["libre_url"] = self.libre_var.get().strip()
        data["daily_goal"] = max(1, min(10, int(self.goal_var.get() or 5)))
        data.setdefault("practice", {})
        data["practice"]["paragraphs_per_unit"] = max(
            1, min(10, int(self.span_var.get() or 1))
        )
        data.setdefault("ui", {})
        data["ui"]["article_font_size"] = max(12, min(24, int(self.size_var.get() or 16)))
        data["ui"]["show_demo_on_failure"] = bool(self.demo_var.get())
        self.config.save()

        theme.article_fonts(data["ui"]["article_font_size"])
        self.app.reader.refresh_progress()
        self.app.start.refresh()
        self.app.toast("设置已保存")
        self.destroy()
