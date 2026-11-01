"""PressRead 外刊精读 —— 程序入口。

用法：
    python main.py            正常启动（联网抓取外刊）
    python main.py --demo     直接打开内置离线示例（无需网络）
    python main.py --selftest 打印环境与连通性自检结果
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_OUT_BUFFER: list[str] = []


def _out(text: str = "") -> None:
    """--windowed 打包后没有控制台，sys.stdout 为 None，这里静默跳过。"""
    _OUT_BUFFER.append(text)
    if sys.stdout is None:
        return
    try:
        print(text)
    except Exception:
        pass


def _flush_out() -> None:
    """把自检结果落到日志文件：GUI 版没有控制台，双击运行时只能看这个。"""
    if not _OUT_BUFFER:
        return
    try:
        from pressread import paths

        path = paths.ensure_data_dirs()["logs"] / "selftest.txt"
        path.write_text("\n".join(_OUT_BUFFER) + "\n", encoding="utf-8")
    except Exception:
        pass


def check_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        _out("当前 Python 缺少 tkinter。Windows 请安装 python.org 的完整版 Python（勾选 tcl/tk）。")
        _out(f"当前解释器：{sys.executable}")
        raise SystemExit(2)


def selftest() -> None:
    from pressread import __version__
    from pressread import paths
    from pressread.context import AppContext
    from pressread.config import Config

    _out(f"PressRead {__version__}")
    _out(f"Python {sys.version.split()[0]}  |  {sys.executable}")
    _out(f"数据目录: {paths.data_dir()}")
    cfg = Config.load()
    _out(f"启用来源: {', '.join(cfg.enabled_sources)}")
    ctx = AppContext(cfg)
    started = time.time()
    feeds, errors = ctx.articles.fetch_all_feeds()
    _out(f"RSS 抓取 {time.time() - started:.1f}s: " + ", ".join(f"{k}={len(v)}" for k, v in feeds.items()))
    for error in errors:
        _out(f"  ! {error}")
    if feeds:
        source_id = next(iter(feeds))
        item = feeds[source_id][0]
        try:
            html_text = ctx.http.get_text(item.url, timeout=15)
            article = ctx.articles._extract(item, html_text)
            _out(f"正文抽取: {article.title[:50]} | {len(article.paragraphs)} 段 / {article.word_count} 词")
        except Exception as exc:
            _out(f"正文抽取失败: {exc}")
    try:
        entry = ctx.dictionary.lookup("ubiquitous")
        first = entry.senses[0].definition if entry.senses else ""
        _out(f"英汉词典: {entry.word} | {len(entry.senses)} 义项 | {entry.source_label} | {first[:24]}")
    except Exception as exc:
        _out(f"英汉词典失败: {exc}")
    try:
        entry = ctx.dictionary.lookup_en("ubiquitous")
        _out(f"英英词典: {entry.word} | {len(entry.senses)} 义项 | {entry.source_label}")
    except Exception as exc:
        _out(f"英英词典失败: {exc}")
    try:
        result = ctx.translator.translate("Every night, billions of small creatures swim upward to feed.")
        tone = "支持（必应三档语气）" if ctx.translator.supports_tone() else "未启用必应"
        _out(f"翻译: [{result.provider}] {result.text} | 语气档 {tone}")
    except Exception as exc:
        _out(f"翻译失败: {exc}")
    ctx.close()
    _flush_out()


def smoke() -> int:
    """打包自检：不进主循环，检查随包资源、tkinter 与主窗口能否真正建起来。

    结果写到 exe 同目录的 exe_smoke.txt（可用 PRESSREAD_SMOKE_OUT 覆盖），
    这样 --windowed 打包后即便没有控制台，也能确认 exe 是好的。
    """
    import json
    import tempfile
    from pathlib import Path

    from pressread import __version__, paths

    out = os.environ.get("PRESSREAD_SMOKE_OUT")
    out_path = Path(out) if out else Path(sys.executable).parent / "exe_smoke.txt"
    lines: list[str] = [f"PressRead {__version__} smoke test", f"time {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    ok = True

    def step(name: str, fn) -> None:
        nonlocal ok
        started = time.time()
        try:
            detail = fn()
            lines.append(f"[OK]   {name}  ({time.time() - started:.2f}s)  {detail}")
        except Exception as exc:
            ok = False
            lines.append(f"[FAIL] {name}  ({time.time() - started:.2f}s)  {type(exc).__name__}: {exc}")

    def env_info() -> str:
        import tkinter

        return (
            f"frozen={paths.is_frozen()} python={sys.version.split()[0]} "
            f"tk={tkinter.TkVersion} root={paths.project_root()}"
        )

    def bundled_data() -> str:
        demo = json.loads(paths.resource_path("data", "demo_article.json").read_text(encoding="utf-8"))
        glossary = json.loads(paths.resource_path("data", "glossary.json").read_text(encoding="utf-8"))
        return f"示例文章 {len(demo['paragraphs'])} 段 / 离线词表 {len(glossary)} 条"

    def window() -> str:
        import tkinter as tk

        from pressread.context import AppContext
        from pressread.ui.app import PressReadApp

        app = PressReadApp(AppContext())
        app.update_idletasks()
        app.update()
        width, height = app.winfo_width(), app.winfo_height()
        if width < 800 or height < 600:
            raise RuntimeError(f"窗口尺寸异常 {width}x{height}")
        app.show_demo()
        app.update()
        body = app.reader.text.get("1.0", "end").strip()
        if len(body) < 200:
            raise RuntimeError(f"阅读页正文过短：{len(body)} 字")
        # 「换一段」必须真的推进：v1.5 之前这里调用了不存在的 unit_at，
        # 异常被 Tk 吞掉，按钮看起来毫无反应。自检要守住这条路径。
        first_index = app.reader.unit.index
        first_text = app.reader.unit.text
        app.reader.next_paragraph()
        app.update()
        after = app.reader.unit
        if after is None or after.index <= first_index or after.text == first_text:
            raise RuntimeError("「换一段」没有推进到下一段")
        app.destroy()
        return (
            f"窗口 {width}x{height} / 正文 {len(body)} 字符 / "
            f"换一段 {first_index}→{after.index}"
        )

    step("运行环境", env_info)
    step("随包资源", bundled_data)

    # 自检写到临时目录，绝不碰用户真实数据
    if not os.environ.get("PRESSREAD_HOME"):
        os.environ["PRESSREAD_HOME"] = tempfile.mkdtemp(prefix="pressread_smoke_")
    step("主窗口与离线示例", window)

    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines) + "\n"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    except OSError:
        pass
    _out(text)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="PressRead 外刊精读")
    parser.add_argument("--demo", action="store_true", help="直接打开内置离线示例")
    parser.add_argument("--practice", action="store_true", help="跳过开始页，直接抓一篇开始练习")
    parser.add_argument("--selftest", action="store_true", help="环境与网络自检")
    parser.add_argument("--smoke", action="store_true", help="打包自检（写 exe_smoke.txt）")
    parser.add_argument("--version", action="store_true", help="打印版本")
    args = parser.parse_args(argv)

    if args.version:
        from pressread import __version__

        _out(__version__)
        return
    if args.selftest:
        selftest()
        return
    if args.smoke:
        raise SystemExit(smoke())

    check_tkinter()
    enable_dpi_awareness()
    from pressread.context import AppContext
    from pressread.ui.app import run

    run(AppContext(), demo=args.demo, auto_start=args.practice)


if __name__ == "__main__":
    main()
