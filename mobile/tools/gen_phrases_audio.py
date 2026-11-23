"""补生成 194 个内置短语的发音，并入 index.json（全量词生成后运行）。

短语如 'take part in' / 'look forward to' 不在单词词表里，
单独补一次，避免短语点朗读失败。已存在的文件自动跳过。
"""
import asyncio
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
AUDIO_DIR = os.path.join(ASSETS, "audio")
INDEX_JSON = os.path.join(AUDIO_DIR, "index.json")
VOICE = "en-US-AriaNeural"


def key_of(word: str) -> str:
    return hashlib.md5((word or "").strip().lower().encode("utf-8")).hexdigest()


async def gen_one(sem, phrase: str):
    import edge_tts
    path = os.path.join(AUDIO_DIR, key_of(phrase) + ".mp3")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return phrase, True, "skip"
    for attempt in range(5):
        try:
            async with sem:
                await edge_tts.Communicate(phrase, VOICE).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return phrase, True, "ok"
        except Exception:
            await asyncio.sleep(min(2 ** attempt * 1.5, 20))
    return phrase, False, "fail"


async def main():
    with open(os.path.join(ASSETS, "builtin_phrases.json"), encoding="utf-8") as f:
        phrases = [k.strip() for k in json.load(f) if k.strip()]
    sem = asyncio.Semaphore(6)
    results = await asyncio.gather(*[gen_one(sem, p) for p in phrases])
    # gen_one 返回 (phrase, success_bool, how)
    ok = sum(1 for _, success, how in results if success and how == "ok")
    skip = sum(1 for _, success, how in results if success and how == "skip")
    fail = [(p, how) for p, success, how in results if not success]
    print(f"短语发音：新增{ok} 跳过{skip} 失败{len(fail)}")
    for p, h in fail[:20]:
        print("  FAIL", p, h)

    # 重建 index.json：现有全部 mp3（含全量单词 + 短语）
    index = {}
    for fn in os.listdir(AUDIO_DIR):
        if not fn.endswith(".mp3"):
            continue
        k = fn[:-4]
        # 从已有 index 或词表反查原词；短语直接在 phrases 里找
        if os.path.getsize(os.path.join(AUDIO_DIR, fn)) <= 1000:
            continue
        index[k] = k  # 占位，下面用映射覆盖成原词
    # 用 index.json 旧内容 + 词表 + 短语建立 key->原词映射
    mapping = {}
    old = {}
    if os.path.exists(INDEX_JSON):
        with open(INDEX_JSON, encoding="utf-8") as f:
            old = json.load(f)
    mapping.update(old)
    words = [w.strip() for w in open(os.path.join(AUDIO_DIR, "words_all.txt"), encoding="utf-8") if w.strip()]
    for w in words:
        mapping[key_of(w)] = w
    for p in phrases:
        mapping[key_of(p)] = p
    final = {k: v for k, v in mapping.items() if k in index}
    with open(INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)
    print(f"index.json 合并完成：共 {len(final)} 条")


if __name__ == "__main__":
    asyncio.run(main())
