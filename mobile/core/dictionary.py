"""基础词库的来源管理：内置种子、词库文件导入、本机模型补全。

重要约定：**文档 / 图片导入流程不会调用这里的任何写操作**。
程序默认空白启动，基础词库只通过下面三条途径写入：
  1. 用户主动点「植入内置标准词库」（在 db 层完成，默认不执行）；
  2. 导入自己的 CSV / JSON / TXT 词库文件，或手动补录词条；
  3. 可选：本机 Ollama 批量补全空缺字段（只填空，不碰人工锁定的词条）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

from .db import VocabDB

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
BUILTIN_PATH = os.path.join(ASSETS, "builtin_dict.json")
PHRASES_PATH = os.path.join(ASSETS, "builtin_phrases.json")

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"

PROMPT_TMPL = """你是一本英汉学习词典。为下面每个英文词条给出：音标（单个单词给国际音标并用 /.../ 包裹，短语留空字符串）、简洁中文释义（含词性）、一个简短英文例句及其中文翻译。
严格只输出 JSON，格式：
{{"items":[{{"word":"apple","phonetic":"/ˈæp.əl/","translation":"n. 苹果","example":"She eats an apple every morning. 她每天早上吃一个苹果。"}}]}}
不要输出任何解释、Markdown 或多余文字。短语也要给出例句，但 phonetic 留空。不要编造词条，只处理给出的词条。
词条列表：{words}
"""


def _extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class Dictionary:
    def __init__(self, db: VocabDB):
        self.db = db
        self._builtin: Optional[dict] = None
        self._phrases: Optional[dict] = None

    @property
    def builtin(self) -> dict:
        if self._builtin is None:
            try:
                with open(BUILTIN_PATH, "r", encoding="utf-8") as f:
                    self._builtin = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._builtin = {}
        return self._builtin

    @property
    def builtin_phrases(self) -> dict:
        if self._phrases is None:
            try:
                with open(PHRASES_PATH, "r", encoding="utf-8") as f:
                    self._phrases = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._phrases = {}
        return self._phrases

    def fill_builtin(self, words=None) -> int:
        """兼容旧调用。内置词库只在用户主动点「植入内置标准词库」时写入，导入图片不写库。"""
        return 0

    def import_dict_file(self, path: str, mode: str = "merge") -> tuple[int, str]:
        return self.db.import_dict_file(path, mode)

    def import_csv(self, path: str) -> tuple[int, str]:
        return self.db.import_dict_file(path, "merge")

    def export_dict_csv(self, path: str, kind: str = "") -> tuple[int, str]:
        return self.db.export_dict_csv(path, kind)

    # ------------------------------------------------------------ 本机 Ollama
    def ollama_config(self) -> tuple[str, str, bool]:
        return (
            self.db.get_setting("ollama_url", DEFAULT_OLLAMA_URL),
            self.db.get_setting("ollama_model", DEFAULT_OLLAMA_MODEL),
            self.db.get_setting("auto_enrich", "0") == "1",
        )

    def ollama_status(self, timeout: float = 2.0) -> tuple[bool, str]:
        import requests

        url, model, _ = self.ollama_config()
        try:
            r = requests.get(url.rstrip("/") + "/api/tags", timeout=timeout)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            if not names:
                return False, "本机 Ollama 已启动，但还没有安装任何模型"
            if model not in names:
                return False, f"未找到模型 {model}；本机可用：{'、'.join(names[:4])}"
            return True, f"已连接本机 Ollama，模型 {model} 可用"
        except Exception as e:
            return False, f"本机 Ollama 未启动或无法访问（{type(e).__name__}）；不影响其它功能"

    def enrich_words(
        self,
        headwords: list[str],
        progress: Optional[Callable[[int, int, str], None]] = None,
        batch_size: int = 8,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[int, str]:
        """补全基础词库里信息不全的词条，只填空、不覆盖人工内容。"""
        import requests

        headwords = [h for h in headwords if h]
        if not headwords:
            return 0, "没有需要补全的词条"
        ok, msg = self.ollama_status()
        if not ok:
            return 0, msg
        url, model, _ = self.ollama_config()
        total = len(headwords)
        filled = 0
        last_error = ""
        for start in range(0, total, batch_size):
            if cancel and cancel():
                break
            chunk = headwords[start:start + batch_size]
            try:
                resp = requests.post(
                    url.rstrip("/") + "/api/generate",
                    json={
                        "model": model,
                        "prompt": PROMPT_TMPL.format(words=", ".join(chunk)),
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0.2},
                    },
                    timeout=180,
                )
                resp.raise_for_status()
                data = _extract_json(resp.json().get("response", ""))
                items = (data or {}).get("items", []) if isinstance(data, dict) else []
                for item in items:
                    hw = str(item.get("word", "")).strip()
                    if not hw:
                        continue
                    if self.db.fill_entry_info(
                        hw,
                        str(item.get("phonetic", "") or "").strip(),
                        str(item.get("translation", "") or "").strip(),
                        str(item.get("example", "") or "").strip(),
                        source="ollama",
                    ):
                        filled += 1
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
            done = min(start + batch_size, total)
            if progress:
                progress(done, total, chunk[0])
        note = f"基础词库已补全 {filled} 条" + (f"（部分批次失败：{last_error}）" if last_error else "")
        return filled, note
