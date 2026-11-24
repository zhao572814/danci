"""本地 SQLite 数据层（v2：基础词库 + 学习词单两层）。

两层模型
--------
* ``dict_entries`` —— **基础词库 / 词条主档**：唯一的音标、词义、例句来源。
  只通过「导入词库文件 / 手动补录 / 本机模型补全」写入，人工确认的条目会锁定。
  **上传图片或 PDF 永远不会改动这张表**，只是从里面挑词。
* ``words`` —— **学习条目**：一次批次里要背的东西，只引用 dict_entries.id，
  外加熟练度、创建时间。没有词条内容字段。

* ``memberships`` 多对多：一个条目可同时属于「已背诵」和「重点易错」；
  从已背库移出只删关系，词条主档和其它库完全不受影响。
* ``mark_log`` 记录每次标记，已背库可显示具体背诵日期（年月日）与次数。

v23 起**全新数据库自动植入内置全量词库**（17634 词 + 194 短语，均带发音），
APK 装上即用，无需用户手动导入。已有数据的库不会被改动；
「植入内置标准词库」按钮仅作误删后恢复。
旧版 v1 数据库会自动迁移，原有单词、背诵日期、熟练度、批次全部保留。
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import re
import sqlite3
import threading
from typing import Iterable, Optional

from .models import LIST_HARD, LIST_MASTERED, SCOPE_ALL, SCOPE_HARD, SCOPE_INBOX, SCOPE_MASTERED, WordRow

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 手机端（Kivy/APK）在 import 本模块前会通过环境变量 VOCAB_DATA_DIR 把数据目录
# 指向 app 私有可写目录；桌面端不设该变量时行为完全不变，仍用仓库下的 data/。
DATA_DIR = os.environ.get("VOCAB_DATA_DIR") or os.path.join(APP_DIR, "data")
ASSETS_DIR = os.path.join(APP_DIR, "assets")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "vocab.db")

BUILTIN_WORDS_JSON = os.path.join(ASSETS_DIR, "builtin_dict.json")
BUILTIN_PHRASES_JSON = os.path.join(ASSETS_DIR, "builtin_phrases.json")

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS dict_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    headword     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind         TEXT NOT NULL DEFAULT 'word',
    phonetic     TEXT NOT NULL DEFAULT '',
    translation  TEXT NOT NULL DEFAULT '',
    example      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'pending',
    locked       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS words (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     INTEGER NOT NULL UNIQUE,
    familiarity  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_words (
    batch_id INTEGER NOT NULL,
    word_id  INTEGER NOT NULL,
    PRIMARY KEY (batch_id, word_id)
);
CREATE TABLE IF NOT EXISTS memberships (
    word_id   INTEGER NOT NULL,
    list_name TEXT NOT NULL,
    added_on  TEXT NOT NULL,
    PRIMARY KEY (word_id, list_name)
);
CREATE TABLE IF NOT EXISTS mark_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id   INTEGER NOT NULL,
    action    TEXT NOT NULL,
    on_date   TEXT NOT NULL,
    at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON dict_entries(kind, source);
CREATE INDEX IF NOT EXISTS idx_memberships_list ON memberships(list_name, added_on);
CREATE INDEX IF NOT EXISTS idx_batch_words_word ON batch_words(word_id);
"""

V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS dict_entries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    headword     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind         TEXT NOT NULL DEFAULT 'word',
    phonetic     TEXT NOT NULL DEFAULT '',
    translation  TEXT NOT NULL DEFAULT '',
    example      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'pending',
    locked       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""


def today() -> str:
    return _dt.date.today().isoformat()


def now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def clean_headword(text: str) -> str:
    """规范化词条：去首尾标点、折叠空白、统一小写（缩写除外）。"""
    w = re.sub(r"\s+", " ", (text or "").strip())
    w = w.strip(".,;:!?()[]{}\"'`\u201c\u201d").replace("\u2019", "'")
    if w.isupper() and 2 <= len(w) <= 6 and " " not in w:
        return w
    return w.lower()


def guess_kind(headword: str) -> str:
    return "phrase" if " " in headword.strip() else "word"


