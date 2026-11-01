"""翻译服务：按配置顺序尝试多个真实翻译站点，并把结果写进本地缓存。

默认链路 必应（Microsoft Translator）→ MyMemory → Google。
- 必应走的是网页版自己的接口（cn.bing.com 页面里取 IG / IID / token），
  **不需要任何 API Key**，并且原生支持「标准 / 非正式 / 正式」三档语气，
  这是目前唯一免 Key 又能真的换语气的引擎。
- MyMemory / Google 不区分语气，只在「自动」档参与；切到正式或非正式时
  会跳过它们，而不是拿普通译文冒充正式译法。
所有服务都失败时抛出带中文说明的 TranslationError，界面给「重试」按钮，
不会静默显示空译文。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .fetch import HttpClient, NetworkError
from .textproc import normalize_space, split_sentences

MYMEMORY = "https://api.mymemory.translated.net/get"
GOOGLE = "https://translate.googleapis.com/translate_a/single"
# 必应翻译网页版：页面里带 IG / IID 与防滥用 token，接口本身不需要 Key。
# 国内访问 cn.bing.com 比 www.bing.com 稳，前者失败再退到后者。
BING_SITES = ("https://cn.bing.com/translator", "https://www.bing.com/translator")
BING_API = "https://{host}/ttranslatev3?isVertical=1&IG={ig}&IID={iid}"
MAX_CHUNK = 450
# 必应单次最多 1000 字符，留出余量
BING_CHUNK = 900
# 单次翻译请求的超时（秒）。不通的源要尽快让位给下一个，不能按全局 18 秒死等
PROVIDER_TIMEOUT = 9

# 语气档：界面上三个 chip
TONES = {
    "default": "自动",
    "formal": "正式",
    "informal": "非正式",
}
# 必应网页版「语气」下拉的三个取值：标准 / 非正式 / 正式。
# 标准就是默认输出，不用带 tone 参数（带上反而会返回空响应）。
BING_TONES = {
    "formal": "Formal",
    "informal": "Casual",
}


class TranslationError(Exception):
    def __init__(self, message: str, *, provider: str = ""):
        super().__init__(message)
        self.message = message
        self.provider = provider


@dataclass
class TranslationResult:
    text: str
    provider: str
    source_label: str
    source_url: str = ""
    alternatives: list[str] = field(default_factory=list)
    cached: bool = False
    elapsed_ms: int = 0
    note: str = ""
    tone: str = "default"
    tone_capable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "source_label": self.source_label,
            "source_url": self.source_url,
            "alternatives": self.alternatives,
            "note": self.note,
            "tone": self.tone,
            "tone_capable": self.tone_capable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranslationResult":
        return cls(
            text=data.get("text", ""),
            provider=data.get("provider", ""),
            source_label=data.get("source_label", ""),
            source_url=data.get("source_url", ""),
            alternatives=list(data.get("alternatives", [])),
            note=data.get("note", ""),
            tone=data.get("tone", "default"),
            tone_capable=bool(data.get("tone_capable", False)),
        )


def _chunks(text: str, limit: int = MAX_CHUNK) -> list[str]:
    text = normalize_space(text)
    if len(text) <= limit:
        return [text] if text else []
    sentences = split_sentences(text)
    out: list[str] = []
    buf = ""
    for sentence in sentences:
        while len(sentence) > limit:
            out.append(sentence[:limit])
            sentence = sentence[limit:]
        if buf and len(buf) + 1 + len(sentence) > limit:
            out.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        out.append(buf)
    return out


class Translator:
    LABELS = {
        "bing": ("必应翻译 · Microsoft Translator", "https://cn.bing.com/translator"),
        "mymemory": ("MyMemory 翻译记忆库", "https://mymemory.translated.net/"),
        "google": ("Google 翻译", "https://translate.google.com/"),
        "libre": ("LibreTranslate", "https://libretranslate.com/"),
    }
    # 目前只有必应提供免 Key 的语气档
    TONE_PROVIDERS = ("bing",)

    def __init__(self, http: HttpClient, store, config):
        self.http = http
        self.store = store
        self.config = config
        self._failures: dict[str, tuple[int, float]] = {}
        # 必应页面的 IG / IID / token 一个小时内有效，过期或返回 205 时重取
        self._bing: dict[str, Any] | None = None

    # --------------------------------------------------------------- 对外接口
    def translate(
        self, text: str, *, force: bool = False, tone: str | None = None
    ) -> TranslationResult:
        clean = normalize_space(text)
        if not clean:
            raise TranslationError("没有可翻译的文本")
        if len(clean) > 4000:
            clean = clean[:4000]
        target = self.config.get("translator", "target_lang", default="zh-CN") or "zh-CN"
        tone = tone or self.config.get("translator", "tone", default="default") or "default"
        if tone not in TONES:
            tone = "default"
        # 语气不同 = 不同的译文，缓存必须分开，否则切语气会读到旧结果
        scope = f"en>{target}" if tone == "default" else f"en>{target}|{tone}"

        if not force:
            ttl = self.config.cache_ttl("text") if hasattr(self.config, "cache_ttl") else None
            cached = self.store.cache_get("translation", clean, scope=scope, max_age=ttl)
            if cached:
                result = TranslationResult.from_dict(cached["payload"])
                result.cached = True
                return result

        errors: list[str] = []
        tone_skipped = False
        for provider_id in self._provider_order():
            if tone != "default" and provider_id not in self.TONE_PROVIDERS:
                # 不支持语气的源，只在「自动」档参与；否则会给出名不副实的结果
                tone_skipped = True
                continue
            if self._circuit_open(provider_id):
                errors.append(f"{self.LABELS.get(provider_id, (provider_id,))[0]} 暂时不可用")
                continue
            try:
                result = self._run_provider(provider_id, clean, target, tone)
            except (NetworkError, TranslationError) as exc:
                message = getattr(exc, "message", str(exc))
                errors.append(f"{self._short(provider_id)}：{message}")
                self._record_failure(provider_id)
                continue
            except Exception as exc:  # 解析异常
                errors.append(f"{self._short(provider_id)}：返回格式异常（{exc}）")
                self._record_failure(provider_id)
                continue
            if result and result.text:
                self.store.cache_put(
                    "translation", clean, result.to_dict(), provider=provider_id, scope=scope
                )
                self._failures.pop(provider_id, None)
                return result

        if tone != "default" and tone_skipped and not errors:
            raise TranslationError(
                f"「{TONES[tone]}」语气由必应翻译提供（免 Key），"
                "请在设置 → 词典与翻译里勾选「必应翻译」，或把语气切回「自动」。",
                provider="",
            )
        if tone != "default" and tone_skipped:
            raise TranslationError(
                f"「{TONES[tone]}」语气只有必应翻译支持，当前必应不可用；"
                "可切回「自动」用其他翻译源。",
                provider="",
            )
        detail = errors[0] if errors else "没有可用的翻译源"
        raise TranslationError(f"翻译失败：{detail}", provider="")

    def supports_tone(self) -> bool:
        """必应免 Key，只要启用了这条链路就能切语气。"""
        return "bing" in self._provider_order()

    # ------------------------------------------------------------------ 内部
    def _provider_order(self) -> list[str]:
        known = ("bing", "google", "mymemory")
        configured = (
            self.config.get("translator", "providers", default=["bing", "mymemory"]) or []
        )
        order = [p for p in configured if p in known]
        libre_url = self.config.get("translator", "libre_url", default="")
        if libre_url and "libre" not in order:
            order.append("libre")
        if not order:
            # 一个都没配时给最基本的免 Key 组合，至少不会整句翻不出来
            order = ["bing", "mymemory"]
        return order

    @staticmethod
    def _short(provider_id: str) -> str:
        return {
            "bing": "必应翻译",
            "mymemory": "MyMemory",
            "google": "Google 翻译",
            "libre": "LibreTranslate",
        }.get(provider_id, provider_id)

    def _circuit_open(self, provider_id: str) -> bool:
        count, until = self._failures.get(provider_id, (0, 0.0))
        return count >= 2 and until > time.time()

    def _record_failure(self, provider_id: str) -> None:
        count, _ = self._failures.get(provider_id, (0, 0.0))
        count += 1
        until = time.time() + 120 if count >= 2 else 0.0
        self._failures[provider_id] = (count, until)

    def _run_provider(
        self, provider_id: str, text: str, target: str, tone: str = "default"
    ) -> TranslationResult:
        if provider_id == "bing":
            result = self._bing_translate(text, target, tone)
        elif provider_id == "mymemory":
            result = self._mymemory(text, target)
        elif provider_id == "google":
            result = self._google(text, target)
        elif provider_id == "libre":
            result = self._libre(text, target)
        else:
            raise TranslationError("未知的翻译源")

        # 如实告诉调用方这一条译文是不是带语气档（界面据此决定是否置灰语气按钮）
        result.tone = tone
        result.tone_capable = provider_id in self.TONE_PROVIDERS
        return result

    # ------------------------------------------------------------------ 必应
    def _bing_session(self, *, force: bool = False) -> dict[str, Any]:
        """取必应翻译页面的 IG / IID / 防滥用 token，一小时有效。"""
        now = time.time()
        session = self._bing
        if session and not force and now - session["fetched_at"] < session["ttl"]:
            return session

        last_error: Exception | None = None
        for site in BING_SITES:
            try:
                resp = self.http.request(
                    site,
                    timeout=PROVIDER_TIMEOUT,
                    retries=0,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "Referer": site,
                    },
                )
            except NetworkError as exc:
                last_error = exc
                continue
            html = resp.text or ""
            ig = re.search(r'IG:"([^"]+)"', html)
            iid = re.search(r'data-iid="([^"]+)"', html)
            params = re.search(r"params_AbusePreventionHelper\s?=\s?(\[[^\]]+\])", html)
            if not (ig and iid and params):
                last_error = TranslationError("必应翻译页面结构已变化，暂时取不到授权字段")
                continue
            try:
                key, token, ttl_ms = json.loads(params.group(1))[:3]
            except (ValueError, TypeError):
                last_error = TranslationError("必应翻译的授权字段解析失败")
                continue
            host = urllib.parse.urlsplit(resp.url).netloc or "cn.bing.com"
            session = {
                "host": host,
                "ig": ig.group(1),
                "iid": iid.group(1),
                "key": str(key),
                "token": str(token),
                "fetched_at": now,
                # 页面给的是毫秒有效期（默认 1 小时），留 2 分钟余量
                "ttl": max(60.0, float(ttl_ms or 3600000) / 1000.0 - 120.0),
            }
            self._bing = session
            return session

        if isinstance(last_error, TranslationError):
            raise last_error
        detail = getattr(last_error, "message", str(last_error or "网络不可达"))
        raise TranslationError(f"必应翻译暂时不可用（{detail}）")

    def _bing_translate(self, text: str, target: str, tone: str = "default") -> TranslationResult:
        started = time.time()
        # 必应中文只有简体/繁体两档，zh-CN / zh 都落到 zh-Hans
        lang = "zh-Hans" if target.lower().startswith("zh") else target
        parts: list[str] = []
        for chunk in _chunks(text, BING_CHUNK):
            parts.append(self._bing_chunk(chunk, lang, tone))
        note = ""
        if tone in BING_TONES:
            note = f"必应用「{TONES[tone]}」语气重新生成；同句切回「自动」会命中缓存，不会重复请求。"
        return TranslationResult(
            text="".join(parts),
            provider="bing",
            source_label=self.LABELS["bing"][0],
            source_url=self.LABELS["bing"][1],
            note=note,
            elapsed_ms=int((time.time() - started) * 1000),
        )

    def _bing_chunk(self, text: str, lang: str, tone: str, *, retry: bool = True) -> str:
        session = self._bing_session()
        fields = {
            "fromLang": "en",
            "text": text,
            "to": lang,
            "token": session["token"],
            "key": session["key"],
        }
        tone_value = BING_TONES.get(tone)
        if tone_value:
            fields["tone"] = tone_value
        url = BING_API.format(host=session["host"], ig=session["ig"], iid=session["iid"])
        resp = self.http.post_form(
            url,
            fields,
            timeout=PROVIDER_TIMEOUT,
            retries=0,
            headers={
                "Referer": f"https://{session['host']}/translator",
                "Origin": f"https://{session['host']}",
                "Accept": "*/*",
            },
        )
        raw = (resp.text or "").strip()
        # 页面 token 过期时必应会回空响应或 {"statusCode":205}
        expired = not raw
        data: Any = None
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                expired = True
        if not expired and isinstance(data, dict):
            expired = int(data.get("statusCode") or 0) == 205
        if expired:
            if not retry:
                raise TranslationError("必应翻译的会话已失效，请稍后重试")
            self._bing_session(force=True)
            return self._bing_chunk(text, lang, tone, retry=False)
        if isinstance(data, dict):
            code = data.get("statusCode")
            raise TranslationError(f"必应翻译返回 {code or '错误'}")
        try:
            translated = str(data[0]["translations"][0]["text"]).strip()
        except (TypeError, IndexError, KeyError):
            raise TranslationError("必应翻译返回结构异常") from None
        if not translated:
            raise TranslationError("必应翻译返回空译文")
        return translated

    # ------------------------------------------------------------------ 备用
    def _mymemory(self, text: str, target: str) -> TranslationResult:
        started = time.time()
        email = self.config.get("translator", "mymemory_email", default="") or ""
        parts: list[str] = []
        alternatives: list[str] = []
        for chunk in _chunks(text):
            params = {"q": chunk, "langpair": f"en|{target}"}
            if email:
                params["de"] = email
            url = f"{MYMEMORY}?{urllib.parse.urlencode(params)}"
            data = self.http.get_json(url, timeout=PROVIDER_TIMEOUT, retries=0)
            status = data.get("responseStatus")
            translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            if str(status) != "200" and not translated:
                raise TranslationError(
                    (data.get("responseDetails") or f"MyMemory 返回 {status}")[:120],
                    provider="mymemory",
                )
            if "MYMEMORY WARNING" in translated.upper():
                raise TranslationError("MyMemory 免费额度已用完，请明天再试或改用其他源")
            if not translated:
                raise TranslationError("MyMemory 返回空译文")
            parts.append(translated)
            for match in data.get("matches", []) or []:
                candidate = (match.get("translation") or "").strip()
                quality = float(match.get("quality") or 0) / 100
                if (
                    candidate
                    and candidate != translated
                    and quality >= 0.6
                    and candidate not in alternatives
                    and len(candidate) < 400
                ):
                    alternatives.append(candidate)
        return TranslationResult(
            text="".join(parts),
            provider="mymemory",
            source_label=self.LABELS["mymemory"][0],
            source_url=self.LABELS["mymemory"][1],
            alternatives=alternatives[:3],
            elapsed_ms=int((time.time() - started) * 1000),
        )

    def _google(self, text: str, target: str) -> TranslationResult:
        started = time.time()
        parts: list[str] = []
        lang = "zh-CN" if target.lower().startswith("zh") else target
        for chunk in _chunks(text, 1800):
            params = {
                "client": "gtx",
                "sl": "en",
                "tl": lang,
                "dt": "t",
                "q": chunk,
            }
            url = f"{GOOGLE}?{urllib.parse.urlencode(params)}"
            data = self.http.get_json(url, timeout=PROVIDER_TIMEOUT, retries=0)
            try:
                translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
            except (TypeError, IndexError, KeyError) as exc:
                raise TranslationError("Google 翻译返回格式异常") from exc
            if not translated.strip():
                raise TranslationError("Google 翻译返回空译文")
            parts.append(translated)
        return TranslationResult(
            text="".join(parts),
            provider="google",
            source_label=self.LABELS["google"][0],
            source_url=self.LABELS["google"][1],
            elapsed_ms=int((time.time() - started) * 1000),
        )

    def _libre(self, text: str, target: str) -> TranslationResult:
        started = time.time()
        base = (self.config.get("translator", "libre_url", default="") or "").rstrip("/")
        if not base:
            raise TranslationError("未配置 LibreTranslate 地址")
        key = self.config.get("translator", "libre_key", default="") or ""
        payload = {
            "q": text,
            "source": "en",
            "target": "zh" if target.lower().startswith("zh") else target,
            "format": "text",
        }
        if key:
            payload["api_key"] = key
        data = self.http.post_json(f"{base}/translate", payload, timeout=PROVIDER_TIMEOUT, retries=0)
        translated = (data or {}).get("translatedText", "").strip()
        if not translated:
            raise TranslationError("LibreTranslate 返回空译文")
        return TranslationResult(
            text=translated,
            provider="libre",
            source_label=self.LABELS["libre"][0],
            source_url=base,
            elapsed_ms=int((time.time() - started) * 1000),
        )
