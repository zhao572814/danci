"""离线单词发音（点击单词即可朗读）。

Windows：调用系统自带的 SAPI 语音引擎生成 wav 并缓存，用标准库 winsound 播放。
         本机实测可用英文语音 Microsoft Zira Desktop，全程不联网。
Android：后续 APK 版走系统 TextToSpeech（pyjnius），核心层接口不变。

设计成「生成 wav + 缓存」而不是每次直接朗读，是因为首次之后点击基本是秒响。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import sys
import threading
from typing import Optional

from .db import DATA_DIR

AUDIO_DIR = os.path.join(DATA_DIR, "audio")


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _run_ps(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """用 EncodedCommand 传脚本，彻底规避中文/引号导致的编码问题。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


class WindowsSpeech:
    def __init__(self):
        os.makedirs(AUDIO_DIR, exist_ok=True)
        self._voices: Optional[list[str]] = None
        self._lock = threading.Lock()

    def list_voices(self) -> list[str]:
        if self._voices is not None:
            return self._voices
        script = (
            "$v = New-Object -ComObject SAPI.SpVoice\n"
            "foreach ($x in $v.GetVoices()) { Write-Output ('VOICE:' + $x.GetDescription()) }\n"
        )
        try:
            r = _run_ps(script, timeout=30)
            voices = [
                line[6:].strip()
                for line in (r.stdout or "").splitlines()
                if line.startswith("VOICE:")
            ]
            self._voices = voices
        except Exception:
            self._voices = []
        return self._voices

    def default_english_voice(self) -> str:
        for v in self.list_voices():
            if re.search(r"English|en-US|en-GB", v, re.I):
                return v
        return ""

    def audio_path(self, word: str, voice: str = "", rate: int = 0) -> str:
        key = f"{word.lower()}|{voice}|{rate}".encode("utf-8")
        name = hashlib.md5(key).hexdigest() + ".wav"
        return os.path.join(AUDIO_DIR, name)

    def synthesize(self, word: str, voice: str = "", rate: int = 0) -> Optional[str]:
        path = self.audio_path(word, voice, rate)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            return path
        if voice:
            voice_clause = (
                "foreach ($x in $v.GetVoices()) { "
                f"if ($x.GetDescription() -eq {_ps_quote(voice)}) "
                "{ $v.Voice = $x; break } }"
            )
        else:
            voice_clause = (
                "foreach ($x in $v.GetVoices()) { "
                "if ($x.GetDescription() -match 'English') { $v.Voice = $x; break } }"
            )
        script = (
            "$ErrorActionPreference='Stop'\n"
            "$v = New-Object -ComObject SAPI.SpVoice\n"
            f"{voice_clause}\n"
            f"$v.Rate = {int(rate)}\n"
            "$f = New-Object -ComObject SAPI.SpFileStream\n"
            f"$f.Open({_ps_quote(path)}, 3, $false)\n"
            "$v.AudioOutputStream = $f\n"
            f"$v.Speak({_ps_quote(word)})\n"
            "$f.Close()\n"
        )
        with self._lock:
            try:
                r = _run_ps(script, timeout=60)
                if r.returncode != 0:
                    return None
            except Exception:
                return None
        return path if os.path.exists(path) and os.path.getsize(path) > 1000 else None

    def play(self, path: str) -> bool:
        try:
            import winsound
            winsound.PlaySound(
                path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            )
            return True
        except Exception:
            return False


class Speech:
    """对 UI 暴露的统一接口。"""

    def __init__(self, db):
        self.db = db
        self._backend = WindowsSpeech() if sys.platform == "win32" else None

    @property
    def available(self) -> bool:
        return self._backend is not None

    def list_voices(self) -> list[str]:
        return self._backend.list_voices() if self._backend else []

    def default_english_voice(self) -> str:
        return self._backend.default_english_voice() if self._backend else ""

    def speak(self, word: str) -> tuple[bool, str]:
        if not self._backend:
            return False, "当前平台暂不支持发音（Windows 电脑版 / 安卓版支持）"
        voice = self.db.get_setting("tts_voice", "")
        try:
            rate = int(self.db.get_setting("tts_rate", "0"))
        except ValueError:
            rate = 0
        path = self._backend.synthesize(word, voice, rate)
        if not path:
            return False, "语音合成失败，请检查系统语音引擎"
        return (True, "") if self._backend.play(path) else (False, "音频播放失败")
