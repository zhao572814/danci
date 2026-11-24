"""批量补发音：从用户导出的「缺发音词清单」生成 mp3，合并进现有 index.json。

用法（电脑上联网运行，本机一次性）：
    python tools/gen_audio_add.py 缺词清单.txt

清单文件格式：每行一个词/短语（就是 App 里点「缺发音词清单→全选复制」发来的内容）。
行为：
  * 只生成还没有音频的词；已有的自动跳过，绝不覆盖
  * 新词追加进 index.json 的映射，保留原有全部条目
  * 断点续传、限流重试，中断后重跑不重复
产物：assets/audio/<md5>.mp3 + 更新后的 index.json（提交后重新编译 APK 即可）
"""
import asyncio
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
AUDIO_DIR = os.path.join(ASSETS, "audio")
INDEX_JSON = os.path.join(AUDIO_DIR, "index.json")
VOICE = "en-US-AriaNeural"
CONCURRENCY = 6


def key_of(word: str) -> str:
    return hashlib.md5((word or "").strip().lower().encode("utf-8")).hexdigest()


def load_words(path: str) -> list[str]:
    words = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            w = line.strip()
            # 忽略提示文案行
            if not w or w.startswith("（") or "还没有发音" in w:
                continue
            words.append(w)
    # 去重保序
    seen = set()
    out = []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            out.append(w)
    return out


async def gen_one(sem, word: str) -> tuple[str, bool, str]:
    import edge_tts
    path = os.path.join(AUDIO_DIR, key_of(word) + ".mp3")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return word, True, "skip"
    last = ""
    for attempt in range(5):
        try:
            async with sem:
                await edge_tts.Communicate(word, VOICE).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return word, True, "ok"
            last = "empty file"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(min(2 ** attempt * 1.5, 20))
    return word, False, last


async def main():
    if len(sys.argv) < 2:
        print("用法: python tools/gen_audio_add.py 缺词清单.txt")
        sys.exit(1)
    words = load_words(sys.argv[1])
    if not words:
        print("清单里没有有效词条")
        sys.exit(1)

    with open(INDEX_JSON, encoding="utf-8") as f:
        index = json.load(f)

    todo = [w for w in words if key_of(w) not in index]
    print(f"清单 {len(words)} 词，其中 {len(todo)} 个需新生成，其余已有跳过", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    ok = fail = 0
    fails = []
    t0 = time.time()
    for i, coro in enumerate(asyncio.as_completed([gen_one(sem, w) for w in todo]), 1):
        w, success, how = await coro
        if success:
            ok += 1
        else:
            fail += 1
            fails.append((w, how))
        if i % 50 == 0 or i == len(todo):
            print(f"{i}/{len(todo)} 成功{ok} 失败{fail} 用时{(time.time()-t0)/60:.1f}分", flush=True)

    # 合并进 index：为每个成功生成的词写入「原词」映射
    added = 0
    for w in words:
        k = key_of(w)
        p = os.path.join(AUDIO_DIR, k + ".mp3")
        if k not in index and os.path.exists(p) and os.path.getsize(p) > 1000:
            index[k] = w
            added += 1
    with open(INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    print(f"完成：新增音频{ok} 失败{fail}；index 新增{added}条，现共{len(index)}条", flush=True)
    if fails:
        print("失败样例（前20）:", flush=True)
        for w, why in fails[:20]:
            print("  ", w, "->", why, flush=True)
    print("下一步：把 assets/audio 下新增的 mp3 和 index.json 一并提交，重新编译 APK。", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
