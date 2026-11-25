"""短语识别：把识别出来的文本切成「基础词库里挑出来的条目」。

思路与用户的「先建库、再从库里挑词」完全一致：
* 基础词库里的短语词条（kind='phrase'）构成短语表；
* 文本先按词切分（天然跨行），对短语表做**最长匹配**，命中就把整段当一条短语；
* 被短语覆盖的单词不再单独成条，剩下的才按单词处理；
* 库里没有的短语不会凭空猜测（避免噪音），可以在导入页「合成短语」手动补录进基础库，
  之后同样的文本就能自动识别出来。

全程纯本地规则，不需要任何模型或联网。
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from .db import clean_headword
from .extract import STOPWORDS, WORD_RE, normalize_word

MAX_PHRASE_WORDS = 7

# 词性缩写（带点的写法），如 "adv." "n." "prep." —— 只在"词表行"里剔除，
# 它们是释义的一部分，不是词条本身。
_POS_RE = re.compile(
    r"\b(?:n|v|vt|vi|adj|adv|prep|conj|pron|art|int|num|aux|phr|abbr|pl|sing)\.\s*",
    re.I,
)
# 音标块：/.../（含 IPA 字符），必须整块剔除，否则里面的拉丁字母会被
# WORD_RE 当成单词（例如 /ˈkeəflɪ/ → ke fli），把单词和音标拼成假短语。
_IPA_SLASH_RE = re.compile(r"/[^/\n]{1,48}/")
_IPA_BRACKET_RE = re.compile(r"\[[^\[\]\n]{1,48}\]")
# 行首序号：1. / 1、/ (1) / ① / 一、
_ORDINAL_RE = re.compile(r"^\s*(?:\(?\d{1,3}\)?[\.、\)]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+[、\.])\s*")
# 中文/日文字符与中文标点（词表里的释义部分）
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+")


def _parse_entry_line(line: str) -> str | None:
    """把词表里的一行解析成纯英文词条；不是有效词条行返回 None。

    规则（严格按用户要求）：
      * 一行只出一条：整行的英文词序列就是这一条（1 个词=单词，多个=短语）；
      * 音标 /.../ [...]、词性缩写 adv. n.、中文释义 全部剔除，不进词条；
      * 因为先剔音标，绝不会把 "carefully /ˈkeəflɪ/ adv. 认真地" 拼成
        "carefully ke fli adv" 这种假短语；
      * 中文在上、英文在下的排版：纯英文行正常解析，纯中文行返回 None。
    """
    s = _ORDINAL_RE.sub("", line or "")
    if not s.strip():
        return None
    # 1) 整块剔除音标（必须先做，否则音标里的拉丁字母会混进词条）
    s = _IPA_SLASH_RE.sub(" ", s)
    s = _IPA_BRACKET_RE.sub(" ", s)
    # 2) 剔除词性缩写与中文释义
    s = _POS_RE.sub(" ", s)
    parts = _CJK_RE.split(s)
    # 中文可能在前（"锻炼一小时 exercise for an hour"）或在后，
    # 也可能一行只有中文——把所有片段里的英文词都收集起来。
    eng = " ".join(m.group(0) for m in WORD_RE.finditer(" ".join(parts)))
    if not eng:
        return None
    return eng.strip()


def detect_table_items(text: str) -> list[dict] | None:
    """词表模式：文本像"每行一个词条"的单词/短语表时，按行整条识别。

    返回 None 表示"不是词表格式"，调用方应回退到文章模式（查词库挑词）。
    这样既能整条认出词库里没有的短语，也不会把普通文章误切成词条。
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    parsed = [_parse_entry_line(ln) for ln in lines]
    valid = [p for p in parsed if p]
    if len(valid) < 2:
        return None
    # 词表特征：能解析出英文的行里，绝大多数是"短词条"（1~7 词）。
    # 注意分母用 valid（有英文的行），不能用 lines——词表里常是"中文释义 +
    # 英文词条"各占一行，中文行解析不出英文，算进分母会把词表误判成文章。
    # 真正的文章每行是长句（>7 词），valid 里短行比例低，不会误判。
    short = sum(1 for p in valid if len(p.split()) <= MAX_PHRASE_WORDS)
    if short / len(valid) < 0.85:
        return None

    out: list[dict] = []
    seen: set[str] = set()
    for p in parsed:
        if not p:
            continue
        words = [normalize_word(m.group(0)) for m in WORD_RE.finditer(p)]
        words = [w for w in words if w]
        if not words or len(words) > MAX_PHRASE_WORDS:
            continue
        # 单词/短语判定：词数 >1 即短语；虚词开头的短语（in class / on TV）
        # 要整条保留，不能用 STOPWORDS 过滤掉 in/on。
        hw = clean_headword(" ".join(words))
        key = hw.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"headword": hw,
                    "kind": "phrase" if len(words) > 1 else "word"})
    return out or None


