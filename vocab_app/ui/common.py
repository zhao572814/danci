"""界面公用件：浅色主题、后台任务桥、词条编辑对话框。"""

from __future__ import annotations

import threading
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)

STYLE = """
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #1f2937;
}
QMainWindow, QDialog { background: #f5f7fb; }
#NavList {
    background: #ffffff; border: none; border-right: 1px solid #e5e7eb;
    padding: 12px 6px; font-size: 14px; outline: none;
}
#NavList::item { padding: 11px 14px; margin: 2px 6px; border-radius: 8px; color: #4b5563; }
#NavList::item:selected { background: #e8f0fe; color: #1d4ed8; font-weight: 600; }
#NavList::item:hover:!selected { background: #f3f4f6; }
#Header { background: #ffffff; border-bottom: 1px solid #e5e7eb; }
#Title { font-size: 17px; font-weight: 700; color: #111827; }
#SubTitle { color: #6b7280; font-size: 12px; }
#StatChip { background: #eef2ff; color: #3730a3; border-radius: 12px; padding: 4px 10px; }
QGroupBox {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
    margin-top: 14px; padding: 14px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #374151; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 7px;
    padding: 7px 9px; selection-background-color: #bfdbfe;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #3b82f6; }
QPushButton {
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 7px;
    padding: 7px 13px; color: #374151;
}
QPushButton:hover { background: #f3f6fc; border-color: #93c5fd; }
QPushButton:pressed { background: #e8f0fe; }
QPushButton:disabled { color: #9ca3af; background: #f9fafb; }
QPushButton#Primary { background: #2563eb; border: 1px solid #2563eb; color: white; font-weight: 600; }
QPushButton#Primary:hover { background: #1d4ed8; }
QPushButton#Primary:disabled { background: #93c5fd; border-color: #93c5fd; color: #f1f5f9; }
QPushButton#Warn { background: #fff7ed; border: 1px solid #fed7aa; color: #c2410c; }
QPushButton#Warn:hover { background: #ffedd5; }
QPushButton#UserChip { background: #eef2ff; border: 1px solid #c7d2fe; color: #4338ca; border-radius: 14px; padding: 5px 12px; }
QPushButton#UserChip:hover { background: #e0e7ff; }
QPushButton#WordBtn {
    border: none; background: transparent; color: #111827;
    font-size: 22px; font-weight: 700; text-align: left; padding: 2px 0;
}
QPushButton#WordBtn:hover { color: #2563eb; }
QPushButton#Speak { border: none; background: #eef2ff; color: #4338ca; border-radius: 7px; padding: 4px 9px; }
QPushButton#Speak:hover { background: #e0e7ff; }
QTableWidget {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 9px;
    gridline-color: #f1f3f7; selection-background-color: #e8f0fe; selection-color: #111827;
}
QHeaderView::section {
    background: #f8fafc; border: none; border-bottom: 1px solid #e5e7eb;
    padding: 8px; font-weight: 600; color: #475569;
}
QTableWidget::item { padding: 6px 4px; }
#Card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; }
#CardHard { background: #fffbf5; border: 1px solid #fcd34d; border-radius: 10px; }
#CardDone { background: #f7fdf9; border: 1px solid #bbf7d0; border-radius: 10px; }
#Phonetic { color: #6d28d9; font-size: 14px; }
#Translation { color: #374151; font-size: 14px; }
#Example { color: #64748b; font-size: 12px; }
#Tag { color: #9a3412; background: #ffedd5; border-radius: 8px; padding: 2px 8px; font-size: 11px; }
#TagDone { color: #166534; background: #dcfce7; border-radius: 8px; padding: 2px 8px; font-size: 11px; }
#TagFam { color: #475569; background: #f1f5f9; border-radius: 8px; padding: 2px 8px; font-size: 11px; }
#Hint { color: #6b7280; font-size: 12px; }
#StudyCard { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; }
#ViewWord {
    border: none; background: transparent; color: #111827;
    font-size: 34px; font-weight: 700; padding: 4px 8px;
}
#ViewWord:hover { color: #2563eb; }
QPushButton#BigSpeak {
    background: #eef2ff; border: 1px solid #c7d2fe; color: #4338ca;
    border-radius: 12px; padding: 16px 34px; font-size: 20px; font-weight: 600;
}
QPushButton#BigSpeak:hover { background: #e0e7ff; }
#PromptCN { color: #111827; font-size: 22px; font-weight: 600; }
#ResultOK { color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0;
            border-radius: 9px; padding: 10px; font-size: 14px; }
#ResultBad { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca;
             border-radius: 9px; padding: 10px; font-size: 14px; }
#ResultHint { color: #4338ca; background: #eef2ff; border: 1px solid #c7d2fe;
              border-radius: 9px; padding: 10px; font-size: 14px; }
#Notice { color: #b45309; background: #fffbeb; border: 1px solid #fde68a; border-radius: 7px; padding: 8px; }
QCheckBox { spacing: 6px; }
QStatusBar { background: #ffffff; border-top: 1px solid #e5e7eb; color: #4b5563; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #94a3b8; }
QProgressBar { border: 1px solid #e5e7eb; border-radius: 6px; text-align: center; background: white; }
QProgressBar::chunk { background: #3b82f6; border-radius: 5px; }
"""


