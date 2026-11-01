"""文本处理：分句、分词、段落清洗、难度估计。

分句要经得起外刊的真实文本：缩写（Dr. / U.S. / e.g.）、小数（3.5）、
引号收尾（….”）、省略号都要照顾，否则句子会被切碎，逐句译文就对不上。
"""

from __future__ import annotations

import re
import unicodedata

ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
    "fig", "figs", "no", "nos", "vol", "pp", "al", "inc", "ltd", "co", "corp",
    "dept", "univ", "est", "approx", "ca", "cf", "ed", "eds", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "mon", "tue",
    "wed", "thu", "fri", "sat", "sun", "gen", "gov", "sen", "rep", "col", "lt",
    "cmdr", "sgt", "capt", "mt", "ft", "hr", "min", "sec", "yr", "u.s", "u.k",
    "a.m", "p.m", "b.c", "a.d", "ph.d", "m.d", "d.c", "e.u", "u.n",
}

TERMINATORS = ".!?…"
CLOSERS = "\"'”’)]}»"

WORD_RE = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)*(?:-[^\W\d_]+)*", re.UNICODE)

BOILERPLATE_PATTERNS = [
    r"^\s*subscribe\b",
    r"\bsubscribe (now|today|to)\b",
    r"\bsign up for\b",
    r"\bnewsletter\b",
    r"\bcookie(s)? (policy|settings|preferences)\b",
    r"\ball rights reserved\b",
    r"\badvertisement\b",
    r"^\s*advertisement\s*$",
    r"\bfollow us\b",
    r"\bshare this\b",
    r"^\s*read more\b",
    r"\bclick here\b",
    r"\bdownload (the|our) app\b",
    r"\bsupport our journalism\b",
    r"\bbecome a (member|subscriber)\b",
    r"\bwe summarize the week\b",
    r"\bget the latest\b",
    r"\bthis (article|story|piece) (was|has been) (originally )?(published|updated)\b",
    r"\bimage (credit|source)s?\b",
    r"\bphoto(graph)? (by|credit)\b",
    r"^\s*(photograph|photo|image|illustration|video|credit)s?\s*:",
    r"\bskip to (main )?content\b",
    r"\benjoy(ing)? (this|the) (article|story)\b",
    r"\bwant to read more\b",
    r"\bavailable (only )?(to|for) subscribers\b",
    r"©\s*\d{4}",
    r"^\s*(by|words by)\s+[A-Z][a-z]+ [A-Z][a-z]+\s*$",
    r"create a reading list",
    r"click to tell google",
    r"^\s*listen to this article",
    r"^\s*this is today's edition",
    r"^\s*plus:",
    r"\bhide caption\b",
    r"\bis (an?|the) (senior |independent |freelance |staff )?(journalist|reporter|editor|writer|correspondent)\b",
    r"\bcontributed to this (report|article|story)\b",
    r"\bresearch assistance\b",
    r"^\s*[A-Z][a-z]+ [A-Z][a-z]+ is an? \w+",
]


