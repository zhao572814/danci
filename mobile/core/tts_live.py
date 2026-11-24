"""现场离线 TTS：用打包进 APK 的 sherpa-onnx 引擎，对任意英文单词/短语现场合成发音。

设计原则（重要）：
  * 完全隔离：所有安卓/原生调用都在函数内部、全部 try/except。任何一步失败
    都返回 (False, 原因)，由调用方回退到内置音频，绝不让 App 崩溃。
  * 懒加载：第一次朗读才初始化引擎，不拖慢启动。
  * 合成->存 wav->用 Kivy SoundLoader 播放：SoundLoader 播本地文件已在真机验证可用，
    sherpa 的 GeneratedAudio 没有 play()，故走 wav 中转，不自研 AudioTrack。

用法：
    from core import tts_live
    ok, msg = tts_live.speak("some_custom_word")
"""
from __future__ import annotations

import os
import threading
import traceback

# assets 里模型目录（相对 assets 根）。buildozer 会把 assets/ 整个打进 APK。
MODEL_DIR = "tts_model"
MODEL_FILE = MODEL_DIR + "/en_US-amy-low.onnx"
LEXICON_FILE = MODEL_DIR + "/lexicon-en-us.txt"
TOKENS_FILE = MODEL_DIR + "/tokens.txt"

_engine = None
_init_lock = threading.Lock()
_init_err = ""
_sound = None          # 持有当前播放对象，防止被 GC 停止
_wav_path = ""


def _app_data_dir() -> str:
    """可写目录：优先 Kivy 的 user_data_dir，退回 VOCAB_DATA_DIR。"""
    try:
        from kivy.app import App
        d = App.get_running_app().user_data_dir
        if d:
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    d = os.environ.get("VOCAB_DATA_DIR") or os.path.expanduser("~")
    os.makedirs(d, exist_ok=True)
    return d


def available() -> bool:
    """引擎是否已就绪（会触发一次懒加载）。仅用于朗读时，别在 UI 线程随便调。"""
    return _get_engine() is not None


def status_text() -> str:
    """只报状态，绝不触发初始化（初始化 60MB 模型会卡 UI）。"""
    if _engine is not None:
        return "现场合成：已就绪（任意词可读）"
    if _init_err:
        return f"现场合成：不可用（{_init_err[:50]}），已回退内置音频"
    return "现场合成：待首次使用（补录的词点朗读时自动启动）"


def _get_engine():
    """懒加载 sherpa-onnx OfflineTts。失败返回 None 并记录原因，绝不抛异常。"""
    global _engine, _init_err
    if _engine is not None:
        return _engine
    if _init_err:
        return None
    with _init_lock:
        if _engine is not None:
            return _engine
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            if activity is None:
                raise RuntimeError("拿不到 Android Activity")
            asset_mgr = activity.getAssets()

            Vits = autoclass("com.k2fsa.sherpa.onnx.OfflineTtsVitsModelConfig")
            MCfg = autoclass("com.k2fsa.sherpa.onnx.OfflineTtsModelConfig")
            Cfg = autoclass("com.k2fsa.sherpa.onnx.OfflineTtsConfig")
            Tts = autoclass("com.k2fsa.sherpa.onnx.OfflineTts")

            # AAR 是 Kotlin data class 全参构造：注意 lexicon 在 tokens 前面
            vits = Vits(MODEL_FILE, LEXICON_FILE, TOKENS_FILE,
                        "", "", 0.667, 0.8, 1.0)
            # model config: (vits, matcha, kokoro, zipvoice, kitten, pocket,
            #                supertonic, numThreads, debug, provider)
            mcfg = MCfg(vits, None, None, None, None, None, None,
                        1, False, "cpu")
            cfg = Cfg(mcfg, "", "", 1, 0.2)

            _engine = Tts(asset_mgr, cfg)   # Android 构造必须传 AssetManager
            return _engine
        except Exception as e:
            _init_err = f"{type(e).__name__}: {e}"
            try:
                with open(os.path.join(_app_data_dir(), "tts_live_error.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(_init_err + "\n" + traceback.format_exc())
            except Exception:
                pass
            return None


def synthesize(word: str) -> tuple[bool, str]:
    """把词合成成 wav 文件，返回 (成功, wav路径或错误说明)。同步调用。"""
    global _wav_path
    word = (word or "").strip()
    if not word:
        return False, "没有可朗读的内容"
    eng = _get_engine()
    if eng is None:
        return False, "现场合成引擎不可用"

    try:
        audio = eng.generate(word, 0, 1.0)   # text, sid, speed —— 同步
        if audio is None:
            return False, "合成没有返回结果"
        out = os.path.join(_app_data_dir(), "tts_live.wav")
        audio.save(out)
        if not os.path.exists(out) or os.path.getsize(out) < 100:
            return False, "合成音频写入失败"
        _wav_path = out
        return True, out
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def speak(word: str) -> tuple[bool, str]:
    """现场合成并播放。成功返回 (True, "")；任何失败返回 (False, 原因)。"""
    global _sound
    ok, path_or_err = synthesize(word)
    if not ok:
        return False, path_or_err
    try:
        from kivy.core.audio import SoundLoader
        if _sound is not None:
            try:
                _sound.stop()
            except Exception:
                pass
        snd = SoundLoader.load(path_or_err)
        if snd is None:
            return False, "播放器无法加载合成的音频"
        _sound = snd
        snd.play()
        return True, ""
    except Exception as e:
        return False, f"播放失败：{type(e).__name__}: {e}"


def release() -> None:
    global _engine
    if _engine is not None:
        try:
            _engine.release()
        except Exception:
            pass
        _engine = None