def parse_entry_lines(text: str) -> list[dict]:
    """把粘贴/纯文本的词条清单解析成结构化条目（全程本地，不联网）。

    支持每行：
        apple                              只有词条
        take part in                       含空格自动识别为短语
        apple, n. 苹果                     逗号 + 中文释义
        apple /ˈæp.əl/ n. 苹果           带音标
        apple<TAB>/ˈæp.əl/<TAB>n. 苹果<TAB>An apple a day. 一天一苹果。
        苹果 apple                         中文在前也认
    行首序号（1. / - / •）会自动去掉；# 开头为注释。
    """
    out: list[dict] = []
    seen: dict[str, int] = {}
    pos_re = re.compile(
        # (?<![A-Za-z]) 保证词性标记前有单词边界，避免把 "take part in" 结尾的
        # "n" 误当成名词词性、把短语截断成 "take part i"。
        r"(?<![A-Za-z])\(?(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|pl)\.?\)?\s*$",
        re.I,
    )
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*(?:\d+[.)、]|[-•·*])\s*", "", line).strip()
        if not line:
            continue
        phonetic = translation = example = ""
        if "\t" in line:
            cols = [c.strip() for c in line.split("\t") if c.strip()]
            headword = cols[0]
            rest = cols[1:]

            def _is_phonetic(s: str) -> bool:
                s = s.strip()
                if len(s) > 80:
                    return False
                if s.startswith("/") and s.endswith("/"):
                    return True
                # 常见词库用方括号音标：[ˈæp.əl] / [[ˈæp.əl]]
                if s.startswith("[[") and s.endswith("]]"):
                    return True
                return s.startswith("[") and s.endswith("]")

            def _clean_phonetic(s: str) -> str:
                s = s.strip()
                while s.startswith("[") and s.endswith("]"):
                    s = s[1:-1].strip()
                return f"[{s}]" if s and s not in ("/", "//") else s

            if len(rest) >= 2 and _is_phonetic(rest[0]):
                # 两列也认：音标 + 释义；三列则是 音标 + 释义 + 例句
                phonetic = _clean_phonetic(rest[0])
                translation = rest[1] if len(rest) > 1 else ""
                example = rest[2] if len(rest) > 2 else ""
            else:
                for c in rest:
                    if _is_phonetic(c) and not phonetic:
                        phonetic = _clean_phonetic(c)
                    elif (re.search(r"[\u4e00-\u9fff]", c) and "." in c
                          and " " in c and len(c) > 15 and not example):
                        example = c
                    elif not translation:
                        translation = c
                    elif not example:
                        example = c
        else:
            m = re.search(r"/[^/]{1,80}/|\[{1,2}[^\[\]]{1,80}\]{1,2}", line)
            if m:
                phonetic = m.group(0)
                while phonetic.startswith("[") and phonetic.endswith("]"):
                    phonetic = phonetic[1:-1].strip()
                if phonetic and not (phonetic.startswith("/") and phonetic.endswith("/")):
                    phonetic = f"[{phonetic}]"
                line = (line[:m.start()] + " " + line[m.end():]).strip()
            zh = re.search(r"[\u4e00-\u9fff]", line)
            if zh:
                left = line[:zh.start()].strip(" ,，:：-—")
                right = line[zh.start():].strip()
                if re.search(r"[A-Za-z]", left):
                    pos_m = pos_re.search(left)
                    pos_prefix = left[pos_m.start():].strip() if pos_m else ""
                    headword = pos_re.sub("", left).strip(" ,，:：-—")
                    translation = (pos_prefix + " " + right).strip() if pos_prefix else right
                else:
                    eng = re.search(r"[A-Za-z][A-Za-z'’\- ]*", line)
                    headword = eng.group(0).strip() if eng else ""
                    translation = (
                        line.replace(eng.group(0), "", 1).strip(" ,，:：-—") if eng else right
                    )
            else:
                headword = line.strip(" ,，")
        headword = clean_headword(headword)
        if not headword or not re.search(r"[A-Za-z]", headword):
            continue
        if len(headword.split()) > 8:   # 明显是整句，不是词条
            continue
        item = {
            "headword": headword, "phonetic": phonetic.strip(),
            "translation": translation.strip(), "example": example.strip(),
            "kind": guess_kind(headword),
        }
        key = headword.lower()
        if key in seen:
            # 同一词条重复出现时，保留信息更全的那一条
            old = out[seen[key]]
            score = lambda d: sum(bool(d[k]) for k in ("phonetic", "translation", "example"))
            if score(item) > score(old):
                out[seen[key]] = item
            continue
        seen[key] = len(out)
        out.append(item)
    return out


