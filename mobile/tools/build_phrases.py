"""构建内置短语库：常用短语清单（junior/senior/cet4/cet6）+ ECDICT 中文释义。

选词策略（重要）：
  * 短语"名单"来自 2ndLA/english-phrases 的分级表（初中/高中/四级/六级，共约
    6000 条），这些是教材和考试真正会考的常用短语——不用 ECDICT 的词频排序，
    因为 ECDICT 里词频字段大量为 0，排序会退化成字母序，选出冷僻专业词。
  * 中文释义 + 音标从 ECDICT 查（有释义才收；查不到释义的用清单自带的英文，
    但优先保证有中文，背单词必须有意思）。

产物 assets/builtin_phrases.json 随 APK 打包；已人工校对的 194 条原样保留在最前。
全程本地，只在这台构建机跑一次。
"""
from __future__ import annotations

import csv
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT_SRC = os.path.join(HERE, "dict_src")
CSV = os.path.join(DICT_SRC, "ecdict.csv")
OUT = os.path.join(HERE, "assets", "builtin_phrases.json")
LIST_FILES = ("ph_junior.txt", "ph_senior.txt", "ph_cet4.txt", "ph_cet6.txt")

IPA_CHARS = re.compile(r"[ɑəɪʊæʌɔɜðθʃʒŋɒɛɝˌˋː]")


def clean_translation(raw: str) -> str:
    if not raw:
        return ""
    lines = [l.strip() for l in raw.replace("\\n", "\n").splitlines() if l.strip()]
    keep = []
    for l in lines:
        if l.startswith("[网络]"):
            continue
        keep.append(l)
        if len(keep) >= 2:
            break
    return "；".join(keep)[:100]


def load_ecdict() -> dict[str, tuple[str, str]]:
    """返回 {小写词条: (音标, 中文释义)}，只保留含空格的短语。"""
    out: dict[str, tuple[str, str]] = {}
    if not os.path.exists(CSV):
        print("警告：词典文件不存在，将只用清单（无释义）")
        return out
    with open(CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            w = (r.get("word") or "").strip()
            if " " not in w or len(w.split()) > 8:
                continue
            trans = clean_translation(r.get("translation") or "")
            if not trans:
                continue
            ph = (r.get("phonetic") or "").strip()
            out[w.lower()] = (ph, trans)
    return out


def main():
    # 1) 常用短语清单（有序：初中→高中→四级→六级）
    phrases: list[str] = []
    for fn in LIST_FILES:
        p = os.path.join(DICT_SRC, fn)
        if not os.path.exists(p):
            print("缺少", fn)
            continue
        for line in open(p, encoding="utf-8"):
            w = line.strip().lower()
            if w and " " in w and w not in phrases:
                phrases.append(w)
    print(f"常用短语清单：{len(phrases)} 条")

    # 2) 从 ECDICT 补释义
    ecdict = load_ecdict()
    print(f"ECDICT 带释义短语：{len(ecdict)} 条")

    # 3) 原有 194 条人工校对短语原样保留
    merged: dict[str, dict] = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            merged = json.load(f)

    added = 0
    no_trans = 0
    for w in phrases:
        if w in merged:
            continue
        ph, trans = ecdict.get(w, ("", ""))
        if not trans:
            no_trans += 1
        phon = "/" + ph + "/" if ph and IPA_CHARS.search(ph) else ""
        merged[w] = {"phonetic": phon, "translation": trans, "example": ""}
        added += 1

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    with_trans = sum(1 for v in merged.values() if v.get("translation"))
    print(f"新增 {added} 条；短语库合计 {len(merged)} 条，其中有中文释义 {with_trans} 条")
    print(f"清单里 ECDICT 查不到释义的：{no_trans} 条（这些只存英文，朗读不受影响）")
    for k in list(merged)[:2] + list(merged)[-3:]:
        print("  ", k, "->", merged[k]["translation"][:45])


if __name__ == "__main__":
    main()
