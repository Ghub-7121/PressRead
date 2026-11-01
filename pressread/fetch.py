"""轻量 HTTP 客户端：只用标准库，带重试、编码识别、熔断与代理支持。

外刊站点分布在世界各地，国内网络对部分域名不友好。这里不假设任何站点一定可达：
失败会抛出带中文说明的 NetworkError，上层负责切换来源或降级到离线示例。
"""

from __future__ import annotations

import gzip
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

MAX_BYTES = 6 * 1024 * 1024


class NetworkError(Exception):
    """所有网络错误的统一出口，message 面向用户。"""

    def __init__(self, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.url = url
        self.status = status


class CircuitOpen(NetworkError):
    pass


@dataclass
class Response:
    url: str
    status: int
    text: str
    elapsed: float
    headers: dict[str, str]


class HttpClient:
    def __init__(
        self,
        timeout: float = 18.0,
        retries: int = 1,
        user_agent: str = DEFAULT_UA,
        proxy: str = "",
    ):
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.user_agent = user_agent
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        handlers: list[Any] = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        # 不要传自定义 HTTPSHandler(context=...)：那会改变 TLS 指纹，
        # Politico / Undark 这类站点的 WAF 会直接回 403。urllib 默认上下文
        # 同样做证书与主机名校验，安全性不变。
        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------ 基础
    @staticmethod
    def _host(url: str) -> str:
        try:
            return urllib.parse.urlsplit(url).hostname or ""
        except ValueError:
            return ""

    def _check_circuit(self, host: str, url: str) -> None:
        until = self._open_until.get(host, 0)
        if until > time.time():
            raise CircuitOpen(f"{host} 连续失败，已暂停访问 {int(until - time.time())} 秒", url=url)

    def _record(self, host: str, ok: bool) -> None:
        if ok:
            self._failures.pop(host, None)
            self._open_until.pop(host, None)
            return
        count = self._failures.get(host, 0) + 1
        self._failures[host] = count
        if count >= 3:
            self._open_until[host] = time.time() + 90
            self._failures[host] = 0

    def _decode(self, raw: bytes, headers: Any) -> str:
        encoding = (headers.get("Content-Encoding") or "").lower()
        try:
            if "gzip" in encoding:
                raw = gzip.decompress(raw)
            elif "deflate" in encoding:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except (OSError, zlib.error):
            pass
        charset = ""
        try:
            charset = headers.get_content_charset() or ""
        except Exception:
            charset = ""
        if not charset:
            head = raw[:4096].decode("ascii", "ignore").lower()
            idx = head.find("charset=")
            if idx >= 0:
                charset = head[idx + 8 : idx + 28].split('"')[0].split("'")[0]
                charset = charset.strip().split(";")[0].split(">")[0].strip()
        for candidate in (charset, "utf-8", "gb18030", "latin-1"):
            if not candidate:
                continue
            try:
                return raw.decode(candidate)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", "replace")

    def request(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        method: str | None = None,
        retries: int | None = None,
    ) -> Response:
        host = self._host(url)
        self._check_circuit(host, url)
        hdrs = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
            # 部分站点（如 Politico）的 WAF 只放行带 Sec-Fetch-* 的「浏览器式」请求
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if headers:
            hdrs.update(headers)
        # 翻译这类「后面还有备胎」的请求可以传 retries=0，失败就立刻换源，
        # 不必按全局重试次数把用户晾在超时上
        attempts = (self.retries if retries is None else max(0, int(retries))) + 1
        last_error: Exception | None = None
        started = time.time()
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
                with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                    raw = resp.read(MAX_BYTES)
                    text = self._decode(raw, resp.headers)
                    self._record(host, True)
                    return Response(
                        url=resp.geturl(),
                        status=resp.status,
                        text=text,
                        elapsed=time.time() - started,
                        headers={k.lower(): v for k, v in resp.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                body = ""
                try:
                    body = exc.read(2048).decode("utf-8", "ignore")
                except Exception:
                    pass
                # 4xx 不重试（除了 429）
                if exc.code < 500 and exc.code != 429:
                    self._record(host, False)
                    raise NetworkError(
                        f"服务器返回 {exc.code}", url=url, status=exc.code
                    ) from None
                if attempt == attempts - 1:
                    break
            except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, ConnectionError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
            except OSError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
            time.sleep(0.6 * (attempt + 1))
        self._record(host, False)
        reason = self._explain(last_error)
        raise NetworkError(reason, url=url)

    @staticmethod
    def _explain(exc: Exception | None) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            return f"服务器返回 {exc.code}，稍后再试"
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return "连接超时（该站点在当前网络下可能不可达）"
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLError):
                return "TLS 握手失败（网络可能被拦截）"
            if isinstance(reason, socket.gaierror):
                return "域名解析失败，请检查网络或代理"
            return f"网络请求失败：{reason}"
        if isinstance(exc, ConnectionError):
            return "连接被重置，请检查网络或代理"
        return "网络请求失败，请稍后重试"

    # ------------------------------------------------------------------ 便捷
    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.request(url, **kwargs).text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        from . import __version__

        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("Accept", "application/json")
        headers.setdefault("X-PressRead-Version", __version__)
        resp = self.request(url, headers=headers, **kwargs)
        try:
            return json.loads(resp.text)
        except ValueError as exc:
            raise NetworkError("返回内容不是合法 JSON", url=url) from exc

    def post_json(self, url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        headers.setdefault("Accept", "application/json")
        resp = self.request(url, data=body, headers=headers, method="POST", **kwargs)
        try:
            return json.loads(resp.text)
        except ValueError as exc:
            raise NetworkError("返回内容不是合法 JSON", url=url) from exc

    def post_form(self, url: str, fields: dict[str, str], **kwargs: Any) -> Response:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return self.request(url, data=body, headers=headers, method="POST", **kwargs)