def normalize_space(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    return re.sub(r"\s+", " ", text).strip()


def clean_paragraph(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"-\s+(?=[a-z])", "-", text)  # 保留连字符词
    text = re.sub(r"\[\d{1,3}\]", "", text)  # 角标引用
    text = re.sub(r"\[(citation needed|note \d+)\]", "", text, flags=re.I)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = normalize_space(text)
    return text.strip(" |•-–—")


def strip_tags(fragment: str) -> str:
    import html as _html

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return normalize_space(_html.unescape(text))


def is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    if len(text) < 40:
        return True
    for pattern in BOILERPLATE_PATTERNS:
        if re.search(pattern, lowered):
            return True
    letters = sum(ch.isalpha() for ch in text)
    if letters < 40:
        return True
    if letters / max(1, len(text)) < 0.55:
        return True
    # 全大写标题行
    if text.isupper():
        return True
    return False


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """返回句子的 (start, end) 偏移，不含首尾空白。"""
    text = text or ""
    spans: list[tuple[int, int]] = []
    length = len(text)
    start = 0
    i = 0
    while i < length:
        ch = text[i]
        if ch not in TERMINATORS:
            i += 1
            continue
        j = i + 1
        # 连续的终止符：?!、...、……
        while j < length and text[j] in TERMINATORS:
            j += 1
        # 收尾引号/括号
        while j < length and text[j] in CLOSERS:
            j += 1
        boundary = False
        if j >= length:
            boundary = True
        elif text[j].isspace():
            k = j
            while k < length and text[k].isspace():
                k += 1
            if k >= length:
                boundary = True
            else:
                nxt = text[k]
                prev_word = _word_before(text, i)
                if ch == "." and _is_abbreviation(prev_word, text, i):
                    boundary = False
                elif ch == "." and i > 0 and text[i - 1].isdigit() and nxt.isdigit():
                    boundary = False
                elif nxt.islower():
                    boundary = False
                elif nxt.isalpha() or nxt.isdigit() or nxt in CLOSERS or nxt in "([{":
                    boundary = True
                else:
                    boundary = True
        if boundary:
            piece = text[start:j].strip()
            if piece:
                left = start + (len(text[start:j]) - len(text[start:j].lstrip()))
                spans.append((left, left + len(piece)))
            start = j
            i = j
        else:
            i += 1
    tail = text[start:].strip()
    if tail:
        left = start + (len(text[start:]) - len(text[start:].lstrip()))
        spans.append((left, left + len(tail)))
    return spans


def _word_before(text: str, index: int) -> str:
    end = index
    while end > 0 and (text[end - 1].isalpha() or text[end - 1] == "."):
        end -= 1
    return text[end:index]


def _is_abbreviation(word: str, text: str, index: int) -> bool:
    if not word:
        return False
    bare = word.rstrip(".").lower()
    if bare in ABBREVIATIONS:
        return True
    if len(bare) == 1 and bare.isalpha():
        return True  # 人名首字母 J. K. Rowling
    if re.fullmatch(r"([a-z]\.){2,}", word.lower()):
        # U.S. 形式，且下一句以小写开头的情况已在外部处理
        return True
    return False


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    return [text[a:b].strip() for a, b in sentence_spans(text) if text[a:b].strip()]


def paragraph_starts(text: str, sentences: list[str] | None = None) -> list[int]:
    """每段第一句在 sentences 里的下标，供阅读页分段换行。

    只认「空行分段」：文本里没有空行、或者按段分句的数量和整体分句对不上，
    就返回 [0]，宁可显示成一整段也不要把句子错位到别的段落里。
    """
    sentences = sentences if sentences is not None else split_sentences(text)
    if not sentences:
        return [0]
    chunks = [c for c in re.split(r"\n\s*\n+", (text or "").strip()) if c.strip()]
    if len(chunks) <= 1:
        return [0]
    starts: list[int] = []
    cursor = 0
    for chunk in chunks:
        starts.append(cursor)
        cursor += len(split_sentences(chunk))
    if cursor != len(sentences):
        return [0]
    return starts


def split_long_paragraph(text: str, max_chars: int = 720) -> list[str]:
    """把过长的段落按句子边界切成若干块，供逐段练习。"""
    text = normalize_space(text)
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = split_sentences(text)
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        if buf and len(buf) + 1 + len(sentence) > max_chars:
            chunks.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def words_in(text: str) -> list[tuple[str, int, int]]:
    """返回 (词, 起, 止)，仅英文字母词（含撇号/连字符）。"""
    return [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(text or "")]


def normalize_word(word: str) -> str:
    # 句末标点常跟着词一起被选中，查词前统一剥掉
    word = word.strip(CLOSERS + "([{«,.;:!?…—-")
    word = word.replace("’", "'").replace("‘", "'")
    word = re.sub(r"'s$", "", word, flags=re.I)
    word = re.sub(r"'$", "", word)
    return word.lower()


def word_count(text: str) -> int:
    return len(words_in(text))


def avg_sentence_words(text: str) -> float:
    sentences = split_sentences(text)
    if not sentences:
        return 0.0
    return round(word_count(text) / len(sentences), 1)


def level_estimate(text: str) -> tuple[str, str]:
    """粗略 CEFR 估计，返回 (等级, 说明)。只做阅读难度提示，不作评分。"""
    sentences = split_sentences(text)
    if not sentences:
        return ("—", "暂无数据")
    avg = word_count(text) / len(sentences)
    if avg <= 12:
        level = "A2"
    elif avg <= 18:
        level = "B1"
    elif avg <= 26:
        level = "B2"
    elif avg <= 34:
        level = "C1"
    else:
        level = "C2"
    return (level, f"平均句长 {avg:.0f} 词")


def practice_score(text: str) -> float:
    """段落适合练习的程度：长度适中、句子数 2-6 最佳。"""
    length = len(text)
    if length < 90 or length > 1100:
        return -100.0
    sentences = split_sentences(text)
    if not sentences:
        return -100.0
    score = 0.0
    if 220 <= length <= 760:
        score += 3.0
    if 2 <= len(sentences) <= 6:
        score += 2.0
    elif len(sentences) > 8:
        score -= 2.0
    avg = word_count(text) / len(sentences)
    if 10 <= avg <= 30:
        score += 1.0
    if is_boilerplate(text):
        score -= 50.0
    return score


def title_case_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKC", text).lower()).strip()
