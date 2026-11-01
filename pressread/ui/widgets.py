"""通用小部件：滚动容器、进度点、加载动画、提示条、悬浮提示。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import theme


class ScrollFrame(tk.Frame):
    """Canvas + inner Frame 的纵向滚动区，宽度随容器自适应。

    auto_hide=True 时内容装得下就收起滚动条，装不下才显示，
    避免设置页这种长表单「内容被裁掉却看不到滚动条」。
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        bg: str = theme.BG,
        padding: int = 0,
        auto_hide: bool = False,
    ):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self._bg = bg
        self.auto_hide = auto_hide
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0, takefocus=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg, bd=0, highlightthickness=0, padx=padding, pady=padding)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.inner.bind("<Enter>", self._bind_wheel)

    def _on_inner(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.auto_hide:
            self._sync_scrollbar()

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        if self.auto_hide:
            self.after_idle(self._sync_scrollbar)

    def _sync_scrollbar(self) -> None:
        if not self.canvas.winfo_exists() or not self.scrollbar.winfo_exists():
            return
        needs = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 2
        if needs and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side="right", fill="y")
        elif not needs and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()

    def _bind_wheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def clear(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()


class ProgressDots(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        total: int = 5,
        done: int = 0,
        size: int = 12,
        gap: int = 9,
        bg: str = theme.SURFACE,
        color: str = theme.ACCENT,
        idle: str = theme.LINE_STRONG,
    ):
        width = max(1, total) * (size + gap) - gap
        super().__init__(
            parent, width=width, height=size + 4, bg=bg, bd=0, highlightthickness=0, takefocus=0
        )
        self.total = total
        self.done = done
        self.size = size
        self.gap = gap
        self.color = color
        self.idle = idle
        self.draw()

    def update_progress(self, done: int) -> None:
        self.done = max(0, min(self.total, done))
        self.draw()

    def set_total(self, total: int) -> None:
        total = max(1, int(total))
        if total == self.total:
            return
        self.total = total
        self.configure(width=total * (self.size + self.gap) - self.gap)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        r = self.size / 2
        cy = (self.size + 4) / 2
        for i in range(self.total):
            cx = i * (self.size + self.gap) + r
            if i < self.done:
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=self.color, outline=self.color)
                self.create_line(
                    cx - r * 0.42, cy + 0.02 * r, cx - r * 0.08, cy + r * 0.4,
                    cx + r * 0.45, cy - r * 0.38, fill="#FFFFFF", width=1.6,
                    capstyle="round", joinstyle="round",
                )
            else:
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="", outline=self.idle, width=1.2)


class LoadingDots(tk.Label):
    def __init__(self, parent: tk.Misc, text: str = "加载中", **kwargs):
        kwargs.setdefault("bg", theme.SURFACE)
        kwargs.setdefault("fg", theme.MUTED)
        kwargs.setdefault("font", theme.FONTS["ui_sm"])
        super().__init__(parent, text=text, **kwargs)
        self._base = text
        self._dots = 0
        self._job: str | None = None

    def start(self) -> None:
        self.stop()
        self._tick()

    def stop(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.configure(text=self._base + "·" * self._dots)
        try:
            self._job = self.after(360, self._tick)
        except tk.TclError:
            self._job = None

    def destroy(self) -> None:
        self.stop()
        super().destroy()


class Toast(tk.Frame):
    def __init__(self, parent: tk.Misc):
        super().__init__(
            parent,
            bg=theme.INK,
            bd=0,
            highlightthickness=0,
        )
        self._label = tk.Label(
            self,
            text="",
            bg=theme.INK,
            fg="#FFFFFF",
            font=theme.FONTS["ui"],
            padx=16,
            pady=8,
        )
        self._label.pack()
        self._job: str | None = None
        self._visible = False

    def show(self, message: str, ms: int = 2400) -> None:
        self._label.configure(text=message)
        self.place(relx=0.5, rely=0.965, anchor="s")
        self.lift()
        self._visible = True
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = self.after(ms, self.hide)

    def hide(self) -> None:
        self._visible = False
        self.place_forget()
        self._job = None


class Tooltip:
    """鼠标停留 500ms 后显示的小黑提示。"""

    def __init__(self, widget: tk.Misc, text: str, *, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _show(self) -> None:
        if self._tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.configure(bg=theme.INK)
        tk.Label(
            tip, text=self.text, bg=theme.INK, fg="#FFFFFF",
            font=theme.FONTS["ui_xs"], padx=9, pady=5, justify="left",
            wraplength=280,
        ).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class LinkLabel(tk.Label):
    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None], **kwargs):
        kwargs.setdefault("bg", theme.SURFACE)
        kwargs.setdefault("fg", theme.ACCENT)
        kwargs.setdefault("font", theme.FONTS["ui_sm"])
        kwargs.setdefault("cursor", "hand2")
        super().__init__(parent, text=text, **kwargs)
        self._command = command
        self.bind("<Button-1>", lambda _e: self._command())
        self.bind("<Enter>", lambda _e: self.configure(font=theme.FONTS["ui_bold"]))
        self.bind("<Leave>", lambda _e: self.configure(font=theme.FONTS["ui_sm"]))


