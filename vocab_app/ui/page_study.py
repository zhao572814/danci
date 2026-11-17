"""背诵页：一张一张的闪卡测验。

三种模式：
  · 读音背诵：点「朗读」听单词 → 输入听到的单词 → 确认 → ✓ / ✗
  · 看中文背诵：看基础词库里的中文释义 → 输入对应单词 → 确认 → ✓ / ✗
  · 查看模式：直接看单词的全部内容（单词 / 音标 / 释义 / 例句），可朗读

答对的单词自动划入「已背词库」；答错的留在待背，下次还会出现。
所有音标 / 释义 / 例句都取自基础词库，本页不改动它。
"""

from __future__ import annotations

import re
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QVBoxLayout, QWidget, QSizePolicy,
)

from ..core.db import today
from ..core.models import (
    RECITE_CHINESE, RECITE_MODE_LABELS, RECITE_SOUND, RECITE_VIEW,
    SCOPE_ALL, SCOPE_INBOX,
)
from .common import run_async


def _normalize(text: str) -> str:
    """判题用的宽松归一化：忽略大小写、首尾空格、多余空白和末尾标点。"""
    s = unicodedata.normalize("NFKC", (text or "")).strip().lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"[.,!?;:。，！？；：]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


class StudyPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.mode = (ctx.db.get_setting("recite_mode", RECITE_SOUND) or RECITE_SOUND)
        if self.mode not in RECITE_MODE_LABELS:
            self.mode = RECITE_SOUND
        self.scope = SCOPE_INBOX
        self.words: list[dict] = []
        self.idx = 0
        self.answered = False
        self.correct = False
        self._build()

    # ------------------------------------------------------------------ 界面
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 14)
        root.setSpacing(12)

        head = QLabel(
            "一次背一个：选「读音背诵」听发音拼写，或「看中文背诵」看释义拼写；"
            "答对自动划入已背词库，答错会留在待背。音标 / 释义 / 例句都取自基础词库。"
        )
        head.setObjectName("Hint")
        head.setWordWrap(True)
        root.addWidget(head)

        # 模式切换
        line1 = QHBoxLayout()
        line1.addWidget(QLabel("背诵模式："))
        self.mode_group = QButtonGroup(self)
        for key in (RECITE_SOUND, RECITE_CHINESE, RECITE_VIEW):
            rb = QRadioButton(RECITE_MODE_LABELS[key])
            rb.setChecked(key == self.mode)
            rb.toggled.connect(lambda on, k=key: self._on_mode(k) if on else None)
            self.mode_group.addButton(rb)
            line1.addWidget(rb)
        line1.addSpacing(18)
        line1.addWidget(QLabel("范围："))
        self.scope_group = QButtonGroup(self)
        for label, scope in (("只背未背过的", SCOPE_INBOX), ("全部单词", SCOPE_ALL)):
            rb = QRadioButton(label)
            rb.setChecked(scope == self.scope)
            rb.toggled.connect(lambda on, s=scope: self._on_scope(s) if on else None)
            self.scope_group.addButton(rb)
            line1.addWidget(rb)
        line1.addStretch(1)
        self.stat = QLabel("")
        self.stat.setObjectName("StatChip")
        line1.addWidget(self.stat)
        root.addLayout(line1)

        # 进度
        pline = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setFixedHeight(9)
        self.progress.setTextVisible(False)
        pline.addWidget(self.progress, 1)
        self.pos_label = QLabel("")
        self.pos_label.setObjectName("Hint")
        pline.addWidget(self.pos_label)
        root.addLayout(pline)

        # 卡片容器（居中，固定最大宽度，内容动态生成）
        center = QHBoxLayout()
        center.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("StudyCard")
        self.card.setMinimumSize(560, 330)
        self.card.setMaximumWidth(760)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(28, 24, 28, 24)
        self.card_layout.setSpacing(14)
        center.addWidget(self.card, 1)
        center.addStretch(1)
        root.addLayout(center, 1)

        # 翻页 / 操作
        bottom = QHBoxLayout()
        self.prev_btn = QPushButton("← 上一个")
        self.prev_btn.clicked.connect(self._prev)
        bottom.addWidget(self.prev_btn)
        self.next_btn = QPushButton("下一个 →")
        self.next_btn.clicked.connect(self._next)
        bottom.addWidget(self.next_btn)
        self.skip_btn = QPushButton("跳过")
        self.skip_btn.clicked.connect(self._next)
        bottom.addWidget(self.skip_btn)
        bottom.addSpacing(12)
        self.mastered_btn = QPushButton("标记已背")
        self.mastered_btn.setObjectName("Warn")
        self.mastered_btn.clicked.connect(self._toggle_mastered)
        bottom.addWidget(self.mastered_btn)
        self.speak_btn = QPushButton("♪ 朗读当前单词")
        self.speak_btn.clicked.connect(self._speak_current)
        bottom.addWidget(self.speak_btn)
        bottom.addStretch(1)
        clear_btn = QPushButton("清空待背单词")
        clear_btn.setObjectName("Warn")
        clear_btn.setToolTip(
            "只清掉还没背过的词，已背词库和基础词库都不动。"
            "从已背库删除而回到待背的词属于未背，会被清掉。"
        )
        clear_btn.clicked.connect(self._clear_inbox)
        bottom.addWidget(clear_btn)
        refresh = QPushButton("重新开始")
        refresh.clicked.connect(self.refresh)
        bottom.addWidget(refresh)
        root.addLayout(bottom)

    # ------------------------------------------------------------------ 数据
    def refresh(self):
        self.words = self.ctx.db.list_words(scope=self.scope)
        self.idx = 0
        self._show_card()

    def _on_mode(self, key: str):
        if key == self.mode:
            return
        self.mode = key
        self.ctx.db.set_setting("recite_mode", key)
        self._show_card()

    def _on_scope(self, scope: str):
        if scope == self.scope:
            return
        self.scope = scope
        self.refresh()

    def _current(self) -> dict | None:
        return self.words[self.idx] if 0 <= self.idx < len(self.words) else None

    def _clear_card(self):
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)  # 立即脱离布局，避免切换瞬间残留旧内容
                w.deleteLater()

    def _show_card(self):
        self._clear_card()
        self.answered = False
        self.correct = False
        total = len(self.words)
        c = self.ctx.db.counts()
        self.stat.setText(f"待背 {c['inbox']} · 已背 {c['mastered']}")
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(self.idx + 1 if total else 0)
        self.pos_label.setText(f"{self.idx + 1} / {total}" if total else "0 / 0")
        self.prev_btn.setEnabled(self.idx > 0)
        more = self.idx + 1 < total
        self.next_btn.setEnabled(more)
        self.skip_btn.setEnabled(more)
        row = self._current()
        if not row:
            empty = QLabel(
                "这里还没有要背的单词。先到「基础词库」导入词条，"
                "再到「识别单词」页粘贴 txt / 文本挑出要背的词。"
            )
            empty.setObjectName("Hint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.card_layout.addStretch(1)
            self.card_layout.addWidget(empty)
            self.card_layout.addStretch(1)
            self.mastered_btn.setEnabled(False)
            self.speak_btn.setEnabled(False)
            return
        self.mastered_btn.setEnabled(True)
        self.speak_btn.setEnabled(True)
        is_done = bool(row.get("mastered_on"))
        self.mastered_btn.setText("✓ 已背 · 点此移出" if is_done else "标记已背")
        if self.mode == RECITE_VIEW:
            self._render_view(row)
        elif self.mode == RECITE_SOUND:
            self._render_quiz(row, sound=True)
        else:
            self._render_quiz(row, sound=False)

    # ------------------------------------------------------------------ 卡片内容
    def _info(self, row: dict) -> tuple[str, str, str]:
        """音标 / 释义 / 例句一律以基础词库为准；缺的显示占位提示。"""
        return (
            row.get("phonetic") or "",
            row.get("translation") or "",
            row.get("example") or "",
        )

    def _render_view(self, row: dict):
        kind = QLabel("单词" if row.get("kind") != "phrase" else "短语")
        kind.setObjectName("TagFam")
        kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_layout.addWidget(kind)

        word = QPushButton(row["word"])
        word.setObjectName("ViewWord")
        word.setCursor(Qt.CursorShape.PointingHandCursor)
        word.setToolTip("点击朗读")
        word.clicked.connect(lambda: self._speak(row["word"]))
        self.card_layout.addWidget(word, 0, Qt.AlignmentFlag.AlignCenter)

        ph, tr, ex = self._info(row)
        speak = QPushButton("♪ 朗读这个单词")
        speak.setObjectName("Speak")
        speak.clicked.connect(lambda: self._speak(row["word"]))
        self.card_layout.addWidget(speak, 0, Qt.AlignmentFlag.AlignCenter)

        self.card_layout.addSpacing(4)
        ph_lbl = QLabel(f"音标：{ph or '（基础词库里没有）'}")
        ph_lbl.setObjectName("Phonetic")
        ph_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_layout.addWidget(ph_lbl)

        # 释义和例句都是对单词的解释，归到一个「解释」区一起显示
        explain_parts = [p for p in (tr, ex) if p]
        explain = "\n".join(explain_parts) if explain_parts else "（基础词库里没有）"
        ex_lbl = QLabel(f"解释：{explain}")
        ex_lbl.setObjectName("Translation")
        ex_lbl.setWordWrap(True)
        ex_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_layout.addWidget(ex_lbl)
        self.card_layout.addStretch(1)

    def _render_quiz(self, row: dict, sound: bool):
        tip = QLabel(
            "点下面的按钮听读音，然后输入你听到的单词" if sound
            else "根据中文释义，输入对应的英文单词"
        )
        tip.setObjectName("Hint")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card_layout.addWidget(tip)

        ph, tr, ex = self._info(row)
        if sound:
            play = QPushButton("♪ 点击朗读")
            play.setObjectName("BigSpeak")
            play.clicked.connect(lambda: self._speak(row["word"]))
            self.card_layout.addWidget(play, 0, Qt.AlignmentFlag.AlignCenter)
            # 听音模式不显示答案，只提示词性 / 长度，避免直接看到单词
            hint = QLabel(f"（{len(row['word'].split())} 个词）" if row.get("kind") == "phrase" else "")
            hint.setObjectName("Hint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.card_layout.addWidget(hint)
        else:
            if tr:
                prompt = QLabel(tr)
                prompt.setObjectName("PromptCN")
                prompt.setWordWrap(True)
                prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.card_layout.addWidget(prompt)
            else:
                warn = QLabel(
                    "基础词库里这个词还没有中文释义，无法用中文背诵。\n"
                    "可切到「查看模式」或到「基础词库」页补上释义。"
                )
                warn.setObjectName("Notice")
                warn.setWordWrap(True)
                warn.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.card_layout.addWidget(warn)

        self.answer_edit = QLineEdit()
        self.answer_edit.setPlaceholderText("在这里输入单词 / 短语，按回车确认")
        self.answer_edit.setMinimumHeight(42)
        self.answer_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(15)
        self.answer_edit.setFont(font)
        self.answer_edit.returnPressed.connect(lambda: self._check(row))
        self.card_layout.addWidget(self.answer_edit)

        ok = QPushButton("确认 ✓")
        ok.setObjectName("Primary")
        ok.clicked.connect(lambda: self._check(row))
        self.card_layout.addWidget(ok, 0, Qt.AlignmentFlag.AlignCenter)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setMinimumHeight(56)
        self.result_label.hide()  # 判定前不占位，避免空白残影
        self.card_layout.addWidget(self.result_label)

        # 查看 / 再听一遍
        self.reveal_btn = QPushButton("看答案 / 再听一遍")
        self.reveal_btn.clicked.connect(lambda: self._reveal(row, sound))
        self.card_layout.addWidget(self.reveal_btn, 0, Qt.AlignmentFlag.AlignCenter)

    # ------------------------------------------------------------------ 判定
    def _check(self, row: dict):
        if self.answered:
            return
        typed = self.answer_edit.text().strip()
        if not typed:
            self.ctx.status("请先输入单词，或点「看答案」")
            return
        target = row["word"]
        ok = _normalize(typed) == _normalize(target)
        self.answered = True
        self.correct = ok
        self.answer_edit.setReadOnly(True)
        if ok and not row.get("mastered_on"):
            self.ctx.db.mark_mastered([row["id"]])
            row["mastered_on"] = today()
            self.ctx.refresh_counts_only()
        ph, tr, ex = self._info(row)
        detail = f"正确答案：{target}" + (f"  {ph}" if ph else "")
        if ok:
            self.result_label.setObjectName("ResultOK")
            self.result_label.setText(f"✓ 正确！已加入已背词库\n{detail}")
            if tr:
                self.result_label.setText(self.result_label.text() + f"\n{tr}")
        else:
            self.result_label.setObjectName("ResultBad")
            self.result_label.setText(
                f"✗ 还不对\n{detail}" + (f"\n{tr}" if tr else "")
            )
        self.result_label.setStyleSheet("")  # 触发 objectName 样式刷新
        self.result_label.show()
        if ex:
            self.result_label.setToolTip(f"例句：{ex}")
        self.ctx.status("答对了自动划入已背词库；答错的会留在待背，下次继续" if ok else "再听一遍，下一轮还会遇到它")

    def _reveal(self, row: dict, sound: bool):
        if sound:
            self._speak(row["word"])
        if self.answered:
            return
        ph, tr, ex = self._info(row)
        text = f"答案：{row['word']}" + (f"  {ph}" if ph else "")
        if tr:
            text += f"\n{tr}"
        if ex:
            text += f"\n例句：{ex}"
        self.result_label.setObjectName("ResultHint")
        self.result_label.setText(text)
        self.result_label.show()

    # ------------------------------------------------------------------ 操作
    def _speak(self, word: str):
        run_async(
            lambda emit: self.ctx.speech.speak(word),
            on_done=lambda res: self.ctx.status(f"♪ {word}" if res[0] else f"朗读失败：{res[1]}"),
            on_error=lambda m: self.ctx.status(f"朗读失败：{m}"),
        )

    def _speak_current(self):
        row = self._current()
        if row:
            self._speak(row["word"])

    def _prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._show_card()

    def _next(self):
        if self.idx + 1 < len(self.words):
            self.idx += 1
            self._show_card()
        else:
            self.ctx.status("已经是最后一个了；想重新背点「重新开始」")

    def _clear_inbox(self):
        """清空未背单词，保留已背词库与基础词库（二次确认）。"""
        c = self.ctx.db.counts()
        inbox = c["inbox"]
        if not inbox:
            self.ctx.status("当前没有待背单词，无需清空")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空待背单词")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"确定清空全部 {inbox} 个待背单词吗？")
        box.setInformativeText(
            "· 已背词库里的词和它们的背诵日期都会保留。\n"
            "· 基础词库的音标 / 释义 / 例句完全不动。\n"
            "· 注意：从已背词库删除、回到待背的词，会被一起清掉。\n"
            "· 想重新背，去「识别单词」页重新挑词即可。"
        )
        yes = box.addButton("清空待背", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        res = self.ctx.db.clear_inbox_words()
        self.idx = 0
        self.words = []
        self.refresh()
        self.ctx.refresh_views()
        self.ctx.status(
            f"已清空 {res['cleared']} 个待背单词；"
            f"已背词库 {res['kept_mastered']} 个词和基础词库都完好"
        )

    def _toggle_mastered(self):
        row = self._current()
        if not row:
            return
        if row.get("mastered_on"):
            self.ctx.db.unmark_mastered([row["id"]])
            row["mastered_on"] = None
            self.ctx.status(f"「{row['word']}」已移出已背词库，回到待背")
        else:
            self.ctx.db.mark_mastered([row["id"]])
            row["mastered_on"] = today()
            self.ctx.status(f"「{row['word']}」已划入已背词库（{today()}）")
        self.ctx.refresh_counts_only()
        self.mastered_btn.setText("✓ 已背 · 点此移出" if row["mastered_on"] else "标记已背")
