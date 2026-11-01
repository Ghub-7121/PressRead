"""依赖装配：一个 AppContext 持有配置、数据库、网络、各项服务。"""

from __future__ import annotations

from . import paths
from .config import Config
from .daily import DailyTask
from .dictionary import DictionaryService, LocalGlossary
from .exporter import Exporter
from .fetch import HttpClient
from .service import ArticleService
from .store import Store
from .translate import Translator


class AppContext:
    def __init__(self, config: Config | None = None):
        self.paths = paths.ensure_data_dirs()
        self.config = config or Config.load()
        self.store = Store(paths.db_path())
        # 并发抓取留下的「落选」文章只在同一次运行内有效：
        # 下次打开程序先清空（未满 1 小时的也清）。这里只 DELETE
        # prefetch_cache 一张表，词典缓存、翻译缓存、历史文章、每日练习、
        # 各源文章列表缓存都不受影响。清理失败也不该挡住启动。
        try:
            self.prefetch_purged = self.store.prefetch_purge()
        except Exception:
            self.prefetch_purged = 0
        timeout = self.config.get("network", "timeout", default=18)
        retries = self.config.get("network", "retries", default=1)
        proxy = self.config.get("network", "proxy", default="") or ""
        try:
            timeout = float(timeout)
            retries = int(retries)
        except (TypeError, ValueError):
            timeout, retries = 18.0, 1
        self.http = HttpClient(timeout=timeout, retries=retries, proxy=proxy)
        self.dictionary = DictionaryService(
            self.http, self.store, self.config, glossary=LocalGlossary()
        )
        self.translator = Translator(self.http, self.store, self.config)
        self.articles = ArticleService(self.config, self.store, self.http)
        self.daily = DailyTask(self.store, self.config)
        self.exporter = Exporter(self.store, self.config)

    def close(self) -> None:
        self.store.close()
