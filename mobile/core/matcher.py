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

from typing import Callable, Iterable

from .db import clean_headword
from .extract import STOPWORDS, WORD_RE, normalize_word

MAX_PHRASE_WORDS = 7


def detect_items(
    text: str,
    phrase_headwords: Iterable[str],
    filter_stopwords: bool = True,
    detect_phrases: bool = True,
    min_len: int = 2,
) -> list[dict]:
    """返回按出现顺序、去重的候选条目：[{'headword':..., 'kind':'word'|'phrase'}]。"""
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
