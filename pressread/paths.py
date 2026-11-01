"""运行路径解析。

数据默认放在用户目录（Windows: %APPDATA%\\PressRead），
设置环境变量 PRESSREAD_HOME 可以整体重定向（测试与便携模式用）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "PressRead"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """源码根目录 / PyInstaller 解包目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """随程序分发的只读资源（data/ 下的示例文章、词表）。"""
    return project_root().joinpath(*parts)


def data_dir() -> Path:
    env = os.environ.get("PRESSREAD_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".pressread"


def ensure_data_dirs() -> dict[str, Path]:
    root = data_dir()
    paths = {
        "root": root,
        "exports": root / "exports",
        "logs": root / "logs",
        "cache": root / "cache",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def db_path() -> Path:
    return data_dir() / "pressread.db"


def config_path() -> Path:
    return data_dir() / "config.json"