class Bus(QObject):
    """后台线程 → 主线程的安全信号桥。"""
    done = Signal(object)
    error = Signal(str)
    progress = Signal(object)


_TASKS: set = set()


def run_async(
    fn: Callable[[Callable[[object], None]], object],
    on_done: Optional[Callable] = None,
    on_error: Optional[Callable] = None,
    on_progress: Optional[Callable] = None,
) -> Bus:
    """在后台线程跑 fn(emit_progress)，结果通过 Qt 信号回到主线程。

    fn 里调用 emit_progress(obj) 可以推进度；返回值交给 on_done。
    """
    bus = Bus()

    def emit_progress(value):
        bus.progress.emit(value)

    if on_done is not None:
        bus.done.connect(on_done)
    if on_error is not None:
        bus.error.connect(on_error)
    if on_progress is not None:
        bus.progress.connect(on_progress)

    key = object()
    _TASKS.add(key)

    def work():
        try:
            result = fn(emit_progress)
            bus.done.emit(result)
        except Exception as e:
            traceback.print_exc()
            bus.error.emit(f"{type(e).__name__}: {e}")
        finally:
            _TASKS.discard(key)

    threading.Thread(target=work, daemon=True).start()
    return bus


class EditWordDialog(QDialog):
    """手动编辑 / 修正音标、释义、例句。"""

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑词条")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.word_edit = QLineEdit(row.get("word", ""))
        self.word_edit.setReadOnly(True)
        self.phonetic_edit = QLineEdit(row.get("phonetic", ""))
        self.translation_edit = QLineEdit(row.get("translation", ""))
        self.example_edit = QPlainTextEdit(row.get("example", ""))
        self.example_edit.setFixedHeight(80)
        self.phonetic_edit.setPlaceholderText("例如 /ˈæp.əl/")
        self.translation_edit.setPlaceholderText("例如 n. 苹果")
        self.example_edit.setPlaceholderText("英文例句 + 中文翻译")
        form.addRow("单词", self.word_edit)
        form.addRow("音标", self.phonetic_edit)
        form.addRow("释义", self.translation_edit)
        form.addRow("例句", self.example_edit)
        layout.addLayout(form)
        layout.addWidget(QLabel("手动填写的内容会保存在本机数据库，不会被自动补全覆盖。"))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            self.phonetic_edit.text().strip(),
            self.translation_edit.text().strip(),
            self.example_edit.toPlainText().strip(),
        )


class BatchSupplementDialog(QDialog):
    """一次把多个「库里没有的条目」补录进基础词库，允许留空稍后再填。"""

    def __init__(self, items: list[dict], parent=None):
        from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem
        from PySide6.QtCore import Qt

        super().__init__(parent)
        self.setWindowTitle("批量补录到基础词库")
        self.resize(680, min(520, 150 + 34 * len(items)))
        self._items = items
        layout = QVBoxLayout(self)
        hint = QLabel(
            f"下面 {len(items)} 条基础词库里没有。可以直接填写音标 / 释义 / 例句，"
            "也可以先留空加入，之后到「基础词库」页编辑。人工补录的词条会被锁定。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        layout.addWidget(hint)

        self.table = QTableWidget(len(items), 4)
        self.table.setHorizontalHeaderLabels(["词条", "音标", "释义", "例句"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in (1, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        for r, it in enumerate(items):
            hw = QTableWidgetItem(it["headword"])
            hw.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            hw.setToolTip("短语" if it.get("kind") == "phrase" else "单词")
            self.table.setItem(r, 0, hw)
            for c, key in ((1, "phonetic"), (2, "translation"), (3, "example")):
                self.table.setItem(r, c, QTableWidgetItem(it.get(key, "") or ""))
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("加入基础词库")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> list[tuple[str, str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            vals = []
            for c in (1, 2, 3):
                item = self.table.item(r, c)
                vals.append((item.text().strip() if item else ""))
            out.append(tuple(vals))
        return out
