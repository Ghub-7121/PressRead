"""抓打包后 exe 的窗口截图（按标题 + 进程号找句柄，PrintWindow，不依赖第三方库）。

用法：
    python tools/shot_exe.py --out build/exe_start.bmp
    python tools/shot_exe.py --out build/exe_demo.bmp --demo
    python tools/shot_exe.py --out build/exe_practice.bmp --practice --wait 22
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import capture_hwnd, user32  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _pids() -> set[int]:
    """当前所有 PressRead.exe 进程（含 PyInstaller 引导进程）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq PressRead.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="ignore",
        ).stdout
    except OSError:
        return set()
    pids: set[int] = set()
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) >= 2 and parts[1].strip('"').isdigit():
            pids.add(int(parts[1].strip('"')))
    return pids


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def find_window(needle: str, timeout: float = 40.0, pids: set[int] | None = None) -> int:
    """按标题找 Tk 顶层窗口。

    只匹配 TkTopLevel 类，且（传了 pids 时）只认这些进程的窗口：
    桌面上可能另有一份同名旧版在跑，不限定进程会抓错窗口。
    """
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if not length or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "TkTopLevel":
            return True
        if pids and _window_pid(hwnd) not in pids:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if needle in buffer.value:
            handles.append(hwnd)
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        handles.clear()
        user32.EnumWindows(callback, 0)
        if handles:
            return handles[0]
        time.sleep(0.3)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给打包后的 exe 截图")
    parser.add_argument("--exe", default=str(ROOT / "dist" / "PressRead.exe"))
    parser.add_argument("--out", default=str(ROOT / "build" / "exe_shot.bmp"))
    parser.add_argument("--demo", action="store_true", help="以离线示例启动")
    parser.add_argument("--practice", action="store_true", help="直接进入今日练习")
    parser.add_argument("--wait", type=float, default=1.5, help="窗口出现后再等几秒")
    parser.add_argument("--maximized", action="store_true")
    args = parser.parse_args(argv)

    home = tempfile.mkdtemp(prefix="pressread_shot_")
    env = dict(os.environ, PRESSREAD_HOME=home)
    if args.maximized:
        env["PRESSREAD_MAXIMIZED"] = "1"
    cmd = [args.exe]
    if args.demo:
        cmd.append("--demo")
    elif args.practice:
        cmd.append("--practice")

    before = _pids()
    proc = subprocess.Popen(cmd, env=env)
    try:
        # 新进程先出现引导进程，再起 GUI 子进程，所以每轮都要重新取进程集合，
        # 否则会拿着只有引导进程的集合去过滤，永远找不到窗口。
        hwnd, deadline = 0, time.time() + 40
        while time.time() < deadline and not hwnd:
            new_pids = _pids() - before
            if new_pids:
                hwnd = find_window("PressRead", timeout=2.0, pids=new_pids)
            time.sleep(0.5)
        if not hwnd:
            print("没找到窗口")
            return 1
        time.sleep(args.wait)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        ok = capture_hwnd(hwnd, str(out))
        print(f"capture={ok} -> {out}")
        return 0 if ok else 1
    finally:
        # PyInstaller onefile 的引导进程会再起一个子进程，只 terminate 父进程
        # 会留下「幽灵窗口」，下一轮截图就会抓到旧窗口。必须按进程树杀。
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
