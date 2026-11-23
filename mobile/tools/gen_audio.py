"""预生成内置词库发音音频（开发期一次性运行，产物打包进 APK）。

用 edge-tts（微软免费神经网络语音，质量远好于设备系统 TTS）生成
内置单词/短语的 mp3。用户手机端只播放这些本地文件，完全不依赖
系统 TextToSpeech —— 彻底绕开鸿蒙卓易通没有语音引擎的问题。

用法（在本机，需联网一次）：
    python tools/gen_audio.py

产物：assets/audio/<md5(单词)>.mp3 + assets/audio/index.json
"""
import asyncio
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
AUDIO_DIR = os.path.join(ASSETS, "audio")
VOICE = "en-US-AriaNeural"


def key_of(word: str) -> str:
    return hashlib.md5(word.strip().lower().encode("utf-8")).hexdigest()


def load_words():
    words = set()
    for name in ("builtin_dict.json", "builtin_phrases.json"):
        p = os.path.join(ASSETS, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        for k in data:
            k = k.strip()
            if k and not any(ch in k for ch in "\r\n\t"):
                words.add(k)
    return sorted(words)


async def gen_one(sem, word):
    import edge_tts
    path = os.path.join(AUDIO_DIR, key_of(word) + ".mp3")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return word, True, "skip"
    for attempt in range(3):
        try:
            async with sem:
                await edge_tts.Communicate(word, VOICE).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                return word, True, "ok"
        except Exception as e:
            if attempt == 2:
                return word, False, f"{type(e).__name__}: {e}"
            await asyncio.sleep(1.5 * (attempt + 1))
    return word, False, "empty"


async def main():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    words = load_words()
    print(f"共 {len(words)} 个词条待生成 → {AUDIO_DIR}")
    sem = asyncio.Semaphore(6)
    tasks = [gen_one(sem, w) for w in words]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = fail = skip = 0
    for r in results:
        if isinstance(r, Exception):
            fail += 1
            continue
        _, success, how = r
        if not success:
            fail += 1
            print("FAIL", r[0], how)
        elif how == "skip":
            skip += 1
        else:
            ok += 1
    index = {key_of(w): w for w in words
             if os.path.exists(os.path.join(AUDIO_DIR, key_of(w) + ".mp3"))}
    with open(os.path.join(AUDIO_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    total = sum(os.path.getsize(os.path.join(AUDIO_DIR, f))
                for f in os.listdir(AUDIO_DIR) if f.endswith(".mp3"))
    print(f"生成完成：新增 {ok}，跳过 {skip}，失败 {fail}；"
          f"共 {len(index)} 条，合计 {total/1024/1024:.2f} MB")


if __name__ == "__main__":
    asyncio.run(main())
