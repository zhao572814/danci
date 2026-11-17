"""词库页：已背诵词库（含背诵年月日、可移出）与重点易错词库。"""

from __future__ import annotations

import csv
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.db import today
from ..core.models import LIST_HARD, LIST_MASTERED, SCOPE_HARD, SCOPE_MASTERED, WordRow
from .common import EditWordDialog, run_async

# 与基础词库同理：分页 + 固定列宽。这里每行还有按钮控件，单页行数更小一点
PAGE_SIZE = 100


class LibraryPage(QWidget):
    def __init__(self, ctx, list_name: str):
        super().__init__()
        self.ctx = ctx
        self.list_name = list_name
        self.is_mastered = list_name == LIST_MASTERED
        self.search = ""
        self.page = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(10)

        title = "已背诵词库" if self.is_mastered else "重点 / 易错词库"
        desc = (
            "这里记录每个单词是哪天背过的；点「删除 / 移出」只取消已背标记，"
            "单词会重新回到待背列表（基础词库里的词条不动）。"
            if self.is_mastered else
            "难背的单词放这里集中攻。一个单词可以同时出现在已背库和待背列表里。"
        )
        head = QLabel(desc)
        head.setObjectName("Hint")
        head.setWordWrap(True)
        root.addWidget(head)

        line = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索单词或释义")
        self.search_edit.returnPressed.connect(self._apply_search)
        line.addWidget(self.search_edit)
        sbtn = QPushButton("搜索")
        sbtn.clicked.connect(self._apply_search)
        line.addWidget(sbtn)
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.clicked.connect(self._prev_page)
        line.addWidget(self.prev_btn)
        self.page_label = QLabel("第 1 页")
        line.addWidget(self.page_label)
        self.next_btn = QPushButton("下一页")
        self.next_btn.clicked.connect(self._next_page)
        line.addWidget(self.next_btn)
        line.addSpacing(12)
        self.stat = QLabel("")
        self.stat.setObjectName("StatChip")
        line.addWidget(self.stat)
        line.addStretch(1)
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export_csv)
        line.addWidget(export)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        line.addWidget(refresh)
        root.addLayout(line)

        headers = ["选", "单词", "♪", "音标", "释义",
                   "背诵日期" if self.is_mastered else "加入日期",
                   "次数" if self.is_mastered else "已背?", "熟练度", "操作"]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        h = self.table.horizontalHeader()
        for i in range(len(headers)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        for i, width in ((0, 42), (2, 44), (5, 108), (6, 60), (7, 88), (8, 138)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, width)
        for i in (1, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.remove_btn = QPushButton("删除 / 移出勾选")
        self.remove_btn.setObjectName("Warn")
        self.remove_btn.clicked.connect(self._remove_checked)
        bottom.addWidget(self.remove_btn)
        if not self.is_mastered:
            mark = QPushButton("把勾选的划入已背诵词库")
            mark.setObjectName("Primary")
            mark.clicked.connect(self._mark_checked)
            bottom.addWidget(mark)
        root.addLayout(bottom)
        self.title = title

    # ------------------------------------------------------------------ 数据
    def refresh(self):
        scope = SCOPE_MASTERED if self.is_mastered else SCOPE_HARD
        raw = self.ctx.db.list_words(
            scope=scope, search=self.search,
            limit=PAGE_SIZE + 1, offset=self.page * PAGE_SIZE,
        )
        self._has_more = len(raw) > PAGE_SIZE
        rows = raw[:PAGE_SIZE]
        c = self.ctx.db.counts()
        if self.is_mastered:
            self.stat.setText(
                f"已背 {c['mastered']} 词 · 今日新增 {c['today_mastered']} 词 · 词库总量 {c['total']} 词"
            )
        else:
            self.stat.setText(f"重点 {c['hard']} 词 · 词库总量 {c['total']} 词")
        self.page_label.setText(f"第 {self.page + 1} 页 · 本页 {len(rows)} 条")
        self.prev_btn.setEnabled(self.page > 0)
        self.next_btn.setEnabled(self._has_more)
        history = (
            self.ctx.db.mark_history_counts([r["id"] for r in rows], LIST_MASTERED)
            if self.is_mastered else {}
        )
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._fill_row(i, r, history.get(r["id"], 0))
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self.remove_btn.setText(f"删除 / 移出勾选（{self.title}）")

    def _prev_page(self):
        if self.page > 0:
            self.page -= 1
            self.refresh()

    def _next_page(self):
        if getattr(self, "_has_more", False):
            self.page += 1
            self.refresh()

    def _fill_row(self, i: int, r: dict, times: int = 0):
        chk = QTableWidgetItem("")
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Unchecked)
        chk.setData(Qt.ItemDataRole.UserRole, r["id"])
        self.table.setItem(i, 0, chk)

        self.table.setItem(i, 1, QTableWidgetItem(r["word"]))

        speak = QPushButton("♪")
        speak.setToolTip("朗读")
        speak.setFixedSize(34, 26)
        speak.setStyleSheet("padding: 0; font-size: 14px;")
        speak.clicked.connect(lambda _, w=r["word"]: self._speak(w))
        self.table.setCellWidget(i, 2, speak)
        self.table.setItem(i, 3, QTableWidgetItem(r.get("phonetic") or ""))
        self.table.setItem(i, 4, QTableWidgetItem(r.get("translation") or ""))
        date = r.get("mastered_on") if self.is_mastered else r.get("hard_on")
        self.table.setItem(i, 5, QTableWidgetItem(date or ""))
        if self.is_mastered:
            self.table.setItem(i, 6, QTableWidgetItem(str(times)))
        else:
            self.table.setItem(i, 6, QTableWidgetItem("是" if r.get("mastered_on") else "否"))
        self.table.setItem(i, 7, QTableWidgetItem(WordRow.from_dict(r).familiarity_label))

        ops = QWidget()
        ops.setMinimumWidth(126)
        lay = QHBoxLayout(ops)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)
        btn_style = "padding: 0; font-size: 12px;"
        rm = QPushButton("删除")
        rm.setFixedSize(56, 26)
        rm.setStyleSheet(btn_style)
        rm.clicked.connect(lambda _, wid=r["id"], w=r["word"]: self._remove_one(wid, w))
        lay.addWidget(rm)
        edit = QPushButton("编辑")
        edit.setFixedSize(56, 26)
        edit.setStyleSheet(btn_style)
        edit.clicked.connect(lambda _, row=r: self._edit(row))
        lay.addWidget(edit)
        lay.addStretch(1)
        self.table.setCellWidget(i, 8, ops)

    def _apply_search(self):
        self.search = self.search_edit.text().strip()
        self.page = 0
        self.refresh()

    def _checked_ids(self) -> list[int]:
        ids = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                ids.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return ids

    # ------------------------------------------------------------------ 操作
    def _speak(self, word: str):
        run_async(
            lambda emit: self.ctx.speech.speak(word),
            on_done=lambda res: self.ctx.status(
                f"♪ {word}" if res[0] else f"朗读失败：{res[1]}"
            ),
        )

    def _remove_one(self, wid: int, word: str):
        self._remove([wid], word)

    def _remove_checked(self):
        ids = self._checked_ids()
        if not ids:
            self.ctx.status("请先勾选要移出的单词")
            return
        self._remove(ids, f"{len(ids)} 个单词")

    def _remove(self, ids: list[int], label: str):
        if self.is_mastered:
            self.ctx.db.unmark_mastered(ids)
            self.ctx.status(f"已把 {label} 移出已背诵词库，它们重新回到待背列表；重点库标记保留")
        else:
            self.ctx.db.unmark_hard(ids)
            self.ctx.status(f"已把 {label} 移出重点易错词库；单词本身与其它库不受影响")
        self.page = 0
        self.ctx.refresh_views()

    def _mark_checked(self):
        ids = self._checked_ids()
        if not ids:
            self.ctx.status("请先勾选已背过的重点单词")
            return
        self.ctx.db.mark_mastered(ids)
        self.ctx.status(f"已把 {len(ids)} 个单词划入已背诵词库（{today()}），它们仍保留在重点词库")
        self.page = 0
        self.ctx.refresh_views()

    def _edit(self, row: dict):
        dlg = EditWordDialog(row, self)
        if dlg.exec():
            ph, tr, ex = dlg.values()
            self.ctx.db.update_word_info(row["id"], ph, tr, ex, "manual")
            self.ctx.status(f"已保存「{row['word']}」的词条信息")
            self.refresh()

    def _on_double_click(self, row: int, col: int):
        it = self.table.item(row, 0)
        if not it:
            return
        wid = int(it.data(Qt.ItemDataRole.UserRole))
        matches = [r for r in self.ctx.db.list_words(
            scope=SCOPE_MASTERED if self.is_mastered else SCOPE_HARD,
            search=f"#{wid}",
        )]
        if matches:
            self._edit(matches[0])

    def _export_csv(self):
        default = f"{self.title}_{today()}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出词库", default, "CSV (*.csv)")
        if not path:
            return
        scope = SCOPE_MASTERED if self.is_mastered else SCOPE_HARD
        rows = self.ctx.db.list_words(scope=scope, search=self.search)
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["word", "phonetic", "translation", "example", "date", "familiarity"])
                for r in rows:
                    w.writerow([
                        r["word"], r["phonetic"], r["translation"], r["example"],
                        r.get("mastered_on") or r.get("hard_on") or "", r["familiarity"],
                    ])
            self.ctx.status(f"已导出 {len(rows)} 个单词到 {os.path.basename(path)}")
        except Exception as e:
            self.ctx.status(f"导出失败：{e}")