class MiniSlider(tk.Frame):
    """自绘滑块：clam 的 TScale 会给轨道描一圈粗边，这里按主题重画成细轨 + 圆钮。"""

    HEIGHT = 26
    RADIUS = 8

    def __init__(
        self,
        parent: tk.Misc,
        *,
        from_: int = 12,
        to: int = 24,
        value: int = 16,
        step: int = 1,
        width: int = 240,
        bg: str = theme.SURFACE,
        command=None,
    ):
        super().__init__(parent, bg=bg)
        self.low = float(from_)
        self.high = float(to)
        self.step = max(1, int(step))
        self.value = float(value)
        self.command = command
        self._hover = False
        self.canvas = tk.Canvas(
            self, width=width, height=self.HEIGHT, bg=bg, bd=0,
            highlightthickness=0, cursor="hand2", takefocus=1,
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Enter>", lambda _e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(False))
        self.canvas.bind("<Left>", lambda _e: self._nudge(-self.step))
        self.canvas.bind("<Right>", lambda _e: self._nudge(self.step))
        self.canvas.bind("<Home>", lambda _e: self._apply(self.low))
        self.canvas.bind("<End>", lambda _e: self._apply(self.high))
        self._draw()

    # ------------------------------------------------------------------ 取值
    def get(self) -> int:
        return int(round(self.value))

    def set(self, value: float, *, notify: bool = False) -> None:
        self.value = self._snap(value)
        self._draw()
        if notify and self.command:
            self.command(self.get())

    # ------------------------------------------------------------------ 绘制
    def _span(self) -> tuple[float, float]:
        return self.RADIUS + 2, self.canvas.winfo_reqwidth() - self.RADIUS - 2

    def _ratio(self) -> float:
        span = self.high - self.low
        return 0.0 if span <= 0 else (self.value - self.low) / span

    def _draw(self) -> None:
        if not self.canvas.winfo_exists():
            return
        x1, x2 = self._span()
        cy = self.HEIGHT / 2
        tx = x1 + (x2 - x1) * self._ratio()
        self.canvas.delete("all")
        self.canvas.create_line(
            x1, cy, x2, cy, fill=theme.LINE, width=6, capstyle="round"
        )
        if tx > x1 + 0.5:
            self.canvas.create_line(
                x1, cy, tx, cy, fill=theme.ACCENT, width=6, capstyle="round"
            )
        ring = theme.ACCENT_DARK if self._hover else theme.ACCENT
        self.canvas.create_oval(
            tx - self.RADIUS, cy - self.RADIUS, tx + self.RADIUS, cy + self.RADIUS,
            fill=theme.SURFACE, outline=ring, width=2.6,
        )

    def _set_hover(self, hover: bool) -> None:
        if self._hover != hover:
            self._hover = hover
            self._draw()

    # ------------------------------------------------------------------ 交互
    def _snap(self, value: float) -> float:
        steps = round((value - self.low) / self.step)
        snapped = self.low + steps * self.step
        return max(self.low, min(self.high, snapped))

    def _value_at(self, x: int) -> float:
        x1, x2 = self._span()
        if x2 <= x1:
            return self.low
        ratio = max(0.0, min(1.0, (x - x1) / (x2 - x1)))
        return self.low + ratio * (self.high - self.low)

    def _apply(self, value: float) -> None:
        snapped = self._snap(value)
        if snapped == self.value:
            return
        self.value = snapped
        self._draw()
        if self.command:
            self.command(self.get())

    def _on_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        self._apply(self._value_at(event.x))

    def _on_drag(self, event: tk.Event) -> None:
        self._apply(self._value_at(event.x))

    def _on_release(self, _event: tk.Event) -> None:
        self._draw()

    def _nudge(self, delta: int) -> str:
        self._apply(self.value + delta)
        return "break"


def section_label(parent: tk.Misc, text: str, *, bg: str = theme.SURFACE) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=theme.MUTED,
        font=theme.FONTS["ui_xs"],
        anchor="w",
    )


class CheckRow(tk.Frame):
    """自绘复选框：clam 主题的勾选符号是个叉，看起来像关闭按钮，这里统一重画。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        *,
        desc: str = "",
        bg: str = theme.SURFACE,
        wraplength: int = 560,
        command=None,
    ):
        super().__init__(parent, bg=bg)
        self.variable = variable
        self._bg = bg
        self._command = command
        self.canvas = tk.Canvas(
            self, width=18, height=18, bg=bg, bd=0, highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack(side="left", anchor="n", pady=(1, 0))
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.label = tk.Label(
            self.body, text=text, bg=bg, fg=theme.INK, font=theme.FONTS["ui"],
            anchor="w", justify="left", wraplength=wraplength,
        )
        self.label.pack(anchor="w")
        self.desc_label = None
        if desc:
            self.desc_label = tk.Label(
                self.body, text=desc, bg=bg, fg=theme.MUTED, font=theme.FONTS["ui_xs"],
                anchor="w", justify="left", wraplength=wraplength,
            )
            self.desc_label.pack(anchor="w", pady=(1, 0))
        targets = [self, self.canvas, self.body, self.label]
        if self.desc_label:
            targets.append(self.desc_label)
        for widget in targets:
            widget.bind("<Button-1>", self._toggle)
        self.canvas.bind("<Enter>", lambda _e: self._draw(hover=True))
        self.canvas.bind("<Leave>", lambda _e: self._draw(hover=False))
        try:
            variable.trace_add("write", lambda *_a: self._draw())
        except tk.TclError:
            pass
        self._draw()

    def _toggle(self, _event=None) -> None:
        self.variable.set(not bool(self.variable.get()))
        if self._command:
            self._command()

    def _draw(self, hover: bool = False) -> None:
        if not self.canvas.winfo_exists():
            return
        checked = bool(self.variable.get())
        self.canvas.delete("all")
        border = theme.ACCENT if (checked or hover) else theme.LINE_STRONG
        self.canvas.create_rectangle(
            1, 1, 17, 17,
            outline=border,
            fill=theme.ACCENT if checked else theme.SURFACE,
            width=1.4,
        )
        if checked:
            self.canvas.create_line(
                4.6, 9.2, 7.6, 12.2, 13.6, 5.0,
                fill="#FFFFFF", width=1.9, capstyle="round", joinstyle="round",
            )
