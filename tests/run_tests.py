"""一键跑全部单元测试 + GUI 冒烟测试。

用法：
    python tests/run_tests.py           只跑单元测试
    python tests/run_tests.py --gui     单元测试 + 离线 GUI 冒烟测试
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("PRESSREAD_HOME", str(ROOT / ".tmp_home" / "unittest"))

    suite = unittest.TestLoader().discover(
        str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    if "--gui" in argv:
        print("\n—— GUI 冒烟测试 ——")
        gui = subprocess.run([sys.executable, str(TESTS / "smoke_test.py")])
        if gui.returncode != 0:
            return gui.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
