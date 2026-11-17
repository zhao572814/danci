"""基础词库页：唯一的音标 / 词义 / 例句来源，独立维护。

图片和 PDF 只从这里「挑词」，不会改动这里的内容。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.db import guess_kind, parse_entry_lines
from .common import BatchSupplementDialog, EditWordDialog, run_async

SOURCE_LABEL = {
    "builtin": "内置标准词库", "phrase_builtin": "内置标准短语", "csv": "CSV 导入",
    "json": "JSON 导入", "manual": "手动补录", "ollama": "本机模型",
    "pending": "待补录", "legacy": "旧版迁移",
}

# 表格一次最多渲染这么多行。Qt 的「按内容自适应列宽」在几千行时会扫描全部单元格，
# 导致窗口假死；这里固定列宽 + 分页，几万条词库也能秒开。
PAGE_SIZE = 200


class NewEntryDialog(QDialog):
    def __init__(self, headword: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增基础词库词条")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.headword_edit = QLineEdit(headword)
        self.headword_edit.setPlaceholderText("单词（apple）或短语（take part in）")
        self.phonetic_edit = QLineEdit()
        self.phonetic_edit.setPlaceholderText("短语可留空，例如 /ˈæp.əl/")
        self.translation_edit = QLineEdit()
        self.example_edit = QPlainTextEdit()
        self.example_edit.setFixedHeight(80)
        form.addRow("词条", self.headword_edit)
        form.addRow("音标", self.phonetic_edit)
        form.addRow("释义", self.translation_edit)
        form.addRow("例句", self.example_edit)
        layout.addLayout(form)
        tip = QLabel("含空格的词条会自动归为「短语」，之后上传的文本里会自动整段识别出来。")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("加入基础词库")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (
            self.headword_edit.text().strip(),
            self.phonetic_edit.text().strip(),
            self.translation_edit.text().strip(),
            self.example_edit.toPlainText().strip(),
        )


class ImportEntriesDialog(QDialog):
    """往基础词库加单词 / 短语的统一入口：粘贴清单，或选一个词库文件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入词条到基础词库")
        self.resize(660, 560)
        layout = QVBoxLayout(self)

        tip = QLabel(
            "以后想往基础词库放单词或短语，就用这里。两种方式可以同时用：\n"
            "① 每行一条，含空格的自动识别为短语：\n"
            "    apple        take part in        once in a blue moon\n"
            "② 带音标 / 中文释义也行：apple /ˈæp.əl/ n. 苹果  ·  apple, 苹果\n"
            "    或用 Tab 分四列：词条 / 音标 / 释义 / 例句\n"
            "③ 也可以选一个词库文件：CSV、JSON 或 txt 清单。"
        )
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "在这里粘贴词条清单，一行一条……\n"
            "apple\n"
            "take part in\n"
            "serendipity /ˌser.ənˈdɪp.ə.ti/ n. 意外发现美好事物的运气\n"
            "look forward to, 期待；盼望"
        )
        self.text_edit.setMinimumHeight(190)
        layout.addWidget(self.text_edit, 1)

        frow = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("或者选择词库文件（CSV / JSON / TXT）")
        fbtn = QPushButton("选择文件…")
        fbtn.clicked.connect(self._choose_file)
        frow.addWidget(self.file_edit, 1)
        frow.addWidget(fbtn)
        layout.addLayout(frow)

        self.overwrite_cb = QCheckBox("同名词条覆盖更新（不勾选则只新增，保留已有内容）")
        self.overwrite_cb.setChecked(True)
        layout.addWidget(self.overwrite_cb)

        self.preview = QLabel("待导入 0 条")
        self.preview.setObjectName("StatChip")
        layout.addWidget(self.preview)
        # 粘贴几万行时不要每改一个字就全量解析，做 180ms 防抖
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._update_preview)
        self.text_edit.textChanged.connect(self._preview_timer.start)
        self._update_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("加入基础词库")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择词库文件", "",
            "词库文件 (*.csv *.json *.txt *.md);;CSV (*.csv);;JSON (*.json);;文本清单 (*.txt *.md);;所有文件 (*.*)",
        )
        if path:
            self.file_edit.setText(path)
            self._update_preview()

    def _update_preview(self):
        items = parse_entry_lines(self.text_edit.toPlainText())
        words = sum(1 for i in items if i["kind"] == "word")
        phrases = len(items) - words
        extra = f" · 另选文件：{os.path.basename(self.file_edit.text())}" if self.file_edit.text() else ""
        self.preview.setText(f"粘贴清单解析出 {len(items)} 条（单词 {words} · 短语 {phrases}）{extra}")

    def values(self):
        return (
            self.text_edit.toPlainText(),
            self.file_edit.text(),
            self.overwrite_cb.isChecked(),
        )


class BaseLibraryPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.rows: list[dict] = []
        self.page = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(10)

        head = QLabel(
            "这里是词条的唯一来源：音标、词义、例句都只在这里维护，默认是空的，由你自己导入。"
            "想加单词或短语，点「导入词条到基础库」批量粘贴或选 CSV / JSON / TXT 文件；"
            "也可以点「植入内置标准词库」用现成的常用词 + 短语。上传图片 / PDF 只会从这里挑词，"
            "不会覆盖任何内容；人工录入的词条会被锁定，本机模型补全也不会改它们。"
        )
        head.setObjectName("Hint")
        head.setWordWrap(True)
        root.addWidget(head)

        line = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索词条或释义")
        self.search_edit.returnPressed.connect(self._on_search)
        line.addWidget(self.search_edit)
        sbtn = QPushButton("搜索")
        sbtn.clicked.connect(self._on_search)
        line.addWidget(sbtn)
        self.filter_cb = QComboBox()
        self.filter_cb.addItem("全部词条", "")
        self.filter_cb.addItem("仅单词", "word")
        self.filter_cb.addItem("仅短语", "phrase")
        self.filter_cb.addItem("仅待补录", "pending")
        self.filter_cb.currentIndexChanged.connect(self._on_filter)
        line.addWidget(self.filter_cb)
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.clicked.connect(self._prev_page)
        line.addWidget(self.prev_btn)
        self.page_label = QLabel("第 1 页")
        line.addWidget(self.page_label)
        self.next_btn = QPushButton("下一页")
        self.next_btn.clicked.connect(self._next_page)
        line.addWidget(self.next_btn)
        line.addStretch(1)
        self.stat = QLabel("")
        self.stat.setObjectName("StatChip")
        line.addWidget(self.stat)
        root.addLayout(line)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["选", "类型", "词条", "音标", "释义", "例句", "来源", "状态"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(lambda r, c: self._edit_rows([r]))
        h = self.table.horizontalHeader()
        # 关键：绝不用 ResizeToContents（会扫描表格里每一个单元格来算列宽，几千行时窗口假死）
        for i in range(8):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        for i, width in ((0, 42), (1, 58), (6, 110), (7, 82)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, width)
        for i in (2, 3, 4, 5):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self.empty_hint = QLabel("")
        self.empty_hint.setObjectName("Notice")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.hide()
        root.addWidget(self.empty_hint)
        root.addWidget(self.table, 1)

        g = QGroupBox("词库维护")
        v = QVBoxLayout(g)
        r1 = QHBoxLayout()
        imp = QPushButton("导入词条到基础库")
        imp.setObjectName("Primary")
        imp.setToolTip("批量粘贴单词/短语清单，或选择 CSV / JSON / TXT 词库文件")
        imp.clicked.connect(self._import_entries)
        r1.addWidget(imp)
        builtin = QPushButton("植入内置标准词库")
        builtin.setToolTip("可选：把程序自带的常用单词 + 常见短语（含音标 / 释义 / 例句）一次性加入基础库，默认不会自动灌")
        builtin.clicked.connect(self._install_builtin)
        r1.addWidget(builtin)
        exp = QPushButton("导出 CSV")
        exp.clicked.connect(self._export_file)
        r1.addWidget(exp)
        add = QPushButton("新增词条")
        add.clicked.connect(self._add_entry)
        r1.addWidget(add)
        enrich = QPushButton("用本机模型补全缺失")
        enrich.clicked.connect(lambda: self.ctx.enrich_async(None))
        r1.addWidget(enrich)
        r1.addStretch(1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        edit = QPushButton("编辑选中")
        edit.clicked.connect(self._edit_selected)
        r2.addWidget(edit)
        supp = QPushButton("批量补录选中的待补录词条")
        supp.clicked.connect(self._supplement_selected)
        r2.addWidget(supp)
        dele = QPushButton("删除选中（未被引用）")
        dele.setObjectName("Warn")
        dele.clicked.connect(self._delete_selected)
        r2.addWidget(dele)
        r2.addStretch(1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        r2.addWidget(refresh)
        v.addLayout(r2)
        note = QLabel(
            "导入支持：粘贴清单（一行一条，含空格即短语）、CSV（word / phonetic / translation / example / kind，"
            "列名也支持中文）、JSON（{\"apple\": {\"phonetic\": \"...\", \"translation\": \"...\", \"example\": \"...\"}}）、"
            "TXT 清单。导入的词条可留空信息，之后在这里编辑或用本机模型补全。"
            "表格每页显示 200 条，用搜索和翻页浏览，不会因为词库大而卡住。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        v.addWidget(note)
        root.addSpacing(6)
        root.addWidget(g)

    # ------------------------------------------------------------------ 数据
    def refresh(self):
        kind = self.filter_cb.currentData() or ""
        source = ""
        if kind == "pending":
            source, kind = "pending", ""
        # 多取一条用来判断有没有下一页，避免为了算总数再查一次全表
        raw = self.ctx.db.list_entries(
            search=self.search_edit.text().strip(), kind=kind, source=source,
            limit=PAGE_SIZE + 1, offset=self.page * PAGE_SIZE,
        )
        self._has_more = len(raw) > PAGE_SIZE
        self.rows = raw[:PAGE_SIZE]
        s = self.ctx.db.entry_stats()
        self.stat.setText(
            f"词条 {s['total']} · 单词 {s['words']} · 短语 {s['phrases']} "
            f"· 待补录 {s['pending']} · 信息完整 {s['complete']}"
        )
        self.page_label.setText(f"第 {self.page + 1} 页 · 本页 {len(self.rows)} 条")
        self.prev_btn.setEnabled(self.page > 0)
        self.next_btn.setEnabled(self._has_more)
        if s["total"] == 0:
            self.empty_hint.setText(
                "基础词库现在是空的：点「导入词条到基础库」粘贴清单或选 CSV / JSON / TXT 文件，"
                "也可以点「植入内置标准词库」用现成的常用词 + 短语。之后上传图片 / PDF 就能从库里挑词。"
            )
        else:
            self.empty_hint.setText(
                "当前筛选或这一页没有词条：换个搜索词，或点「上一页」回到前面。"
            )
        self.empty_hint.setVisible(not self.rows)
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            self._fill_row(i, r)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

    def _on_search(self):
        self.page = 0
        self.refresh()

    def _on_filter(self, *_):
        self.page = 0
        self.refresh()

    def _prev_page(self):
        if self.page > 0:
            self.page -= 1
            self.refresh()

    def _next_page(self):
        if getattr(self, "_has_more", False):
            self.page += 1
            self.refresh()

    def _install_builtin(self):
        from PySide6.QtWidgets import QMessageBox

        n, msg = self.ctx.db.install_builtin_library()
        if n == 0:
            QMessageBox.information(
                self, "内置标准词库", "内置词条已经全部在基础词库里了，没有需要新增的内容。"
            )
            return
        self.ctx.status(msg + "；这些词条不会被图片 / PDF 导入覆盖")
        self.page = 0
        self.ctx.refresh_views()

    def _fill_row(self, i: int, r: dict):
        chk = QTableWidgetItem("")
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Unchecked)
        chk.setData(Qt.ItemDataRole.UserRole, r["id"])
        self.table.setItem(i, 0, chk)
        self.table.setItem(i, 1, QTableWidgetItem("短语" if r["kind"] == "phrase" else "单词"))
        self.table.setItem(i, 2, QTableWidgetItem(r["headword"]))
        self.table.setItem(i, 3, QTableWidgetItem(r["phonetic"] or "—"))
        self.table.setItem(i, 4, QTableWidgetItem(r["translation"] or "—"))
        ex = (r["example"] or "—").replace("\n", " ")
        self.table.setItem(i, 5, QTableWidgetItem(ex[:60] + ("…" if len(ex) > 60 else "")))
        self.table.setItem(i, 6, QTableWidgetItem(SOURCE_LABEL.get(r["source"], r["source"])))
        if r["locked"]:
            state = "已锁定"
        elif r["source"] == "pending":
            state = "待补录"
        elif not r["translation"] or not r["example"]:
            state = "待补全"
        else:
            state = "完整"
        self.table.setItem(i, 7, QTableWidgetItem(state))

    def _checked_rows(self) -> list[int]:
        return [
            i for i in range(self.table.rowCount())
            if self.table.item(i, 0)
            and self.table.item(i, 0).checkState() == Qt.CheckState.Checked
        ]

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.table.selectedIndexes()})

    # ------------------------------------------------------------------ 操作
    def _import_entries(self):
        if getattr(self, "_importing", False):
            self.ctx.status("正在导入上一批，请稍候…")
            return
        dlg = ImportEntriesDialog(self)
        if not dlg.exec():
            return
        text, path, overwrite = dlg.values()
        if not text.strip() and not path:
            self.ctx.status("没有要导入的内容（可以粘贴清单或选择文件）")
            return
        self._importing = True
        self.ctx.status("正在导入基础词库…（大词库在后台跑，界面不会卡住）")

        def work(emit):
            msgs = []
            if text.strip():
                _, msg = self.ctx.db.import_text_entries(
                    text, source="manual", overwrite=overwrite
                )
                msgs.append(msg)
            if path:
                _, msg = self.ctx.dictionary.import_dict_file(
                    path, "merge" if overwrite else "fill"
                )
                msgs.append(f"{os.path.basename(path)}：{msg}")
            return msgs

        def done(msgs):
            self._importing = False
            self.ctx.status("；".join(msgs) + "；人工导入的词条已锁定")
            self.page = 0
            self.ctx.refresh_views()

        def failed(msg):
            self._importing = False
            self.ctx.status(f"导入失败：{msg}")

        run_async(work, on_done=done, on_error=failed)

    def _export_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出基础词库", "基础词库.csv", "CSV (*.csv)"
        )
        if not path:
            return
        n, msg = self.ctx.dictionary.export_dict_csv(path)
        self.ctx.status(msg)

    def _add_entry(self):
        dlg = NewEntryDialog(self.search_edit.text().strip(), self)
        if not dlg.exec():
            return
        hw, ph, tr, ex = dlg.values()
        if not hw:
            return
        self.ctx.db.upsert_entry(
            hw, ph, tr, ex, kind=guess_kind(hw), source="manual", locked=1
        )
        self.ctx.status(f"「{hw}」已加入基础词库")
        self.page = 0
        self.ctx.refresh_views()

    def _edit_selected(self):
        rows = self._checked_rows() or self._selected_rows()
        self._edit_rows(rows)

    def _edit_rows(self, rows: list[int]):
        rows = [r for r in rows if 0 <= r < len(self.rows)]
        if not rows:
            self.ctx.status("请先勾选或选中要编辑的词条")
            return
        if len(rows) > 30:
            self.ctx.status("一次最多批量编辑 30 条，请分批勾选（词条多时建议导入 CSV 词库文件）")
            return
        if len(rows) == 1:
            r = self.rows[rows[0]]
            dlg = EditWordDialog({
                "word": r["headword"], "phonetic": r["phonetic"],
                "translation": r["translation"], "example": r["example"],
            }, self)
            if not dlg.exec():
                return
            ph, tr, ex = dlg.values()
            self.ctx.db.update_entry(r["id"], ph, tr, ex, "manual")
        else:
            subset = [self.rows[r] for r in rows]
            dlg = BatchSupplementDialog(subset, self)
            dlg.setWindowTitle("批量编辑基础词库词条")
            if not dlg.exec():
                return
            for r, (ph, tr, ex) in zip(subset, dlg.values()):
                self.ctx.db.update_entry(r["id"], ph, tr, ex, "manual")
        self.ctx.status(f"已更新 {len(rows)} 条词条（人工内容已锁定）")
        self.ctx.refresh_views()

    def _supplement_selected(self):
        rows = [r for r in (self._checked_rows() or self._selected_rows())
                if 0 <= r < len(self.rows) and self.rows[r]["source"] == "pending"]
        if not rows:
            self.ctx.status("请勾选状态为「待补录」的词条")
            return
        self._edit_rows(rows)

    def _delete_selected(self):
        rows = self._checked_rows() or self._selected_rows()
        if not rows:
            self.ctx.status("请先勾选或选中要删除的词条")
            return
        ok, blocked, names = 0, 0, []
        for i in rows:
            if not (0 <= i < len(self.rows)):
                continue
            r = self.rows[i]
            success, msg = self.ctx.db.delete_entry(r["id"])
            if success:
                ok += 1
            else:
                blocked += 1
                names.append(r["headword"])
        self.ctx.status(
            f"已从基础词库删除 {ok} 条；{blocked} 条因已被背诵词单引用而保留"
            + (f"（{'、'.join(names[:5])}）" if names else "")
        )
        if self.page > 0 and ok >= len(self.rows):
            self.page -= 1
        self.ctx.refresh_views()
