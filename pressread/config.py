"""用户配置：JSON 文件，带默认值与逐项合并，缺字段不会炸掉老配置。"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

# 配置结构版本：只在需要「一次性迁移老配置」时 +1
CONFIG_VERSION = 2

DEFAULT_SOURCES = {
    # 已实测可访问、正文可抽取的外刊
    "sciencenews": True,
    "smithsonian": True,
    "wired": True,
    "quanta": True,
    "technologyreview": True,
    "npr": True,
    "nationalgeographic": True,
    "latimes": True,
    "cbsnews": True,
    "politico": True,
    "popsci": True,
    "cnet": True,
    "sciencedaily": True,
    "sciencealert": True,
    "aeon": True,
    "nautilus": True,
    "cnbc": True,
    "astronomy": True,
    # B 篇向：叙事 / 教育 / 人物 / 成长（默认打开最贴近考题口味的几个）
    "americanscholar": True,
    "narratively": True,
    "edutopia": True,
    "cultofpedagogy": True,
    "guideposts": True,
    "storycorps": True,
    "fatherly": True,
    # 视网络环境而定，默认关闭；设置里可以打开
    "fortune": False,
    "time": False,
    "nytimes": False,
    "nature": False,
    "science": False,
    "guardian": False,
    "economist": False,
    "bbc": False,
    "wsj": False,
    "france24": False,
    "un": False,
    # B 篇向但当前网络不通，或题材更窄的来源
    "brevity": False,
    "lithub": False,
    "yesmag": False,
    "orion": False,
    "hippocampus": False,
    "csmonitor": False,
    "insidehighered": False,
    "facultyfocus": False,
    "the74": False,
    "educationnext": False,
    "eschoolnews": False,
    "edweek": False,
    "bigissue": False,
    "positivenews": False,
    "rd": False,
    "motherly": False,
    "snexplores": False,
}

DEFAULTS: dict[str, Any] = {
    "daily_goal": 5,
    "font_scale": 1.0,
    # 一次练习显示多少段（连续抽取；文章不足这么多段时取最多）
    "practice": {
        "paragraphs_per_unit": 1,
    },
    "sources": DEFAULT_SOURCES,
    "oxford": {
        "app_id": "",
        "app_key": "",
        "allow_web": True,
        "allow_fallback": True,
    },
    "translator": {
        # 依次尝试，前一个失败或超时会自动落到下一个。
        # 必应（Microsoft Translator）免 Key、能在国内直连，而且自带语气档，放最前；
        # Google 在多数国内网络下不可达，默认不启用，需要时可在设置里勾上。
        "providers": ["bing", "mymemory"],
        # 默认语气档：default / formal / informal（必应支持正式与非正式）
        "tone": "default",
        "mymemory_email": "",
        "libre_url": "",
        "libre_key": "",
        "target_lang": "zh-CN",
    },
    "cache": {
        "word_ttl_days": 30,
        "text_ttl_days": 14,
        "feed_ttl_minutes": 10,
        # 并发抓正文时「落选」结果的存活时间；程序下次启动也会清空
        "prefetch_ttl_minutes": 60,
    },
    "fetch": {
        # 同时向几个源发起正文请求，谁先给出可用正文就用谁
        "race_width": 5,
        # 单篇正文请求的超时（秒）
        "article_timeout": 15,
        # 整批竞速的总预算（秒）；到了就放弃等待，走兜底
        "race_budget_seconds": 16,
        # 拉各源文章列表的总预算（秒）
        "feed_budget_seconds": 24,
    },
    "ui": {
        "window": "1280x830",
        "sidebar_width": 392,
        "article_font_size": 16,
        "show_demo_on_failure": True,
    },
    "network": {
        "timeout": 18,
        "retries": 1,
        "proxy": "",
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULTS))
    path: Path = field(default_factory=paths.config_path)
    first_run: bool = False

    # ---------- 载入 / 保存 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or paths.config_path()
        first_run = not path.exists()
        raw: dict[str, Any] = {}
        if not first_run:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
        cfg = cls(data=_deep_merge(DEFAULTS, raw), path=path, first_run=first_run)
        if first_run:
            cfg.save()
        elif int(raw.get("config_version") or 0) < CONFIG_VERSION:
            # 老配置升级：v1.7 把翻译链路从 DeepL（要 Key）换成必应（免 Key）
            if cfg._migrate_translator():
                cfg.save()
        return cfg

    # ---------- 版本迁移 ----------
    def _migrate_translator(self) -> bool:
        """把 DeepL 从链路里摘掉，补上免 Key 的必应。只跑一次。"""
        translator = self.data.setdefault("translator", {})
        providers = [p for p in (translator.get("providers") or []) if p != "deepl"]
        if "bing" not in providers:
            providers.insert(0, "bing")
        changed = providers != translator.get("providers")
        translator["providers"] = providers or ["bing", "mymemory"]
        for dead in ("deepl_key", "deepl_pro"):
            if dead in translator:
                translator.pop(dead, None)
                changed = True
        self.data["config_version"] = CONFIG_VERSION
        return changed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    # ---------- 便捷访问 ----------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, value: Any, *keys: str) -> None:
        node = self.data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    @property
    def daily_goal(self) -> int:
        try:
            return max(1, min(20, int(self.get("daily_goal", default=5))))
        except (TypeError, ValueError):
            return 5

    @property
    def paragraphs_per_unit(self) -> int:
        """一次练习默认抽取的连续段数；文章不够时由服务层取最多能取的段。"""
        try:
            value = int(self.get("practice", "paragraphs_per_unit", default=1))
        except (TypeError, ValueError):
            return 1
        return max(1, min(10, value))

    @property
    def enabled_sources(self) -> list[str]:
        sources = self.get("sources", default={}) or {}
        return [sid for sid, on in sources.items() if on]

    @property
    def article_font_size(self) -> int:
        try:
            return max(12, min(28, int(self.get("ui", "article_font_size", default=16))))
        except (TypeError, ValueError):
            return 16

    @property
    def sidebar_width(self) -> int:
        try:
            return max(320, min(560, int(self.get("ui", "sidebar_width", default=392))))
        except (TypeError, ValueError):
            return 392

    def cache_ttl(self, kind: str) -> int:
        if kind == "word":
            days = self.get("cache", "word_ttl_days", default=30)
        else:
            days = self.get("cache", "text_ttl_days", default=14)
        try:
            return max(1, int(days)) * 86400
        except (TypeError, ValueError):
            return 14 * 86400

    def fetch_number(self, key: str, default: float, *, low: float, high: float) -> float:
        """读取 fetch 段的数值配置，越界或类型不对时退回默认值。"""
        try:
            value = float(self.get("fetch", key, default=default))
        except (TypeError, ValueError):
            return default
        if value != value:  # NaN
            return default
        return max(low, min(high, value))
