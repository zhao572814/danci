"""全量词库发音生成（开发期一次性运行，产物打包进 APK）。

从 assets/audio/words_all.txt 读取全部 17634 词，用 edge-tts 生成 mp3。
特点：
  * 断点续传：已存在且 >1000 字节的文件自动跳过，中断后重跑不重复
  * 限流重试：edge-tts 偶发 429 / 连接错误，指数退避重试
  * 分片并发：并发 6，约 60-90 分钟跑完全量
  * 实时进度：每 200 词打印一次进度，写 progress.txt 方便查看

用法（本机需联网一次）：
    python tools/gen_audio_full.py

产物：assets/audio/<md5(单词)>.mp3 + assets/audio/index.json
"""
import asyncio
import hashlib
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
AUDIO_DIR = os.path.join(ASSETS, "audio")
WORDS_TXT = os.path.join(AUDIO_DIR, "words_all.txt")
INDEX_JSON = os.path.join(AUDIO_DIR, "index.json")
PROGRESS_TXT = os.path.join(AUDIO_DIR, "progress.txt")
VOICE = "en-US-AriaNeural"
CONCURRENCY = 6


def key_of(word: str) -> str:
    return hashlib.md5((word or "").strip().lower().encode("utf-8")).hexdigest()


def load_words() -> list[str]:
    with open(WORDS_TXT, encoding="utf-8") as f:
        words = [w.strip().lower() for w in f if w.strip()]
    # 去重保序
    return sorted(set(words))


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
            # 429/限流 -> 退避更久
            delay = min(2 ** attempt * 1.5, 20)
            await asyncio.sleep(delay)
    return word, False, last


async def main():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    words = load_words()
    total = len(words)
    print(f"全量词表 {total} 词 -> {AUDIO_DIR}", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [gen_one(sem, w) for w in words]

    done = ok = fail = skip = 0
    fails: list[tuple[str, str]] = []
    t0 = time.time()
    with open(PROGRESS_TXT, "w", encoding="utf-8") as pf:
        pf.write(f"总数 {total}\n")
        for coro in asyncio.as_completed(tasks):
            r = await coro
            if isinstance(r, Exception):
                fail += 1
                fails.append(("?", str(r)))
            else:
                w, success, how = r
                if not success:
                    fail += 1
                    fails.append((w, how))
                elif how == "skip":
                    skip += 1
                else:
                    ok += 1
            done += 1
            if done % 200 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate if rate else 0
                line = (f"{done}/{total} 新增{ok} 跳过{skip} 失败{fail} "
                        f"用时{elapsed/60:.1f}分 预计剩余{eta/60:.1f}分")
                print(line, flush=True)
                pf.write(line + "\n")
                pf.flush()

    # 写 index.json：只收录真正生成成功的词
    index = {}
    for w in words:
        p = os.path.join(AUDIO_DIR, key_of(w) + ".mp3")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            index[key_of(w)] = w
    with open(INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    total_mb = sum(
        os.path.getsize(os.path.join(AUDIO_DIR, f))
        for f in os.listdir(AUDIO_DIR) if f.endswith(".mp3")
    ) / 1024 / 1024
    summary = (f"完成：新增{ok} 跳过{skip} 失败{fail}；"
               f"index共{len(index)}条；音频合计{total_mb:.1f}MB；"
               f"总用时{(time.time()-t0)/60:.1f}分")
    print(summary, flush=True)
    if fails:
        print("失败样例（前20）:", flush=True)
        for w, why in fails[:20]:
            print("  ", w, "->", why, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
