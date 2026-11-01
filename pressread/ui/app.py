"""应用主窗口与页面路由。"""

from __future__ import annotations

import os
import platform
import subprocess
import tkinter as tk
import webbrowser
from tkinter import messagebox
from typing import Callable

from .. import __app_name__, __version__
from ..context import AppContext
from ..paths import resource_path
from . import theme
from .history import HistoryPage
from .reader import ReaderPage
from .settings import SettingsDialog
from .start import StartPage
from .widgets import Toast
from .workers import TaskRunner


class PressReadApp(tk.Tk):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.title(f"{__app_name__}  v{__version__}")
        self.configure(bg=theme.BG)
        theme.setup(self)
        theme.article_fonts(ctx.config.article_font_size)
        self._set_icon()

        geometry = ctx.config.get("ui", "window", default="1280x830") or "1280x830"
        self.geometry(geometry)
        self.minsize(1120, 720)
        try:
            self.state("zoomed") if os.environ.get("PRESSREAD_MAXIMIZED") else None
        except tk.TclError:
            pass

        self.runner = TaskRunner(self, max_workers=5)
        self.preferred_source: str | None = None
        self._settings_dialog: SettingsDialog | None = None

        self.container = tk.Frame(self, bg=theme.BG)
        self.container.pack(fill="both", expand=True)

        self.reader = ReaderPage(self.container, self)
        self.start = StartPage(self.container, self)
        self.history = HistoryPage(self.container, self)
        self.pages = [self.start, self.reader, self.history]
        self.sidebar = self.reader.sidebar

        self.toast_widget = Toast(self)
        self.show_start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 路由
    def _hide_all(self) -> None:
        for page in self.pages:
            page.pack_forget()

    def show_start(self) -> None:
        self.reader.on_hide()
        self._hide_all()
        self.start.refresh()
        self.start.pack(fill="both", expand=True)
        self.reader.refresh_progress()

    def start_practice(self) -> None:
        self.show_reader(load=self.reader.load_new_article)

    def show_reader(self, load: Callable[[], None] | None = None) -> None:
        self._hide_all()
        self.reader.pack(fill="both", expand=True)
        self.reader.refresh_progress()
        if load:
            load()

    def go_history(self) -> None:
        self.reader.on_hide()
        self._hide_all()
        self.history.refresh()
        self.history.pack(fill="both", expand=True)

    def go_start(self) -> None:
        self.show_start()

    def show_demo(self) -> None:
        self.show_reader(load=lambda: self.reader.display_unit(self.ctx.articles.load_demo_unit()))

    # ------------------------------------------------------------------ 设置
    def open_settings(self) -> None:
        if self._settings_dialog and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            return
        self._settings_dialog = SettingsDialog(self, self)

    # ------------------------------------------------------------------ 工具
    def _set_icon(self) -> None:
        """任务栏与标题栏图标；源码运行时读 assets/，打包后读解包目录。"""
        ico = resource_path("assets", "app.ico")
        if not ico.exists():
            return
        try:
            self.iconbitmap(default=str(ico))
        except tk.TclError:
            pass

    def toast(self, message: str, ms: int = 2400) -> None:
        self.toast_widget.show(message, ms)

    def open_url(self, url: str) -> None:
        if not url:
            return
        try:
            webbrowser.open(url, new=2)
        except Exception:
            self.toast("无法打开浏览器")

    def open_folder(self, path) -> None:
        try:
            if platform.system() == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            self.toast(f"打开目录失败：{path}")

    def refresh_daily_badge(self) -> None:
        self.reader.refresh_progress()
        if self.start.winfo_ismapped():
            self.start.refresh()

    # ------------------------------------------------------------------ 关闭
    def _on_close(self) -> None:
        try:
            self.reader.on_hide()
        except Exception:
            pass
        try:
            self.ctx.config.save()
        except Exception:
            pass
        self.runner.shutdown()
        self.ctx.close()
        self.destroy()


def run(ctx: AppContext | None = None, *, demo: bool = False, auto_start: bool = False) -> None:
    application = PressReadApp(ctx or AppContext())
    if demo:
        application.after(120, application.show_demo)
    elif auto_start:
        application.after(120, application.start_practice)
    application.mainloop()
