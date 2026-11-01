"""开发用截图：把 Tk 窗口抓成 BMP（Windows GDI，无第三方依赖）。

用法：在程序里调用 capture_window(root, "tests/shots/start.bmp")。
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _hwnd(root) -> int:
    try:
        return int(root.wm_frame(), 16)
    except Exception:
        return int(root.winfo_id())


def capture_window(root, path: str, *, padding: int = 0) -> bool:
    root.update_idletasks()
    return capture_hwnd(_hwnd(root), path, padding=padding)


def capture_hwnd(hwnd: int, path: str, *, padding: int = 0) -> bool:
    """按窗口句柄抓图，可用于抓别的进程（例如打包后的 exe）。"""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left + padding * 2
    height = rect.bottom - rect.top + padding * 2
    if width <= 0 or height <= 0:
        return False

    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(memdc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, memdc, PW_RENDERFULLCONTENT)
        if not ok:
            gdi32.BitBlt(memdc, 0, 0, width, height, hdc, 0, 0, SRCCOPY)

        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # 负数 = 自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        info = BITMAPINFO()
        info.bmiHeader = header
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
        if rows == 0:
            return False

        file_header = struct.pack(
            "<2sIHHI", b"BM", 14 + 40 + len(buffer.raw), 0, 0, 14 + 40
        )
        with open(path, "wb") as handle:
            handle.write(file_header)
            handle.write(bytes(ctypes.string_at(ctypes.byref(info.bmiHeader), 40)))
            handle.write(buffer.raw)
        return True
    finally:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)
