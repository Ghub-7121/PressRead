"""视觉主题：暖纸色背景 + 砖红强调色 + 深青辅助色。

Tk 原生控件偏灰，这里统一用 clam 主题重绘按钮、输入框、滚动条，
并集中管理字体，方便设置里改字号时一次生效。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

BG = "#F5F2EA"
BG_DEEP = "#EDE8DC"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FBF9F3"
INK = "#1F2328"
INK_SOFT = "#4B4F55"
MUTED = "#7C7668"
FAINT = "#A39C8C"
LINE = "#E4DED0"
LINE_STRONG = "#D3CAB6"
ACCENT = "#B04A2F"
ACCENT_DARK = "#913A24"
ACCENT_SOFT = "#F8E9E2"
TEAL = "#2E6F63"
TEAL_DARK = "#235A50"
TEAL_SOFT = "#E5F0EC"
GOLD = "#A87A22"
GOLD_SOFT = "#F8EFD9"
GREEN = "#437A50"
WHITE = "#FFFFFF"
SENT_HL = "#FBF1D3"
WORD_HL = "#EFE3CC"
SELECT_BG = "#F6D9C9"

UI_FAMILIES = ("Microsoft YaHei UI", "微软雅黑", "Segoe UI", "SimHei")
SERIF_FAMILIES = ("Georgia", "Cambria", "Times New Roman", "SimSun")

FONTS: dict[str, tkfont.Font] = {}
STYLE: ttk.Style | None = None
_SCALE = 1.0


def pick_family(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    available = set(tkfont.families(root))
    for family in candidates:
        if family in available:
            return family
    return "TkDefaultFont"


def setup(root: tk.Misc) -> None:
    global STYLE
    ui_family = pick_family(root, UI_FAMILIES)
    serif_family = pick_family(root, SERIF_FAMILIES)

    specs = {
        "ui": (10, "normal", ui_family),
        "ui_sm": (9, "normal", ui_family),
        "ui_xs": (8, "normal", ui_family),
        "ui_bold": (10, "bold", ui_family),
        "ui_lg": (12, "bold", ui_family),
        "ui_xl": (15, "bold", ui_family),
        "hero": (30, "bold", serif_family),
        "hero_sm": (20, "bold", serif_family),
        "article": (16, "normal", serif_family),
        "article_bold": (16, "bold", serif_family),
        "article_it": (16, "normal", serif_family, "italic"),
        "example": (11, "normal", serif_family, "italic"),
        "def": (11, "normal", ui_family),
        "zh": (12, "normal", ui_family),
        "zh_sm": (10, "normal", ui_family),
        "zh_lg": (14, "normal", ui_family),
        "num": (26, "bold", serif_family),
        "mono": (9, "normal", pick_family(root, ("Consolas", "Courier New"))),
    }
    for name, spec in specs.items():
        size, weight, family = spec[:3]
        slant = spec[3] if len(spec) > 3 else "roman"
        FONTS[name] = tkfont.Font(
            root=root, family=family, size=size, weight=weight, slant=slant
        )

    STYLE = ttk.Style(root)
    try:
        STYLE.theme_use("clam")
    except tk.TclError:
        pass
    _configure_styles()


def _configure_styles() -> None:
    s = STYLE
    assert s is not None
    s.configure("TFrame", background=BG)
    s.configure("Surface.TFrame", background=SURFACE)
    s.configure("Soft.TFrame", background=SURFACE_ALT)
    s.configure("Header.TFrame", background=SURFACE)

    s.configure("TLabel", background=BG, foreground=INK, font=FONTS["ui"])
    s.configure("Surface.TLabel", background=SURFACE, foreground=INK, font=FONTS["ui"])
    s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONTS["ui_sm"])
    s.configure("MutedSurface.TLabel", background=SURFACE, foreground=MUTED, font=FONTS["ui_sm"])
    s.configure("Faint.TLabel", background=BG, foreground=FAINT, font=FONTS["ui_xs"])
    s.configure("Title.TLabel", background=SURFACE, foreground=INK, font=FONTS["ui_xl"])
    s.configure("Hero.TLabel", background=SURFACE, foreground=INK, font=FONTS["hero"])
    s.configure("Accent.TLabel", background=SURFACE, foreground=ACCENT, font=FONTS["ui_bold"])

    s.configure(
        "TButton",
        background=SURFACE,
        foreground=INK,
        bordercolor=LINE_STRONG,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        borderwidth=1,
        focusthickness=0,
        relief="flat",
        padding=(14, 8),
        font=FONTS["ui"],
    )
    s.map(
        "TButton",
        background=[("disabled", SURFACE_ALT), ("pressed", BG_DEEP), ("active", SURFACE_ALT)],
        foreground=[("disabled", FAINT)],
        bordercolor=[("active", ACCENT), ("pressed", ACCENT)],
    )
    s.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=WHITE,
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        borderwidth=0,
        padding=(18, 10),
        font=FONTS["ui_bold"],
    )
    s.map(
        "Accent.TButton",
        background=[("disabled", "#E7E0D2"), ("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
        foreground=[("disabled", "#A79F8E")],
    )
    s.configure(
        "Teal.TButton",
        background=TEAL,
        foreground=WHITE,
        bordercolor=TEAL,
        lightcolor=TEAL,
        darkcolor=TEAL,
        borderwidth=0,
        padding=(14, 9),
        font=FONTS["ui_bold"],
    )
    s.map(
        "Teal.TButton",
        background=[("disabled", "#9BBCB4"), ("pressed", TEAL_DARK), ("active", TEAL_DARK)],
    )
    s.configure(
        "Ghost.TButton",
        background=BG,
        foreground=INK_SOFT,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
        borderwidth=0,
        padding=(10, 6),
    )
    s.map(
        "Ghost.TButton",
        background=[("active", BG_DEEP)],
        foreground=[("active", INK)],
    )
    s.configure(
        "SurfaceGhost.TButton",
        background=SURFACE,
        foreground=INK_SOFT,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        borderwidth=0,
        padding=(10, 6),
    )
    s.map(
        "SurfaceGhost.TButton",
        background=[("active", SURFACE_ALT)],
        foreground=[("active", ACCENT)],
    )
    s.configure(
        "Small.TButton",
        background=SURFACE,
        foreground=INK_SOFT,
        bordercolor=LINE,
        borderwidth=1,
        padding=(10, 5),
        font=FONTS["ui_sm"],
    )
    s.map("Small.TButton", background=[("active", SURFACE_ALT)], bordercolor=[("active", ACCENT)])

    s.configure(
        "TEntry",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=INK,
        bordercolor=LINE_STRONG,
        lightcolor=LINE_STRONG,
        darkcolor=LINE_STRONG,
        insertcolor=INK,
        padding=(8, 6),
    )
    s.configure(
        "TRadiobutton",
        background=SURFACE,
        foreground=INK,
        focuscolor=SURFACE,
        font=FONTS["ui"],
    )
    s.map("TRadiobutton", background=[("active", SURFACE)])
    s.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=INK,
        bordercolor=LINE_STRONG,
        arrowcolor=INK_SOFT,
        padding=(8, 5),
    )
    s.configure(
        "TNotebook",
        background=BG,
        bordercolor=LINE,
        tabmargins=(2, 6, 2, 0),
    )
    s.configure(
        "TNotebook.Tab",
        background=BG_DEEP,
        foreground=INK_SOFT,
        padding=(16, 8),
        font=FONTS["ui_bold"],
        bordercolor=LINE,
    )
    s.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE)],
        foreground=[("selected", ACCENT)],
    )
    s.configure(
        "Vertical.TScrollbar",
        background=BG_DEEP,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=MUTED,
        gripcount=0,
        arrowsize=10,
        width=10,
    )
    s.map("Vertical.TScrollbar", background=[("active", LINE_STRONG)])
    s.configure(
        "TSpinbox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=INK,
        bordercolor=LINE_STRONG,
        lightcolor=LINE_STRONG,
        darkcolor=LINE_STRONG,
        arrowcolor=INK_SOFT,
        padding=(7, 5),
    )
    # 字号滑块用自绘的 MiniSlider（clam 的 TScale 轨道描边太重），这里只留基础配色
    s.configure("TScale", background=SURFACE, troughcolor=BG_DEEP, bordercolor=LINE)
    s.configure("TSeparator", background=LINE)
    s.configure("TProgressbar", background=ACCENT, troughcolor=BG_DEEP, bordercolor=BG_DEEP)


def article_fonts(size: int) -> None:
    """设置里改字号后立即重排正文。"""
    size = max(12, min(28, int(size)))
    FONTS["article"].configure(size=size)
    FONTS["article_bold"].configure(size=size)
    FONTS["article_it"].configure(size=size)
    FONTS["zh"].configure(size=max(11, size - 4))
    FONTS["zh_sm"].configure(size=max(9, size - 6))


def card(parent: tk.Misc, **kwargs) -> tk.Frame:
    """白底 + 1px 细边框的卡片。Tk 没有阴影，用边框和留白撑起层次。"""
    return tk.Frame(
        parent,
        bg=SURFACE,
        highlightbackground=LINE,
        highlightcolor=LINE,
        highlightthickness=1,
        bd=0,
        **kwargs,
    )


def divider(parent: tk.Misc, *, vertical: bool = False, color: str = LINE) -> tk.Frame:
    if vertical:
        return tk.Frame(parent, bg=color, width=1)
    return tk.Frame(parent, bg=color, height=1)


def pill(
    parent: tk.Misc,
    text: str,
    *,
    bg: str = ACCENT_SOFT,
    fg: str = ACCENT,
    font: str = "ui_sm",
) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=fg,
        font=FONTS[font],
        padx=8,
        pady=2,
        bd=0,
    )
