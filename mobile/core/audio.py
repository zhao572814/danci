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


def speak(word: str) -> tuple[bool, str]:
    """播放单词发音。返回 (成功, 说明)。纯本地，无网络、无系统接口。"""
    global _SOUND, _LAST_ERR
    word = (word or "").strip()
    if not word:
        return False, "没有可朗读的内容"
    k = key_of(word)
    path = os.path.join(AUDIO_DIR, k + ".mp3")
    if not os.path.exists(path):
        return False, f"'{word}' 没有内置发音（只预置了内置词库里的常用词）。可先用「看中文背诵」或「查看模式」。"
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
