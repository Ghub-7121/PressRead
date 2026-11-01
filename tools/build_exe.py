r"""把 PressRead 打包成单文件 exe（PyInstaller）。

用法（在项目根目录）：
    ..\..\.venv_press\Scripts\python.exe tools\build_exe.py
    ... --clean      先清掉 build/ 与 dist/ 再打包
    ... --console    打控制台版，方便看 print 与异常堆栈
    ... --no-smoke   打完不跑自检

产物：dist/PressRead.exe（单文件，双击即用，不需要装 Python）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "PressRead"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple4},
    prodvers={tuple4},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          "080404B0",
          [
            StringStruct("CompanyName", "PressRead"),
            StringStruct("FileDescription", "外刊精读 PressRead"),
            StringStruct("FileVersion", "{version}"),
            StringStruct("InternalName", "{name}"),
            StringStruct("LegalCopyright", "仅供个人学习使用"),
            StringStruct("OriginalFilename", "{name}.exe"),
            StringStruct("ProductName", "外刊精读 PressRead"),
            StringStruct("ProductVersion", "{version}"),
          ],
        )
      ]
    ),
    VarFileInfo([VarStruct("Translation", [2052, 1200])]),
  ],
)
"""


def read_version() -> str:
    text = (ROOT / "pressread" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("读不到 pressread/__init__.py 里的 __version__")
    return match.group(1)


def write_version_file(version: str) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    parts = [int(p) for p in version.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    path = BUILD / "version_info.txt"
    path.write_text(
        VERSION_INFO.format(tuple4=tuple(parts), version=version, name=NAME),
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打包 PressRead 为单文件 exe")
    parser.add_argument("--clean", action="store_true", help="打包前清空 build/ 与 dist/")
    parser.add_argument("--console", action="store_true", help="打控制台版（调试用）")
    parser.add_argument("--no-smoke", action="store_true", help="跳过打包后自检")
    args = parser.parse_args(argv)

    if args.clean:
        for path in (BUILD, DIST):
            if path.exists():
                shutil.rmtree(path)

    version = read_version()
    icon = ROOT / "assets" / "app.ico"
    version_file = write_version_file(version)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--clean",
        "--name", NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'data'}{os.pathsep}data",
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--version-file", str(version_file),
        # 只用到标准库，顺手排掉几个用不上的大模块
        "--exclude-module", "pytest",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "setuptools",
        "--noupx",
    ]
    if args.console:
        cmd.append("--console")
    else:
        cmd.append("--windowed")
    if icon.exists():
        cmd += ["--icon", str(icon)]
    else:
        print("! assets/app.ico 不存在，将使用默认图标（可先跑 tools/make_icon.py）")
    cmd.append(str(ROOT / "main.py"))

    print(f"PyInstaller {version} -> {NAME}.exe")
    started = time.time()
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"打包失败，退出码 {result.returncode}")
        return result.returncode

    exe = DIST / f"{NAME}.exe"
    if not exe.exists():
        print("打包结束但没找到 exe")
        return 1
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{exe}  {size_mb:.1f} MB  用时 {time.time() - started:.0f}s")

    if not args.no_smoke:
        return smoke(exe)
    return 0


def smoke(exe: Path) -> int:
    """跑 exe 自检：临时数据目录 + 独立结果文件，不碰用户真实数据。"""
    home = tempfile.mkdtemp(prefix="pressread_exe_")
    out = Path(home) / "exe_smoke.txt"
    env = dict(os.environ, PRESSREAD_HOME=home, PRESSREAD_SMOKE_OUT=str(out))
    print("自检中 ……")
    try:
        subprocess.run([str(exe), "--smoke"], env=env, timeout=180)
    except subprocess.TimeoutExpired:
        print("自检超时（180s）")
        return 1
    text = out.read_text(encoding="utf-8") if out.exists() else "（没有生成自检结果）"
    print(text)
    shutil.rmtree(home, ignore_errors=True)
    return 0 if "RESULT PASS" in text else 1


if __name__ == "__main__":
    raise SystemExit(main())
