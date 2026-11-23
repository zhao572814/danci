"""把用户的完整词库 TXT 转成 App 内置词库 JSON（开发期一次性运行）。

输入 TXT 格式（UTF-8，Tab 分隔三列）：
    单词<TAB>音标<TAB>中文释义
输出：
    assets/builtin_dict.json   -> {单词: {phonetic, translation, example}}
    assets/audio/words_all.txt -> 去重词表，供 gen_audio.py 生成全量发音

重复词（多义词分多行）的释义会合并；音标统一成 /.../ 便于展示。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
AUDIO_DIR = os.path.join(ASSETS, "audio")


def normalize_phonetic(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return ""
    # 统一成 /.../；原本就是 /.../ 的不动，[...] 的换掉方括号
    if p.startswith("[") and p.endswith("]"):
        p = "/" + p[1:-1].strip() + "/"
    return p


def convert(src: str) -> int:
    with open(src, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()

    merged: dict[str, dict] = {}
    order: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        headword = parts[0].strip()
        if not headword:
            continue
        phonetic = normalize_phonetic(parts[1])
        # 释义可能含 tab，取第三列之后全部拼回
        translation = "\t".join(parts[2:]).strip().strip('"')
        key = headword.lower()
        if key not in merged:
            merged[key] = {
                "word": headword,
                "phonetic": phonetic,
                "translations": [],
            }
            order.append(key)
        if phonetic and not merged[key]["phonetic"]:
            merged[key]["phonetic"] = phonetic
        if translation and translation not in merged[key]["translations"]:
            merged[key]["translations"].append(translation)

    out_dict: dict[str, dict] = {}
    for key in order:
        item = merged[key]
        out_dict[item["word"]] = {
            "phonetic": item["phonetic"],
            "translation": "；".join(item["translations"]),
            "example": "",
        }

    os.makedirs(AUDIO_DIR, exist_ok=True)
    with open(os.path.join(ASSETS, "builtin_dict.json"), "w", encoding="utf-8") as f:
        json.dump(out_dict, f, ensure_ascii=False, indent=1)
    # 词表：用小写 key，和 audio.key_of / matcher 保持一致
    words = sorted({k.lower() for k in merged})
    with open(os.path.join(AUDIO_DIR, "words_all.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(words))

    print(f"词库转换完成：{len(lines)} 行 -> {len(out_dict)} 个词条（已合并重复词）")
    print(f"写入 {os.path.join(ASSETS, 'builtin_dict.json')}")
    print(f"词表 {os.path.join(AUDIO_DIR, 'words_all.txt')} 共 {len(words)} 词")
    return len(out_dict)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src or not os.path.exists(src):
        print("用法: python build_full_dict.py <词汇TXT路径>")
        sys.exit(1)
    convert(src)
