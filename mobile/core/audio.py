"""离线发音：播放 APK 内预生成的音频文件，完全不依赖系统 TTS。

背景：鸿蒙卓易通兼容层没有可用的系统 TextToSpeech（onInit 直接失败、
语音设置打不开、无障碍权限被拦），系统 TTS 这条路是死的。
方案：开发期用 edge-tts 把内置词库的发音生成 mp3 打包进 APK，
手机端只做"查表 → 播放本地文件"，纯本地、零系统接口、必定可用。

边界：只有预生成过的词（内置词库那批）能朗读。用户自己导入的自定义词
没有对应音频，会明确提示 —— 这是纯离线的固有边界，不假装能读。
"""
from __future__ import annotations

import hashlib
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(APP_DIR, "assets", "audio")

_INDEX = None
_SOUND = None          # 持有当前播放对象，防止被 GC 后停止
_LAST_ERR = ""


def key_of(word: str) -> str:
    return hashlib.md5((word or "").strip().lower().encode("utf-8")).hexdigest()


def _ensure_index():
    global _INDEX
    if _INDEX is not None:
        return
    _INDEX = {}
    try:
        p = os.path.join(AUDIO_DIR, "index.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                _INDEX = json.load(f)
    except Exception:
        _INDEX = {}


def has_audio(word: str) -> bool:
    """这个词是否有预生成发音。"""
    _ensure_index()
    k = key_of(word)
    if k in _INDEX:
        return os.path.exists(os.path.join(AUDIO_DIR, k + ".mp3"))
    # index 缺失时也直接按文件存在性判断（兼容旧打包）
    return os.path.exists(os.path.join(AUDIO_DIR, k + ".mp3"))


def count() -> int:
    _ensure_index()
    return len(_INDEX)


# ---------------------------------------------------------------- 缺发音词清单
# 用户补录的词、识别出的生词若没有内置发音，点朗读会失败。
# 这里把"想读但读不了"的词自动记到可写数据目录，供一键导出 → 电脑批量生成音频。
def _data_dir() -> str:
    return os.environ.get("VOCAB_DATA_DIR") or os.path.expanduser("~")


def _missing_path() -> str:
    return os.path.join(_data_dir(), "missing_audio.txt")


def record_missing(word: str) -> bool:
    """记录一个想读但没有内置发音的词（去重）。返回是否新记录。失败静默。"""
    w = (word or "").strip()
    if not w:
        return False
    try:
        p = _missing_path()
        existing = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                existing = [x.strip() for x in f.read().splitlines() if x.strip()]
        if any(x.lower() == w.lower() for x in existing):
            return False
        existing.append(w)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
        return True
    except Exception:
        return False


def missing_recorded() -> list[str]:
    """读取此前点朗读失败时自动记录过的词。"""
    try:
        p = _missing_path()
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [x.strip() for x in f.read().splitlines() if x.strip()]
    except Exception:
        return []


def clear_missing() -> None:
    """批量生成并装入新版后，清空缺词记录。"""
    try:
        p = _missing_path()
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def missing_from_list(words) -> list[str]:
    """从给定词表里筛出没有内置发音的词（纯内存比对，快）。"""
    _ensure_index()
    miss = []
    seen = set()
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        lw = w.lower()
        if lw in seen:
            continue
        seen.add(lw)
        if key_of(w) not in _INDEX:
            miss.append(w)
    return miss


def speak(word: str) -> tuple[bool, str]:
    """播放单词发音。返回 (成功, 说明)。纯本地，无网络、无系统接口。"""
    global _SOUND, _LAST_ERR
    word = (word or "").strip()
    if not word:
        return False, "没有可朗读的内容"
    k = key_of(word)
    path = os.path.join(AUDIO_DIR, k + ".mp3")
    if not os.path.exists(path):
        # 自动记入缺词清单，供用户一键导出→电脑批量生成发音→下一版装上就能读
        record_missing(word)
        return False, f"'{word}' 还没有发音。已自动记入「缺发音清单」，可在设置页一键复制发我，我批量生成后打进下一版；这个词可先用「看中文背诵」或「查看模式」。"
    try:
        from kivy.core.audio import SoundLoader  # type: ignore
        if _SOUND is not None:
            try:
                _SOUND.stop()
            except Exception:
                pass
        snd = SoundLoader.load(path)
        if snd is None:
            return False, "播放器无法加载这个发音文件"
        _SOUND = snd
        snd.play()
        return True, ""
    except Exception as e:
        _LAST_ERR = f"{type(e).__name__}: {e}"
        return False, "播放发音失败：" + _LAST_ERR
