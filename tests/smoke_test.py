"""GUI 冒烟测试：不联网，用内置离线示例 + 预置缓存把主要界面走一遍并截图。

用法：
    python tests/smoke_test.py            离线（默认，秒级）
    python tests/smoke_test.py --online   额外验证真实查词/翻译链路（需要网络）

截图写到 tests/shots/*.bmp，检查项结果以 JSON 打印，任何一项失败退出码为 1。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SHOTS = ROOT / "tests" / "shots"
RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})
    return ok


def pump(app, seconds: float = 0.25) -> None:
    """跑一段 Tk 事件循环，让 after 调度与后台任务回调落地。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            app.update()
        except Exception:
            return
        time.sleep(0.02)


def wait_until(app, predicate, timeout: float = 6.0, step: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            app.update()
        except Exception:
            return False
        if predicate():
            return True
        time.sleep(step)
    return False


def capture(app, name: str, window=None) -> None:
    from capture import capture_window

    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.bmp"
    try:
        ok = capture_window(window or app, str(path))
    except Exception as exc:  # 截图失败不影响功能判定
        record(f"截图 {name}", False, f"{type(exc).__name__}: {exc}")
        return
    record(f"截图 {name}", ok is not False, str(path))


def sidebar_texts(panel) -> list[str]:
    """把侧栏当前渲染出来的所有文字收集起来，用于判断「替换」还是「追加」。"""
    out: list[str] = []

    def walk(widget):
        for child in widget.winfo_children():
            try:
                text = child.cget("text")
            except Exception:
                text = ""
            if text:
                out.append(str(text))
            walk(child)

    try:
        walk(panel.body.inner)
    except Exception:
        return out
    return out


def seed_word_cache(ctx) -> str:
    """预置英汉 + 英英两份词条缓存，离线也能检查侧栏渲染与缓存命中。"""
    from pressread.dictionary import DictEntry, Sense

    zh = DictEntry(
        word="twilight",
        provider="cambridge",
        source_label="剑桥英汉双解词典 · Cambridge English-Chinese (Simplified)",
        source_url="https://dictionary.cambridge.org/dictionary/english-chinese-simplified/twilight",
        lang="zh",
        phonetics={"uk": "/ˈtwaɪ.laɪt/", "us": "/ˈtwaɪ.laɪt/"},
        senses=[
            Sense(
                pos="noun",
                definition="暮色;黄昏时分",
                gloss="the faint light or the period of time at the end of the day "
                      "after the sun has gone down",
                labels=["literary"],
                examples=[
                    "It was hard to see him clearly in the twilight. — 暮色中很难看清他。",
                ],
            ),
            Sense(
                pos="noun",
                definition="衰退期，末期",
                gloss="the period of time when something is coming to an end",
                grammar="singular",
                examples=["the twilight of his career — 他事业的末期"],
            ),
        ],
    )
    ctx.store.cache_put("word", "twilight", zh.to_dict(), provider=zh.provider, scope="zh")

    en = DictEntry(
        word="twilight",
        provider="oxford_web",
        source_label="牛津学习者词典 · Oxford Learner's Dictionaries",
        source_url="https://www.oxfordlearnersdictionaries.com/definition/english/twilight",
        lang="en",
        phonetics={"uk": "/ˈtwaɪlaɪt/"},
        senses=[
            Sense(
                pos="noun",
                definition="the faint light or the period of time at the end of the day "
                           "after the sun has gone down",
                labels=["uncountable"],
                examples=["It was hard to see him clearly in the twilight."],
            ),
        ],
        origin="late Middle English: from Old English twēon (two) + lēoht (light).",
    )
    ctx.store.cache_put("word", "twilight", en.to_dict(), provider=en.provider, scope="en")
    return zh.word


def main(argv: list[str]) -> int:
    online = "--online" in argv

    home = ROOT / ".tmp_home" / "smoke"
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PRESSREAD_HOME"] = str(home)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    from main import enable_dpi_awareness

    enable_dpi_awareness()

    from pressread.context import AppContext
    from pressread.ui.app import PressReadApp

    ctx = AppContext()
    app = PressReadApp(ctx)
    app.geometry("1280x830+60+40")
    pump(app, 0.6)

    try:
        # ---------------------------------------------------------- 开始页
        record("开始页渲染", app.start.winfo_ismapped(), f"geometry={app.winfo_geometry()}")
        record("今日目标读取", ctx.daily.goal == ctx.config.daily_goal, f"goal={ctx.daily.goal}")
        capture(app, "01_start")

        # ---------------------------------------------------------- 阅读页（离线示例）
        app.show_demo()
        pump(app, 0.8)
        reader = app.reader
        record("离线示例加载", bool(reader.unit and reader.sentences), f"{len(reader.sentences)} 句")
        record(
            "正文分句与译文对齐",
            len(reader.sentences) == len(reader.bundled) and len(reader.sentences) > 0,
            f"{len(reader.sentences)}/{len(reader.bundled)}",
        )
        record("置灰的完成按钮", "disabled" in reader.complete_btn.state(), reader.status.cget("text"))
        capture(app, "02_reader")

        # ---------------------------------------------------------- 换一段
        first_index = reader.unit.index
        first_text = reader.unit.text
        reader.next_paragraph()
        pump(app, 0.3)
        moved = (
            reader.unit is not None
            and reader.unit.index > first_index
            and reader.unit.text != first_text
        )
        record(
            "换一段推进到下一段",
            moved,
            f"index {first_index} → {reader.unit.index if reader.unit else None}",
        )
        # 回到第一段，后面的逐句揭示按一段走
        reader.display_unit(ctx.articles.demo_unit_at(0))
        pump(app, 0.3)

        # ---------------------------------------------------------- 一次显示多段
        ctx.config.data.setdefault("practice", {})["paragraphs_per_unit"] = 3
        multi = ctx.articles.demo_unit_at(0)
        reader.display_unit(multi)
        pump(app, 0.4)
        record(
            "一次显示多段",
            multi.span == 3 and "连续 3 段" in reader.meta_line.cget("text"),
            f"span={multi.span} / {reader.meta_line.cget('text')[:46]}",
        )
        record(
            "多段正文含分段空行",
            multi.text.count("\n\n") == multi.span - 1,
            f"{multi.text.count(chr(10) * 2)} 处空行 / span={multi.span}",
        )
        record(
            "多段译文逐句对齐",
            len(multi.sentences) == len(multi.bundled_translations) and multi.span > 1,
            f"{len(multi.sentences)}/{len(multi.bundled_translations)}",
        )
        # 露两句译文，把「分段缩进 + 译文中段空行」一起截进图里
        for _ in range(2):
            reader.reveal_next()
            pump(app, 0.3)
        # 译文渲染会把视图带到译文区，截图前滚回正文开头看段落结构
        try:
            reader.text.see("1.0")
        except Exception:
            pass
        pump(app, 0.2)
        capture(app, "02b_multi")
        # 再截一张译文区：多揭示三句，让第 1 段与第 2 段的译文同时出现，段间空行才看得见
        for _ in range(3):
            reader.reveal_next()
            pump(app, 0.25)
        try:
            reader.text.see("end")
        except Exception:
            pass
        pump(app, 0.2)
        capture(app, "02c_multi_zh")
        # 复原为单段，后面按默认设置跑
        ctx.config.data["practice"]["paragraphs_per_unit"] = 1
        reader.display_unit(ctx.articles.demo_unit_at(0))
        pump(app, 0.4)

        # ---------------------------------------------------------- 打开原文
        record(
            "离线示例禁用打开原文",
            str(reader.open_btn.cget("state")) == "disabled",
            f"state={reader.open_btn.cget('state')}",
        )
        from pressread.article import Article
        import pressread.ui.reader as reader_mod

        opened: list[str] = []
        real_open = reader_mod.webbrowser.open
        reader_mod.webbrowser.open = lambda url, new=0: (opened.append(url), True)[1]
        backup = reader.article
        try:
            reader.article = Article(
                url="https://example.com/kelp", source_id="aeon", source_name="Aeon",
                category="文化", title="示例", paragraphs=["Kelp grows fast."],
            )
            reader._sync_open_button()
            record(
                "在线文章可打开原文",
                str(reader.open_btn.cget("state")) != "disabled",
                f"state={reader.open_btn.cget('state')}",
            )
            reader.open_source()
            record("打开原文调用浏览器", opened == ["https://example.com/kelp"], str(opened))
        finally:
            reader_mod.webbrowser.open = real_open
            reader.article = backup
            reader._sync_open_button()

        # ---------------------------------------------------------- 侧栏词典
        word = seed_word_cache(ctx)
        reader._lookup_word(word)
        ok = wait_until(app, lambda: app.sidebar._dict_view[0] == "result", 8)
        pump(app, 0.6)
        record("词典侧栏就绪", ok, f"state={app.sidebar._dict_view[0]}")
        entry = app.sidebar._dict_view[1][0]
        record("默认查英汉（剑桥）", entry.provider == "cambridge", f"{entry.provider} / {len(entry.senses)} 义项")
        record(
            "英英未切换不加载",
            app.sidebar._en_state == "idle" and app.sidebar._dict_lang == "zh",
            f"state={app.sidebar._en_state} / lang={app.sidebar._dict_lang}",
        )
        capture(app, "03_dict")

        # 双状态按钮：切到「英英」才查牛津，并且是原地替换而不是追加
        app.sidebar._set_dict_lang("en")
        ok = wait_until(app, lambda: app.sidebar._en_state in ("ready", "error"), 8)
        pump(app, 0.6)
        record(
            "切到英英才查牛津",
            ok and app.sidebar._en_state == "ready",
            f"state={app.sidebar._en_state} / {app.sidebar._en_word}",
        )
        texts = sidebar_texts(app.sidebar)
        has_en = any("the faint light" in t for t in texts)
        has_zh = any("暮色" in t for t in texts)
        record(
            "英英原地替换英汉",
            app.sidebar._dict_lang == "en" and has_en and not has_zh,
            f"lang={app.sidebar._dict_lang} en={has_en} zh={has_zh}",
        )
        capture(app, "03b_dict_en")

        # 切回英汉：不再请求，内容立刻换回来
        calls = ctx.dictionary._memory
        depth = len(calls)
        app.sidebar._set_dict_lang("zh")
        pump(app, 0.4)
        texts = sidebar_texts(app.sidebar)
        record(
            "切回英汉不重新请求",
            app.sidebar._dict_lang == "zh"
            and any("暮色" in t for t in texts)
            and len(calls) == depth,
            f"lang={app.sidebar._dict_lang} memory={len(calls)}",
        )

        # ---------------------------------------------------------- 逐句揭示
        before = len(reader.revealed)
        reader.reveal_next()
        wait_until(app, lambda: len(reader.revealed) > before, 6)
        record("显示下一句译文", len(reader.revealed) == before + 1, f"{before} → {len(reader.revealed)}")
        capture(app, "04_reveal")

        # 全部揭示（内置示例无需网络，逐句跑完）
        guard = 0
        while reader._next_hidden() is not None and guard < 12:
            guard += 1
            count = len(reader.revealed)
            reader.reveal_next()
            wait_until(app, lambda: len(reader.revealed) > count, 6)
        total = len(reader.sentences)
        record("全部译文可显示", len(reader.revealed) == total, f"{len(reader.revealed)}/{total}")
        record("完成后按钮解禁", "disabled" not in reader.complete_btn.state(), reader.status.cget("text"))

        # ---------------------------------------------------------- 完成本段
        reader.mark_complete()
        pump(app, 0.5)
        done = ctx.daily.done_count()
        record("完成后计数", done >= 1, f"done={done} goal={ctx.daily.goal}")

        # ---------------------------------------------------------- 首页 / 历史
        app.go_start()
        pump(app, 0.5)
        record("返回首页", app.start.winfo_ismapped(), app.start.primary_btn.cget("text"))
        capture(app, "05_start_after")

        app.go_history()
        pump(app, 0.4)
        record("历史页渲染", app.history.winfo_ismapped())
        capture(app, "06_history")

        # ---------------------------------------------------------- 设置窗口
        app.go_start()
        pump(app, 0.3)
        app.open_settings()
        dlg = app._settings_dialog
        pump(app, 0.6)
        record("设置窗口打开", bool(dlg and dlg.winfo_exists()))

        notebooks = [
            child for child in dlg.winfo_children() if child.winfo_class() == "TNotebook"
        ]
        from pressread.feeds import PRESETS, SOURCES
        if notebooks:
            notebook = notebooks[0]
            record(
                "来源复选框数量",
                len(dlg.source_vars) > 0,
                f"{len(dlg.source_vars)} 个来源",
            )

            # 新版来源管理：搜索、分类、批量、快捷方案
            dlg.search_var.set("教育")
            pump(app, 0.15)
            cats = {row.source.category for row in dlg._rows}
            record(
                "来源搜索按关键词过滤",
                bool(dlg._rows) and cats <= {"教育"},
                f"{len(dlg._rows)} 行 · {cats}",
            )

            dlg.search_var.set("")
            pump(app, 0.15)
            dlg._set_category("人物")
            pump(app, 0.15)
            record(
                "分类导航过滤",
                {row.source.category for row in dlg._rows} == {"人物"},
                f"{len(dlg._rows)} 行",
            )

            dlg._bulk(False)
            off = all(not dlg.source_vars[s.id].get() for s in SOURCES if s.category == "人物")
            dlg._bulk(True)
            on = all(dlg.source_vars[s.id].get() for s in SOURCES if s.category == "人物")
            record("批量取消/全选本类", off and on, f"off={off} on={on}")

            ids = next(p[3] for p in PRESETS if p[0] == "narrative")
            dlg._apply_preset(ids, "叙事向")
            chosen = sum(1 for s in dlg.source_vars if dlg.source_vars[s].get())
            record("快捷方案套用", chosen == len(set(ids)), f"{chosen}/{len(set(ids))}")

            dlg._reset_sources()
            dlg._set_category("全部")
            pump(app, 0.3)
            capture(app, "07_settings_sources", dlg)

            # 自绘复选框：点一下要真的翻转变量
            key = next(iter(dlg.source_vars))
            var = dlg.source_vars[key]
            before = bool(var.get())
            var.set(not before)
            pump(app, 0.1)
            record("自绘复选框可切换", bool(var.get()) == (not before), key)
            dlg._reset_sources()

            notebook.select(1)
            pump(app, 0.3)
            capture(app, "08_settings_dict", dlg)

            notebook.select(3)
            pump(app, 0.3)
            record(
                "字号显示与滑块一致",
                dlg.size_label.cget("text").startswith(str(dlg.size_var.get())),
                f"{dlg.size_label.cget('text')} / var={dlg.size_var.get()}",
            )
            capture(app, "09_settings_ui", dlg)
        dlg.destroy()
        pump(app, 0.2)

        # ---------------------------------------------------------- 可选：真实网络
        if online:
            app.show_reader()  # 回到阅读页，让边栏可见
            pump(app, 0.5)
            started = time.time()
            reader._lookup_word("ubiquitous")
            ok = wait_until(app, lambda: app.sidebar._dict_view[0] in ("result", "error"), 20)
            pump(app, 0.4)
            state = app.sidebar._dict_view[0]
            record("在线查词（ubiquitous）", ok, f"{state} / {time.time() - started:.1f}s")
            capture(app, "10_online_dict")

            reader.text.tag_add("sel", "s0.first", "s0.last")
            started = time.time()
            reader._translate_selection()
            ok = wait_until(app, lambda: app.sidebar._trans_view[0] in ("result", "error"), 25)
            pump(app, 0.4)
            record(
                "在线翻译（选中整句）",
                ok and app.sidebar._trans_view[0] == "result",
                f"{app.sidebar._trans_view[0]} / {time.time() - started:.1f}s",
            )
            capture(app, "11_online_translate")

        # ---------------------------------------------------------- 缓存与导出
        stats = ctx.store.cache_stats()
        record("缓存记录", stats["total"] >= 1, json.dumps(stats, ensure_ascii=False))
        record("缓存命中计数", stats["hits"] >= 1, f"hits={stats['hits']}")

        result = ctx.exporter.export(ctx.daily.today())
        record("导出成果", all(p.exists() for p in result.files), f"v{result.version} {result.files[0].name}")
        record(
            "版本号格式",
            result.version.count(".") == 2 and result.files[0].name.endswith(f"_v{result.version}.md"),
            result.version,
        )

    except Exception:
        record("未捕获异常", False, traceback.format_exc())
    finally:
        try:
            app._on_close()
        except Exception:
            pass

    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"passed": len(RESULTS) - len(failed), "failed": len(failed), "items": RESULTS},
                     ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