class VocabDB:
    """每条操作自带连接，避免跨线程共享。"""

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        con = self._connect()
        try:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(words)")}
            if cols and "word" in cols and "entry_id" not in cols:
                self._migrate_v1(con)
            con.executescript(_SCHEMA)
            self._normalize_legacy(con)
            con.commit()
        finally:
            con.close()
        # 自动补齐内置全量词库：APK 装上即具备识别 + 朗读能力，不再要求用户手动点。
        # v26 改进：改成「精确差集」判定——只要内置 JSON 里有任何一个词还不在库里
        # 就补植入（INSERT OR IGNORE 只增不覆盖，用户词条/背诵进度/自定义词全保留）。
        # 旧逻辑用数量阈值（builtin_n < expect*0.9），新版只多 147 个词时不会触发，
        # 导致 are/is/was 等新增高频词永远进不了老用户的库 —— 用户实际踩到了这个坑。
        # 失败不影响启动——最坏情况退回手动植入。
        try:
            con2 = self._connect()
            try:
                try:
                    with open(BUILTIN_WORDS_JSON, encoding="utf-8") as _f:
                        builtin_data = json.load(_f)
                except Exception:
                    builtin_data = {}
                need_seed = False
                if builtin_data:
                    have = {
                        (r[0] or "").lower() for r in
                        con2.execute("SELECT headword FROM dict_entries").fetchall()
                    }
                    # 抽样比对：发现任一内置词缺失即补种（全量 INSERT OR IGNORE 幂等）
                    need_seed = any(
                        clean_headword(hw).lower() not in have
                        for hw in builtin_data
                        if clean_headword(hw)
                    )
                else:
                    # JSON 读不到时退回数量阈值兜底
                    builtin_n = con2.execute(
                        "SELECT COUNT(*) FROM dict_entries WHERE source='builtin'"
                    ).fetchone()[0]
                    need_seed = builtin_n < 17000 * 0.9
                if need_seed:
                    self._seed_builtin(con2, force=True)
                    con2.commit()
            finally:
                con2.close()
        except Exception:
            pass

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------- 初始化 / 迁移
    def _migrate_v1(self, con: sqlite3.Connection) -> None:
        """把 v1（words 表自带词条内容）无损升级到 v2。"""
        con.executescript(V1_SCHEMA)
        con.execute(
            "INSERT OR IGNORE INTO dict_entries"
            "(headword,kind,phonetic,translation,example,source,locked,created_at,updated_at) "
            "SELECT word, CASE WHEN instr(word,' ')>0 THEN 'phrase' ELSE 'word' END, "
            "phonetic, translation, example, COALESCE(NULLIF(info_source,''),'legacy'), "
            "CASE WHEN info_source='manual' THEN 1 ELSE 0 END, created_at, updated_at FROM words"
        )
        con.executescript("""
            CREATE TABLE IF NOT EXISTS words_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL UNIQUE,
                familiarity INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO words_v2(id,entry_id,familiarity,created_at,updated_at)
              SELECT w.id, d.id, w.familiarity, w.created_at, w.updated_at
              FROM words w JOIN dict_entries d ON d.headword = w.word;
            DROP TABLE words;
            ALTER TABLE words_v2 RENAME TO words;
        """)

    def _seed_builtin(self, con: sqlite3.Connection, force: bool = False) -> int:
        """把内置标准词库写进基础词库，返回真正新增的条数。

        只有用户主动调用 install_builtin_library() 才会执行；force=True 时忽略
        历史标记，允许用户删掉某几条后重新植入（同名已存在的不覆盖）。
        """
        ts = now()
        added = 0
        for flag, path, source, kind in (
            ("seeded_words_v1", BUILTIN_WORDS_JSON, "builtin", "word"),
            ("seeded_phrases_v1", BUILTIN_PHRASES_JSON, "phrase_builtin", "phrase"),
        ):
            if not force and con.execute(
                "SELECT 1 FROM settings WHERE key=?", (flag,)
            ).fetchone():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {}
            for headword, info in data.items():
                hw = clean_headword(headword)
                if not hw:
                    continue
                cur = con.execute(
                    "INSERT OR IGNORE INTO dict_entries"
                    "(headword,kind,phonetic,translation,example,source,locked,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,0,?,?)",
                    (hw, kind, info.get("phonetic", ""), info.get("translation", ""),
                     info.get("example", ""), source, ts, ts),
                )
                added += cur.rowcount or 0
            con.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (flag, ts)
            )
        return added

    def install_builtin_library(self) -> tuple[int, str]:
        """一键植入内置标准词库（用户主动点击才会执行，默认空白启动）。"""
        with self._lock, self._connect() as con:
            n = self._seed_builtin(con, force=True)
            con.commit()
        return n, f"已植入内置标准词库 {n} 条新词条（常用单词 + 常见短语，含音标 / 释义 / 例句）"

    def backup_to(self, path: str) -> str:
        """做一份一致性数据库备份（清空或大改前调用）。"""
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        src = self._connect()
        try:
            dst = sqlite3.connect(path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return path

    def repair_shifted_entries(self) -> dict[str, int]:
        """修复旧导入造成的字段错位：音标被塞进释义、释义被挤进例句。

        错位特征（导入器只认 /.../ 音标、不认 [...] 时产生）：
          phonetic 为空 且 translation 整体是 [...] / [[...]] 音标。
        修复：translation → phonetic（去掉多余方括号），example → translation，
              example 清空（原词库本就没有例句，example 里装的是释义）。
        返回 {'scanned':..., 'fixed':..., 'no_translation':...}。
        只用一条 UPDATE 批量完成，几万条也不会卡。
        """
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT id, translation, example FROM dict_entries WHERE phonetic=''"
            ).fetchall()
            to_fix_shift = []   # 有释义：音标归位、释义上移
            to_fix_only = []    # 没有释义：只把音标归位
            for r in rows:
                tr = (r["translation"] or "").strip()
                if not (tr.startswith("[") and tr.endswith("]")):
                    continue
                inner = tr
                while inner.startswith("[") and inner.endswith("]"):
                    inner = inner[1:-1].strip()
                if not inner:
                    continue
                ph = f"[{inner}]"
                ex = (r["example"] or "").strip()
                # example 里是「词性. 中文」这类释义，而非英文例句 → 上移为释义
                has_zh = bool(re.search(r"[\u4e00-\u9fff]", ex))
                looks_like_sentence = bool(
                    re.search(r"e\.?g\.?|例句|例\s*[:：]|example", ex, re.I)
                    or (re.search(r"[A-Za-z]", ex[:1]) and len(ex.split()) >= 4)
                )
                if ex and has_zh and not looks_like_sentence:
                    to_fix_shift.append((ph, ex, r["id"]))
                else:
                    # 例句列如果是真例句就保留，只把音标挪走
                    to_fix_only.append((ph, ex, r["id"]))
            if to_fix_shift:
                con.executemany(
                    "UPDATE dict_entries SET phonetic=?, translation=?, example='', "
                    "updated_at=? WHERE id=?",
                    [(p, t, now(), i) for p, t, i in to_fix_shift],
                )
            if to_fix_only:
                con.executemany(
                    "UPDATE dict_entries SET phonetic=?, translation='', updated_at=? WHERE id=?",
                    [(p, now(), i) for p, _, i in to_fix_only],
                )
        return {
            "scanned": len(rows),
            "fixed": len(to_fix_shift) + len(to_fix_only),
            "with_translation": len(to_fix_shift),
            "phonetic_only": len(to_fix_only),
        }

    def clear_all_data(self, keep_settings: bool = True) -> dict[str, int]:
        """清空全部数据：基础词库、学习词单、批次、背诵记录。可用于「恢复空白」。"""
        tables = ("words", "memberships", "batch_words", "batches", "mark_log", "dict_entries")
        with self._lock, self._connect() as con:
            before = {
                t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables
            }
            for t in tables:
                con.execute(f"DELETE FROM {t}")
            if keep_settings:
                con.execute(
                    "DELETE FROM settings WHERE key IN ('seeded_words_v1','seeded_phrases_v1')"
                )
            else:
                con.execute("DELETE FROM settings")
        return before

    def _normalize_legacy(self, con: sqlite3.Connection) -> None:
        """旧版从图片/PDF 自动建的空词条，降级为「待补录」，不伪装成已收录的基础词条。"""
        con.execute(
            "UPDATE dict_entries SET source='pending' WHERE source='legacy' "
            "AND phonetic='' AND translation='' AND example='' AND locked=0"
        )

    # ------------------------------------------------------------------ 设置
    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    # ------------------------------------------------------------- 基础词库
    def list_entries(
        self, search: str = "", kind: str = "", source: str = "", limit: int = 5000,
        offset: int = 0,
    ) -> list[dict]:
        sql = (
            "SELECT d.*, "
            "(SELECT COUNT(*) FROM words w WHERE w.entry_id=d.id) AS ref_count, "
            "(SELECT COUNT(*) FROM words w JOIN memberships m ON m.word_id=w.id "
            "  AND m.list_name='mastered' WHERE w.entry_id=d.id) AS mastered_ref "
            "FROM dict_entries d WHERE 1=1"
        )
        params: list[object] = []
        if kind:
            sql += " AND d.kind=?"
            params.append(kind)
        if source:
            sql += " AND d.source=?"
            params.append(source)
        if search:
            sql += " AND (d.headword LIKE ? OR d.translation LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        sql += " ORDER BY d.kind DESC, d.headword COLLATE NOCASE ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def entry_stats(self) -> dict:
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM dict_entries").fetchone()[0]
            words = con.execute(
                "SELECT COUNT(*) FROM dict_entries WHERE kind='word'").fetchone()[0]
            phrases = con.execute(
                "SELECT COUNT(*) FROM dict_entries WHERE kind='phrase'").fetchone()[0]
            pending = con.execute(
                "SELECT COUNT(*) FROM dict_entries WHERE source='pending'").fetchone()[0]
            # 例句是可选的：有音标（短语可无）+ 释义就算信息完整
            complete = con.execute(
                "SELECT COUNT(*) FROM dict_entries WHERE translation<>'' "
                "AND (kind='phrase' OR phonetic<>'') AND source<>'pending'").fetchone()[0]
        return {"total": total, "words": words, "phrases": phrases,
                "pending": pending, "complete": complete}

    def lookup_entry(self, headword: str) -> Optional[dict]:
        hw = clean_headword(headword)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM dict_entries WHERE headword=?", (hw,)
            ).fetchone()
        return dict(row) if row else None

    def _upsert_in_con(
        self, con: sqlite3.Connection, headword: str, phonetic: str, translation: str,
        example: str, kind: str, source: str, locked: int, overwrite: bool,
    ) -> int:
        """单条写入的实现；批量导入时复用同一个连接/事务，避免几万次开关数据库。"""
        hw = clean_headword(headword)
        if not hw:
            return 0
        k = kind or guess_kind(hw)
        ts = now()
        row = con.execute("SELECT * FROM dict_entries WHERE headword=?", (hw,)).fetchone()
        if not row:
            cur = con.execute(
                "INSERT INTO dict_entries"
                "(headword,kind,phonetic,translation,example,source,locked,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (hw, k, phonetic, translation, example, source, locked, ts, ts),
            )
            return cur.lastrowid
        eid = row["id"]
        if not overwrite:
            return eid
        # 已有内容不被空白覆盖；人工锁定的条目不允许自动补全改写
        if row["locked"] and source in ("ollama", "builtin", "phrase_builtin"):
            return eid
        ph = phonetic or row["phonetic"]
        tr = translation or row["translation"]
        ex = example or row["example"]
        new_source = source if source not in ("", "pending") else row["source"]
        if row["source"] == "pending" and (ph or tr or ex):
            new_source = source
        con.execute(
            "UPDATE dict_entries SET kind=?, phonetic=?, translation=?, example=?, "
            "source=?, locked=MAX(locked,?), updated_at=? WHERE id=?",
            (k, ph, tr, ex, new_source, locked, ts, eid),
        )
        return eid

    def upsert_entry(
        self, headword: str, phonetic: str = "", translation: str = "", example: str = "",
        kind: str = "", source: str = "manual", locked: int = 1, overwrite: bool = True,
    ) -> int:
        """新增或更新基础词库词条。这是写入基础库的唯一入口之一。"""
        with self._lock, self._connect() as con:
            return self._upsert_in_con(
                con, headword, phonetic, translation, example, kind,
                source, locked, overwrite,
            )

    def update_entry(
        self, entry_id: int, phonetic: str, translation: str, example: str,
        source: str = "manual",
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE dict_entries SET phonetic=?, translation=?, example=?, "
                "source=?, locked=1, updated_at=? WHERE id=?",
                (phonetic, translation, example, source, now(), entry_id),
            )

    def delete_entry(self, entry_id: int) -> tuple[bool, str]:
        """只能删除没有被学习条目引用的词条（保证主档引用完整）。"""
        with self._lock, self._connect() as con:
            n = con.execute(
                "SELECT COUNT(*) FROM words WHERE entry_id=?", (entry_id,)
            ).fetchone()[0]
            if n:
                return False, "这个词条已经在背诵词单里，不能直接从基础库删除"
            con.execute("DELETE FROM dict_entries WHERE id=?", (entry_id,))
        return True, "已从基础词库删除"

    def pending_items(self) -> list[dict]:
        """基础库里还没补录音标/释义/例句、但已经被挑进词单的条目。"""
        with self._connect() as con:
            rows = con.execute(
                "SELECT d.*, w.id AS word_id FROM dict_entries d "
                "JOIN words w ON w.entry_id=d.id "
                "WHERE d.source='pending' OR d.translation='' "
                "ORDER BY w.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def import_entries(
        self, items: Iterable[dict], source: str = "manual",
        locked: int = 1, overwrite: bool = True,
    ) -> tuple[int, str]:
        """批量把词条写进基础词库：一个事务写完全部，几万条也不会卡。"""
        n = 0
        with self._lock, self._connect() as con:
            for it in items:
                if not it.get("headword"):
                    continue
                self._upsert_in_con(
                    con, it["headword"], it.get("phonetic", ""),
                    it.get("translation", ""), it.get("example", ""),
                    it.get("kind", ""), source, locked, overwrite,
                )
                n += 1
        return n, f"基础词库已加入 {n} 条词条"

    def import_text_entries(
        self, text: str, source: str = "manual", overwrite: bool = True
    ) -> tuple[int, str]:
        items = parse_entry_lines(text)
        if not items:
            return 0, "没有解析出有效词条（每行一条，英文开头，中文释义可选）"
        n, msg = self.import_entries(items, source=source, locked=1, overwrite=overwrite)
        phrases = sum(1 for i in items if i["kind"] == "phrase")
        return n, f"{msg}（其中短语 {phrases} 条）"

    def import_dict_file(self, path: str, mode: str = "merge") -> tuple[int, str]:
        """导入基础词库文件（CSV 或 JSON）。

        CSV 列：word/headword/单词/phrase、phonetic/音标、translation/释义、
                example/例句、kind/类型（可选）
        JSON 格式：{"apple": {"phonetic":..., "translation":..., "example":...}, ...}
        mode='merge' 增量合并并补全已有条目；'fill' 只补全空字段。
        """
        ext = os.path.splitext(path)[1].lower()
        count, skipped = 0, 0
        try:
            if ext in (".txt", ".md", ".list"):
                text = ""
                for enc in ("utf-8-sig", "utf-8", "gbk"):
                    try:
                        with open(path, "r", encoding=enc) as f:
                            text = f.read()
                        break
                    except UnicodeDecodeError:
                        continue
                return self.import_text_entries(text, source="manual", overwrite=(mode == "merge"))
            elif ext == ".json":
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    data = {d.get("word", d.get("headword", "")): d for d in data}
                items = []
                for hw, info in data.items():
                    if not isinstance(info, dict):
                        skipped += 1
                        continue
                    items.append({
                        "headword": hw,
                        "phonetic": info.get("phonetic", ""),
                        "translation": info.get("translation", ""),
                        "example": info.get("example", ""),
                        "kind": info.get("kind", ""),
                    })
                count, _ = self.import_entries(
                    items, source="json", locked=1, overwrite=(mode == "merge")
                )
            else:
                with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                    sample = f.read(4096)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample)
                    except csv.Error:
                        dialect = csv.excel
                    reader = csv.DictReader(f, dialect=dialect)
                    cols = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}

                    def pick(*names):
                        for n in names:
                            if n in cols:
                                return cols[n]
                        return None

                    cw = pick("word", "headword", "单词", "phrase", "词条")
                    cp = pick("phonetic", "音标", "ipa")
                    ct = pick("translation", "释义", "meaning", "definition", "解释", "词义")
                    ce = pick("example", "例句", "sentence")
                    ck = pick("kind", "类型")
                    if not cw:
                        return 0, "找不到 word / 单词 / 词条 列"
                    items = []
                    for row in reader:
                        hw = (row.get(cw) or "").strip()
                        if not hw:
                            skipped += 1
                            continue
                        items.append({
                            "headword": hw,
                            "phonetic": (row.get(cp) or "").strip() if cp else "",
                            "translation": (row.get(ct) or "").strip() if ct else "",
                            "example": (row.get(ce) or "").strip() if ce else "",
                            "kind": (row.get(ck) or "").strip().lower() if ck else "",
                        })
                    count, _ = self.import_entries(
                        items, source="csv", locked=1, overwrite=(mode == "merge")
                    )
        except Exception as e:
            return 0, f"导入失败：{type(e).__name__}: {e}"
        note = f"基础词库已导入 {count} 条" + (f"，跳过 {skipped} 条空行" if skipped else "")
        return count, note

    def export_dict_csv(self, path: str, kind: str = "") -> tuple[int, str]:
        rows = self.list_entries(kind=kind)
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["word", "kind", "phonetic", "translation", "example", "source"])
                for r in rows:
                    w.writerow([r["headword"], r["kind"], r["phonetic"],
                                r["translation"], r["example"], r["source"]])
            return len(rows), f"已导出 {len(rows)} 条词条"
        except Exception as e:
            return 0, f"导出失败：{e}"

    def phrase_headwords(self) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT headword FROM dict_entries WHERE kind='phrase'").fetchall()
        return [r["headword"] for r in rows]

    # ------------------------------------------------------------- 学习词单
    def _study_id_for_entry(self, con: sqlite3.Connection, entry_id: int) -> int:
        row = con.execute("SELECT id FROM words WHERE entry_id=?", (entry_id,)).fetchone()
        if row:
            return row["id"]
        ts = now()
        cur = con.execute(
            "INSERT INTO words(entry_id,familiarity,created_at,updated_at) VALUES(?,0,?,?)",
            (entry_id, ts, ts),
        )
        return cur.lastrowid

    def import_items(
        self, items: Iterable[dict], batch_name: str, source: str = "",
        allow_pending: bool = True,
    ) -> tuple[int, list[int], int, int]:
        """把一批「从基础库里挑中的条目」关联到新批次，返回 (批次ID, 学习条目ID, 新增数, 跳过数)。

        已收录：只建立引用，**绝不改动 dict_entries**。
        未收录：allow_pending=True 时创建一个 source='pending' 的占位词条（信息留空，
                不算正式基础库词条，可在「基础词库」页补录）；False 时直接跳过。
        """
        ts = now()
        ids: list[int] = []
        new_count = 0
        skipped = 0
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO batches(name,source,created_at) VALUES(?,?,?)",
                (batch_name, source, ts),
            )
            batch_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            for it in items:
                hw = clean_headword(it.get("headword", ""))
                if not hw:
                    continue
                entry = con.execute(
                    "SELECT id, source FROM dict_entries WHERE headword=?", (hw,)
                ).fetchone()
                if entry:
                    eid = entry["id"]
                else:
                    if not allow_pending:
                        skipped += 1
                        continue
                    kind = it.get("kind") or guess_kind(hw)
                    cur = con.execute(
                        "INSERT INTO dict_entries"
                        "(headword,kind,phonetic,translation,example,source,locked,created_at,updated_at) "
                        "VALUES(?,?,'','','','pending',0,?,?)",
                        (hw, kind, ts, ts),
                    )
                    eid = cur.lastrowid
                before = con.execute(
                    "SELECT id FROM words WHERE entry_id=?", (eid,)
                ).fetchone()
                wid = self._study_id_for_entry(con, eid)
                if not before:
                    new_count += 1
                con.execute(
                    "INSERT OR IGNORE INTO batch_words(batch_id,word_id) VALUES(?,?)",
                    (batch_id, wid),
                )
                if wid not in ids:
                    ids.append(wid)
        return batch_id, ids, new_count, skipped

    def import_words(self, words: Iterable[str], batch_name: str, source: str = ""):
        """兼容旧调用：把字符串列表当作条目导入。"""
        items = [{"headword": w, "kind": guess_kind(w)} for w in words]
        bid, ids, new, _ = self.import_items(items, batch_name, source)
        return bid, ids, new

    def list_batches(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT b.id, b.name, b.source, b.created_at, "
                "(SELECT COUNT(*) FROM batch_words bw WHERE bw.batch_id=b.id) AS total, "
                "(SELECT COUNT(*) FROM batch_words bw JOIN memberships m "
                "  ON m.word_id=bw.word_id AND m.list_name='mastered' "
                "  WHERE bw.batch_id=b.id) AS mastered "
                "FROM batches b ORDER BY b.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_batch(self, batch_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM batch_words WHERE batch_id=?", (batch_id,))
            con.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    # ------------------------------------------------------------------ 词条
    def get_word(self, word_id: int) -> Optional[WordRow]:
        rows = self.list_words(SCOPE_ALL, search=f"#{word_id}")
        return WordRow.from_dict(rows[0]) if rows else None

    def update_word_info(
        self, word_id: int, phonetic: str = "", translation: str = "", example: str = "",
        info_source: str = "manual",
    ) -> None:
        """兼容 UI：按学习条目 id 写回它引用的基础词库词条（人工补录会锁定）。"""
        with self._connect() as con:
            row = con.execute(
                "SELECT entry_id FROM words WHERE id=?", (word_id,)
            ).fetchone()
        if row:
            self.update_entry(row["entry_id"], phonetic, translation, example, info_source)

    def fill_entry_info(self, headword: str, phonetic: str, translation: str, example: str,
                        source: str = "ollama") -> bool:
        """自动补全：只填空缺，不碰人工锁定的词条。"""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM dict_entries WHERE headword=?", (clean_headword(headword),)
            ).fetchone()
            if not row or row["locked"]:
                return False
            con.execute(
                "UPDATE dict_entries SET "
                "phonetic=CASE WHEN phonetic='' THEN ? ELSE phonetic END, "
                "translation=CASE WHEN translation='' THEN ? ELSE translation END, "
                "example=CASE WHEN example='' THEN ? ELSE example END, "
                "source=CASE WHEN source='pending' THEN ? ELSE source END, "
                "updated_at=? WHERE id=?",
                (phonetic, translation, example, source, now(), row["id"]),
            )
        return True

    def _missing_clause(self) -> str:
        # 短语本来就没有音标，不把空的 phonetic 当成缺失
        return ("d.locked=0 AND (d.translation='' OR d.example='' "
                "OR (d.kind='word' AND d.phonetic=''))")

    def missing_headwords(self, limit: int = 10000) -> list[str]:
        """整个基础词库里信息不全、且未被人工锁定的词条。"""
        with self._connect() as con:
            rows = con.execute(
                "SELECT headword FROM dict_entries d WHERE " + self._missing_clause()
                + " ORDER BY d.kind ASC, d.id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["headword"] for r in rows]

    def words_missing_info(self, limit: int = 40) -> list[WordRow]:
        """兼容旧接口：返回带词条信息的待补全条目。"""
        with self._connect() as con:
            rows = con.execute(
                "SELECT d.id, d.headword AS word, d.phonetic, d.translation, d.example, "
                "d.source AS info_source, 0 AS familiarity, NULL AS mastered_on, "
                "NULL AS hard_on, '' AS batch_names "
                "FROM dict_entries d WHERE " + self._missing_clause()
                + " ORDER BY d.kind ASC, d.id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [WordRow.from_dict(dict(r)) for r in rows]

    def set_familiarity(self, word_id: int, value: Optional[int] = None, delta: int = 0) -> None:
        with self._lock, self._connect() as con:
            if value is None:
                con.execute(
                    "UPDATE words SET familiarity=MAX(-3, MIN(9, familiarity+?)), updated_at=? WHERE id=?",
                    (delta, now(), word_id),
                )
            else:
                con.execute(
                    "UPDATE words SET familiarity=?, updated_at=? WHERE id=?",
                    (value, now(), word_id),
                )

    # ------------------------------------------------------------------ 词库关系
    def _mark(self, word_ids: Iterable[int], list_name: str, date: str) -> int:
        ids = list(dict.fromkeys(word_ids))
        with self._lock, self._connect() as con:
            for wid in ids:
                con.execute(
                    "INSERT INTO memberships(word_id,list_name,added_on) VALUES(?,?,?) "
                    "ON CONFLICT(word_id,list_name) DO UPDATE SET added_on=excluded.added_on",
                    (wid, list_name, date),
                )
                con.execute(
                    "INSERT INTO mark_log(word_id,action,on_date,at) VALUES(?,?,?,?)",
                    (wid, "add:" + list_name, date, now()),
                )
            if list_name == LIST_MASTERED:
                con.executemany(
                    "UPDATE words SET familiarity=MAX(familiarity, 4), updated_at=? WHERE id=?",
                    [(now(), wid) for wid in ids],
                )
        return len(ids)

    def _unmark(self, word_ids: Iterable[int], list_name: str) -> int:
        ids = list(dict.fromkeys(word_ids))
        with self._lock, self._connect() as con:
            for wid in ids:
                con.execute(
                    "DELETE FROM memberships WHERE word_id=? AND list_name=?", (wid, list_name)
                )
                con.execute(
                    "INSERT INTO mark_log(word_id,action,on_date,at) VALUES(?,?,?,?)",
                    (wid, "remove:" + list_name, today(), now()),
                )
        return len(ids)

    def mark_mastered(self, word_ids: Iterable[int], date: Optional[str] = None) -> int:
        return self._mark(word_ids, LIST_MASTERED, date or today())

    def unmark_mastered(self, word_ids: Iterable[int]) -> int:
        return self._unmark(word_ids, LIST_MASTERED)

    def mark_hard(self, word_ids: Iterable[int]) -> int:
        return self._mark(word_ids, LIST_HARD, today())

    def unmark_hard(self, word_ids: Iterable[int]) -> int:
        return self._unmark(word_ids, LIST_HARD)

    def mark_history(self, word_id: int, list_name: str = LIST_MASTERED) -> list[str]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT on_date FROM mark_log WHERE word_id=? AND action=? ORDER BY id DESC",
                (word_id, "add:" + list_name),
            ).fetchall()
        return [r["on_date"] for r in rows]

    def mark_history_counts(
        self, word_ids: Iterable[int], list_name: str = LIST_MASTERED
    ) -> dict[int, int]:
        """一次查出多行的累计划入次数，避免表格每行都单独开一次数据库连接。"""
        ids = list(dict.fromkeys(word_ids))
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT word_id, COUNT(*) AS c FROM mark_log "
                f"WHERE word_id IN ({marks}) AND action=? GROUP BY word_id",
                (*ids, "add:" + list_name),
            ).fetchall()
        return {r["word_id"]: r["c"] for r in rows}

    def clear_inbox_words(self) -> dict[str, int]:
        """只清空「待背」的学习记录，已背单词与基础词库完全不动。

        待背 = 没有 mastered 标记的学习条目。注意：从「已背词库」删除而回到
        待背的词，已不再有 mastered 标记，因此会被本操作一并清掉（符合预期）。
        删除范围仅限学习侧四张表，dict_entries 一个字都不改。
        """
        with self._lock, self._connect() as con:
            # 用临时表锁定待删集合：后续删除不能改变 NOT EXISTS 的判定
            con.execute("DROP TABLE IF EXISTS _inbox_clear")
            con.execute(
                "CREATE TEMP TABLE _inbox_clear AS "
                "SELECT w.id FROM words w WHERE NOT EXISTS ("
                "  SELECT 1 FROM memberships m "
                "  WHERE m.word_id=w.id AND m.list_name='mastered')"
            )
            cleared = con.execute("SELECT COUNT(*) FROM _inbox_clear").fetchone()[0]
            kept = con.execute(
                "SELECT COUNT(*) FROM words WHERE id NOT IN (SELECT id FROM _inbox_clear)"
            ).fetchone()[0]
            con.execute(
                "DELETE FROM memberships WHERE word_id IN (SELECT id FROM _inbox_clear)"
            )
            con.execute(
                "DELETE FROM batch_words WHERE word_id IN (SELECT id FROM _inbox_clear)"
            )
            con.execute(
                "DELETE FROM mark_log WHERE word_id IN (SELECT id FROM _inbox_clear)"
            )
            con.execute("DELETE FROM words WHERE id IN (SELECT id FROM _inbox_clear)")
            # 清掉已经没有学习条目的空批次，避免留下「0 词」的批次
            con.execute(
                "DELETE FROM batches WHERE NOT EXISTS ("
                "  SELECT 1 FROM batch_words bw WHERE bw.batch_id=batches.id)"
            )
            con.execute("DROP TABLE IF EXISTS _inbox_clear")
        return {"cleared": cleared, "kept_mastered": kept}

    def clear_study_items(self) -> int:
        """清空学习词单（批次、词库关系、熟练度、背诵日期），保留基础词库词条。"""
        with self._lock, self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            for table in ("words", "memberships", "batch_words", "batches", "mark_log"):
                con.execute(f"DELETE FROM {table}")
        return n

    def delete_word_permanently(self, word_ids: Iterable[int]) -> None:
        """彻底删除学习条目；基础词库词条保留（两层分离的核心）。"""
        with self._lock, self._connect() as con:
            for wid in word_ids:
                con.execute("DELETE FROM words WHERE id=?", (wid,))
                con.execute("DELETE FROM memberships WHERE word_id=?", (wid,))
                con.execute("DELETE FROM batch_words WHERE word_id=?", (wid,))
                con.execute("DELETE FROM mark_log WHERE word_id=?", (wid,))

    # ------------------------------------------------------------------ 查询
    def _base_select(self) -> str:
        return (
            "SELECT w.id, d.headword AS word, d.phonetic, d.translation, d.example, "
            "d.source AS info_source, d.kind, d.locked, d.id AS entry_id, "
            "w.familiarity, "
            "(SELECT added_on FROM memberships m WHERE m.word_id=w.id AND m.list_name='mastered') "
            "  AS mastered_on, "
            "(SELECT added_on FROM memberships m WHERE m.word_id=w.id AND m.list_name='hard') "
            "  AS hard_on, "
            "(SELECT GROUP_CONCAT(b.name, '、') FROM batch_words bw "
            "  JOIN batches b ON b.id=bw.batch_id WHERE bw.word_id=w.id) AS batch_names "
            "FROM words w JOIN dict_entries d ON d.id=w.entry_id"
        )

    def list_words(
        self, scope: str = SCOPE_INBOX, batch_id: Optional[int] = None,
        search: str = "", order_by_familiarity: bool = True,
        limit: int = 0, offset: int = 0,
    ) -> list[dict]:
        sql = self._base_select()
        where: list[str] = []
        params: list[object] = []
        if batch_id is not None:
            where.append("EXISTS (SELECT 1 FROM batch_words bw WHERE bw.word_id=w.id AND bw.batch_id=?)")
            params.append(batch_id)
        if scope == SCOPE_INBOX:
            where.append("NOT EXISTS (SELECT 1 FROM memberships m WHERE m.word_id=w.id "
                         "AND m.list_name='mastered')")
        elif scope == SCOPE_MASTERED:
            where.append("EXISTS (SELECT 1 FROM memberships m WHERE m.word_id=w.id "
                         "AND m.list_name='mastered')")
        elif scope == SCOPE_HARD:
            where.append("EXISTS (SELECT 1 FROM memberships m WHERE m.word_id=w.id "
                         "AND m.list_name='hard')")
        if search:
            s = search.strip()
            if s.startswith("#") and s[1:].isdigit():
                where.append("w.id=?")
                params.append(int(s[1:]))
            else:
                where.append("(d.headword LIKE ? OR d.translation LIKE ?)")
                params.extend([f"%{s}%", f"%{s}%"])
        if where:
            sql += " WHERE " + " AND ".join(where)
        if order_by_familiarity and scope in (SCOPE_INBOX, SCOPE_HARD):
            sql += " ORDER BY w.familiarity ASC, d.headword COLLATE NOCASE ASC"
        elif scope == SCOPE_MASTERED:
            sql += " ORDER BY mastered_on DESC, d.headword COLLATE NOCASE ASC"
        else:
            sql += " ORDER BY d.headword COLLATE NOCASE ASC"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            mastered = con.execute(
                "SELECT COUNT(*) FROM memberships WHERE list_name=?", (LIST_MASTERED,)
            ).fetchone()[0]
            hard = con.execute(
                "SELECT COUNT(*) FROM memberships WHERE list_name=?", (LIST_HARD,)
            ).fetchone()[0]
            today_mastered = con.execute(
                "SELECT COUNT(*) FROM mark_log WHERE action=? AND on_date=?",
                ("add:mastered", today()),
            ).fetchone()[0]
        return {"total": total, "inbox": total - mastered, "mastered": mastered,
                "hard": hard, "today_mastered": today_mastered}
