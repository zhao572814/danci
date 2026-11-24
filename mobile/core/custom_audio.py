"""用户自定义发音：给任意单词配一段自己的录音 / 导入的音频文件。

用途：内置词库和现场合成之外，用户想自己给某个词录音（练习发音、方言记忆、
或合成读不准的生词）。纯本地，音频存在 App 数据目录，不上传、不联网。

优先级（见 core/audio.py speak）：内置 mp3 > 自定义 > 现场合成 > 记缺词清单。

存储：VOCAB_DATA_DIR/custom_audio/<md5(word)>.<ext>
朗读：Kivy SoundLoader 直接播这个文件，已在真机验证可用。
"""
from __future__ import annotations

import hashlib
import os

_DIRNAME = "custom_audio"


def _data_dir() -> str:
    return os.environ.get("VOCAB_DATA_DIR") or os.path.expanduser("~")


def audio_dir() -> str:
    d = os.path.join(_data_dir(), _DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def key_of(word: str) -> str:
    return hashlib.md5((word or "").strip().lower().encode("utf-8")).hexdigest()


def path_for(word: str, ext: str = ".wav") -> str:
    return os.path.join(audio_dir(), key_of(word) + ext)


def find(word: str) -> str:
    """返回这个词的自定义音频路径（存在才返回），否则空串。支持常见扩展名。"""
    base = key_of(word)
    for ext in (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"):
        p = os.path.join(audio_dir(), base + ext)
        if os.path.exists(p) and os.path.getsize(p) > 200:
            return p
    return ""


def has(word: str) -> bool:
    return bool(find(word))


def save_bytes(word: str, data: bytes, ext: str = ".mp3") -> tuple[bool, str]:
    """把音频字节存成这个词的自定义发音。ext 用源文件扩展名（含点）。"""
    word = (word or "").strip()
    if not word or not data:
        return False, "没有词或音频数据"
    if not ext.startswith("."):
        ext = "." + ext
    if ext.lower() not in (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"):
        ext = ".mp3"
    try:
        # 清掉同词其他扩展名的旧文件，避免多份冲突
        for old in (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"):
            p = path_for(word, old)
            if os.path.exists(p):
                os.remove(p)
        p = path_for(word, ext)
        with open(p, "wb") as f:
            f.write(data)
        if os.path.getsize(p) < 200:
            return False, "音频文件太小，可能没录到声音"
        return True, p
    except Exception as e:
        return False, f"保存失败：{type(e).__name__}: {e}"


def remove(word: str) -> bool:
    """删除这个词的自定义发音。"""
    ok = False
    for ext in (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac"):
        p = path_for(word, ext)
        if os.path.exists(p):
            try:
                os.remove(p)
                ok = True
            except Exception:
                pass
    return ok


def count() -> int:
    try:
        return len([f for f in os.listdir(audio_dir())
                    if os.path.splitext(f)[1].lower() in
                    (".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac")])
    except Exception:
        return 0


_SOUND_KEEP = []   # 持有播放对象，防止被 GC 后声音中断


def speak(word: str) -> tuple[bool, str]:
    """播放这个词的自定义发音。返回 (成功, 说明)。"""
    p = find(word)
    if not p:
        return False, "没有自定义发音"
    try:
        from kivy.core.audio import SoundLoader  # type: ignore
        snd = SoundLoader.load(p)
        if snd is None:
            return False, f"播放器无法加载这个音频（{os.path.basename(p)}）"
        _SOUND_KEEP.append(snd)
        if len(_SOUND_KEEP) > 8:
            _SOUND_KEEP.pop(0)
        snd.play()
        return True, ""
    except Exception as e:
        return False, f"播放失败：{type(e).__name__}: {e}"


# -------------------------------------------------------------- 麦克风录音
# p4a 提供 plyer 的 recorder（底层 MediaRecorder），在多数安卓/兼容层可用。
# 全程 try/except，失败如实返回原因，不假装成功、不让 App 崩。

_RECORDER = None
_RECORDING_WORD = ""


def ensure_mic_permission() -> tuple[bool, str]:
    """运行时申请麦克风权限（Android 6+ 必须，光在 spec 声明不够）。"""
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        if activity is None:
            return False, "拿不到 Activity，无法申请麦克风权限"
        Build = autoclass("android.os.Build")
        if Build.VERSION.SDK_INT < 23:
            return True, ""
        PermissionChecker = autoclass("androidx.core.content.PermissionChecker")
        PackageManager = autoclass("android.content.pm.PackageManager")
        granted = PermissionChecker.checkSelfPermission(
            activity, "android.permission.RECORD_AUDIO")
        if granted == PackageManager.PERMISSION_GRANTED:
            return True, ""
        # 异步申请：requestPermissions 后用户点允许，下次录音即可用
        # 用 autoclass 建字符串数组，不依赖 jarray（这个 p4a 版本没有它）
        try:
            StrArr = autoclass("[Ljava.lang.String;")
            perms = StrArr(1)
            perms[0] = "android.permission.RECORD_AUDIO"
        except Exception:
            # 极老 pyjnius 的兜底
            from jnius import jarray  # type: ignore
            perms = jarray("java.lang.String")(["android.permission.RECORD_AUDIO"])
        activity.requestPermissions(perms, 1001)
        return False, "请在弹出的授权框里点「允许」，然后再点一次录音"
    except Exception as e:
        return False, f"申请麦克风权限失败：{type(e).__name__}: {e}"


def record_start(word: str) -> tuple[bool, str]:
    """开始录音，录到临时 .m4a；点停止后由 record_stop 保存到 word。

    用 pyjnius 直接调 Android MediaRecorder（plyer 没有 recorder API，不能用）。
    全程 try/except，失败如实返回，用户可以改用「导入音频文件」。
    """
    global _RECORDER, _RECORDING_WORD
    word = (word or "").strip()
    if not word:
        return False, "请先输入要录音的单词"
    try:
        from jnius import autoclass  # type: ignore
        MediaRecorder = autoclass("android.media.MediaRecorder")
        d = audio_dir()
        tmp = os.path.join(d, "_recording_tmp.m4a")
        if os.path.exists(tmp):
            os.remove(tmp)
        rec = MediaRecorder()
        rec.setAudioSource(MediaRecorder.AudioSource.MIC)
        rec.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        rec.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        rec.setOutputFile(tmp)
        rec.prepare()
        rec.start()
        _RECORDER = rec
        _RECORDING_WORD = word
        return True, ""
    except Exception as e:
        return False, (f"录音启动失败：{type(e).__name__}: {e}。"
                       f"设备可能不给麦克风权限，可改用「导入音频文件」。")


def record_stop() -> tuple[bool, str]:
    """停止录音并保存为当前词的自定义发音。"""
    global _RECORDER, _RECORDING_WORD
    if _RECORDER is None or not _RECORDING_WORD:
        return False, "当前没有在录音"
    word = _RECORDING_WORD
    rec = _RECORDER
    src = os.path.join(audio_dir(), "_recording_tmp.m4a")
    try:
        try:
            rec.stop()
        except Exception:
            pass
        try:
            rec.release()
        except Exception:
            pass
        if not os.path.exists(src) or os.path.getsize(src) < 200:
            return False, "没录到声音，请检查麦克风权限后重试，或改用「导入音频文件」"
        with open(src, "rb") as f:
            data = f.read()
        ok, msg = save_bytes(word, data, ".m4a")
        return ok, msg
    except Exception as e:
        return False, f"录音保存失败：{type(e).__name__}: {e}"
    finally:
        try:
            os.remove(src)
        except Exception:
            pass
        _RECORDER = None
        _RECORDING_WORD = ""