def detect_items(
    text: str,
    phrase_headwords: Iterable[str],
    filter_stopwords: bool = True,
    detect_phrases: bool = True,
    min_len: int = 2,
) -> list[dict]:
    """返回按出现顺序、去重的候选条目：[{'headword':..., 'kind':'word'|'phrase'}]。

    优先词表模式（每行一条，短语整条识别、单词不被音标污染）；
    不是词表时回退到文章模式（按词切分 + 词库短语最长匹配）。
    """
    table = detect_table_items(text)
    if table is not None:
        return table
    tokens = [normalize_word(m.group(0)) for m in WORD_RE.finditer(text or "")]
    n = len(tokens)

    phrase_map: dict[tuple[str, ...], str] = {}
    if detect_phrases:
        for p in phrase_headwords:
            parts = tuple(clean_headword(p).lower().split())
            if 2 <= len(parts) <= MAX_PHRASE_WORDS:
                phrase_map[parts] = clean_headword(p)
    max_n = min(MAX_PHRASE_WORDS, max((len(k) for k in phrase_map), default=1))

    covered = [False] * n
    phrases_at: dict[int, tuple[int, str]] = {}
    i = 0
    while i < n:
        hit = False
        if phrase_map:
            for size in range(min(max_n, n - i), 1, -1):
                key = tuple(t.lower() for t in tokens[i:i + size])
                if key in phrase_map:
                    phrases_at[i] = (i + size, phrase_map[key])
                    for j in range(i, i + size):
                        covered[j] = True
                    i += size
                    hit = True
                    break
        if not hit:
            i += 1

    out: list[dict] = []
    seen: set[str] = set()
    i = 0
    while i < n:
        if i in phrases_at:
            end, hw = phrases_at[i]
            key = hw.lower()
            if key not in seen:
                seen.add(key)
                out.append({"headword": hw, "kind": "phrase"})
            i = end
            continue
        if not covered[i]:
            w = tokens[i]
            key = w.lower()
            if len(w) >= min_len and not (filter_stopwords and key in STOPWORDS):
                if key not in seen:
                    seen.add(key)
                    out.append({"headword": clean_headword(w), "kind": "word"})
        i += 1
    return out


def annotate(items: list[dict], lookup: Callable[[str], dict | None]) -> list[dict]:
    """给候选条目标注基础库状态（不写库）。"""
    for it in items:
        entry = lookup(it["headword"])
        it["entry_id"] = entry["id"] if entry else None
        it["in_library"] = bool(entry and entry.get("source") != "pending")
        it["phonetic"] = (entry or {}).get("phonetic", "")
        it["translation"] = (entry or {}).get("translation", "")
        it["pending"] = bool(entry and entry.get("source") == "pending")
    return items


def join_phrase(words: list[str]) -> str:
    """把导入预览里选中的相邻单词合成一个短语文本。"""
    return clean_headword(" ".join(w.strip() for w in words if w.strip()))
