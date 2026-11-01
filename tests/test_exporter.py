"""成果导出：Markdown/JSON 内容、文件名与版本号自增。"""

import json
import tempfile
import unittest

from pressread import __version__
from pathlib import Path

from pressread.config import Config
from pressread.exporter import Exporter, bump_version, safe_filename
from pressread.store import Store


def day_str(offset_days: int = 0) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.localtime(time.time() - offset_days * 86400))


class VersionTests(unittest.TestCase):
    def test_patch_bump(self):
        self.assertEqual(bump_version("1.0.0"), "1.0.1")
        self.assertEqual(bump_version("1.0.9"), "1.0.10")
        self.assertEqual(bump_version("v2.3.4"), "2.3.5")

    def test_malformed_resets(self):
        self.assertEqual(bump_version("1.0"), "1.0.0")
        self.assertEqual(bump_version(""), "1.0.0")

    def test_safe_filename(self):
        self.assertEqual(safe_filename('外刊/精读:"一"'), "外刊_精读_一")
        self.assertEqual(safe_filename("   "), "未命名")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store = Store(base / "pressread.db")
        config = Config.load(base / "config.json")
        config.data["daily_goal"] = 5
        self.exporter = Exporter(self.store, config)
        self.out = base / "exports"
        self.day = day_str()

        for index, (text, mine, refs) in enumerate(
            [
                (
                    "Kelp absorbs nitrogen as it grows. A single acre can pull tons of it.",
                    "海带生长时吸收氮。一英亩就能吸收数吨。",
                    [
                        {"en": "Kelp absorbs nitrogen as it grows.", "zh": "海带生长时吸收氮。"},
                        {"en": "A single acre can pull tons of it.", "zh": "一英亩就能吸收数吨。"},
                    ],
                ),
                (
                    "Growers say the next few seasons will decide which projects survive.",
                    "种植者说，未来几个季度将决定哪些项目能活下来。",
                    [
                        {
                            "en": "Growers say the next few seasons will decide which projects survive.",
                            "zh": "种植者说，未来几个季度将决定哪些项目能活下来。",
                        }
                    ],
                ),
            ]
        ):
            practice = self.store.ensure_practice(
                self.day, "article-1", index, text, source_name="WIRED", title="Kelp Boom"
            )
            self.store.complete_practice(practice["id"], mine, refs)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_export_both_files(self):
        result = self.exporter.export(self.day, target_dir=self.out)
        self.assertEqual(result.version, __version__)
        self.assertEqual(len(result.files), 2)
        self.assertEqual(result.paragraphs, 2)
        for path in result.files:
            self.assertTrue(path.exists())
            self.assertIn("外刊精读_每日成果_", path.name)
            self.assertIn(f"_v{__version__}", path.name)

    def test_markdown_content(self):
        result = self.exporter.export(self.day, target_dir=self.out)
        text = next(p for p in result.files if p.suffix == ".md").read_text(encoding="utf-8")
        self.assertIn("# 外刊精读 · 每日成果", text)
        self.assertIn("- 完成段落：2 / 5", text)
        self.assertIn("## 01｜Kelp Boom", text)
        self.assertIn("### 我的译文", text)
        self.assertIn("海带生长时吸收氮。", text)
        self.assertIn("**2.** A single acre can pull tons of it.", text)
        self.assertIn("版权归原媒体所有", text)

    def test_json_content(self):
        result = self.exporter.export(self.day, target_dir=self.out)
        path = next(p for p in result.files if p.suffix == ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["app"], "PressRead")
        self.assertEqual(data["export_version"], __version__)
        self.assertEqual(data["completed"], 2)
        self.assertEqual(data["paragraphs"][0]["source"], "WIRED")
        self.assertEqual(len(data["paragraphs"][0]["references"]), 2)

    def test_version_increments_and_keeps_old_file(self):
        first = self.exporter.export(self.day, target_dir=self.out)
        second = self.exporter.export(self.day, target_dir=self.out)
        self.assertEqual(second.version, bump_version(__version__))
        self.assertTrue(all(p.exists() for p in first.files))
        self.assertNotEqual(first.files[0].name, second.files[0].name)
        # meta 里保留的是「下一次」的版本号
        self.assertEqual(self.exporter.current_version(), bump_version(bump_version(__version__)))
        self.assertIn(f"_v{bump_version(bump_version(__version__))}", self.exporter.preview_name(self.day))

    def test_preview_name(self):
        self.assertTrue(self.exporter.preview_name(self.day).endswith(f"_v{__version__}.md"))

    def test_empty_day_raises(self):
        with self.assertRaises(ValueError):
            self.exporter.export(day_str(5), target_dir=self.out)

    def test_markdown_only(self):
        result = self.exporter.export(self.day, fmt="md", target_dir=self.out)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].suffix, ".md")

    def test_export_recorded(self):
        self.exporter.export(self.day, target_dir=self.out)
        history = self.store.export_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["version"], __version__)
        self.assertEqual(history[0]["paragraphs"], 2)


if __name__ == "__main__":
    unittest.main()
