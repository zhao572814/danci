"""导入页：文档 / 图片 / 粘贴文本 → 从基础词库里「挑」出条目 → 建一个背诵批次。

不会改动基础词库：已收录的只建立引用；库里没有的默认不导入，可当场补录或合成短语。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import extract, matcher
from .common import BatchSupplementDialog, EditWordDialog, run_async

FILE_FILTER = (
    "支持的文件 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.pdf *.docx "
    "*.txt *.md *.csv *.log);;图片 (*.png *.jpg *.jpeg *.webp *.bmp);;"
    "PDF (*.pdf);;Word (*.docx);;文本 (*.txt *.md *.csv *.log);;所有文件 (*.*)"
)

SOURCE_LABEL = {
    "builtin": "内置词", "phrase_builtin": "内置短语", "csv": "CSV 导入",
    "json": "JSON 导入", "manual": "手动补录", "ollama": "本机模型",
    "pending": "待补录", "legacy": "旧版迁移",
}


class ImportPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.result: extract.ExtractionResult | None = None
        self.items: list[dict] = []
        self._busy = False
        self._build()

    def _build(self):
        # 内容较多，整页可滚动，保证预览表格在任何窗口高度下都足够高
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        head = QLabel(
            "这里只做一件事：从 txt / 文本里挑出「要背哪些词」。挑出来的词音标、释义、例句"
            "一律取自基础词库（信息不全时以基础词库为准），本页不会改动它。"
            "推荐用下方「粘贴文本」或直接选 txt 文件；要往基础库加词条请到「基础词库」页。"
        )
        head.setWordWrap(True)
        head.setObjectName("SubTitle")
        root.addWidget(head)

        # ---- 文件导入
        g1 = QGroupBox("① 选择文件（推荐 txt 文本，也支持图片 / PDF / Word）")
        v1 = QVBoxLayout(g1)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("点「浏览」选择一张单词表截图、PDF、Word 或 txt 文本…")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        self.scan_btn = QPushButton("识别并挑选词条")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(self._scan_file)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        row.addWidget(self.scan_btn)
        v1.addLayout(row)

        row2 = QHBoxLayout()
        self.stopword_cb = QCheckBox("过滤常见虚词")
        # 默认关闭：how / the 这类词也常是需要背的，误删案例太多；
        # 用户想精简时再自己勾上。手打/粘贴的词条永远不过滤。
        self.stopword_cb.setChecked(False)
        self.stopword_cb.setToolTip(
            "默认关闭。勾选后会丢掉 how / the / of 等虚词；"
            "手打或粘贴的词条不受此开关影响，全部保留。"
        )
        self.phrase_cb = QCheckBox("识别短语")
        self.phrase_cb.setChecked(True)
        self.strict_cb = QCheckBox("只导入基础库已收录的")
        self.strict_cb.setChecked(True)
        self.strict_cb.setToolTip("关闭后，库里没有的条目也能进待背列表，但信息留空并在基础库标为「待补录」")
        self.strict_cb.stateChanged.connect(lambda _: self._fill_preview() if self.items else None)
        self.batch_name = QLineEdit()
        self.batch_name.setPlaceholderText("批次名称（留空自动用文件名）")
        for w in (self.stopword_cb, self.phrase_cb, self.strict_cb):
            row2.addWidget(w)
        row2.addWidget(self.batch_name, 1)
        v1.addLayout(row2)
        self.notice = QLabel("")
        self.notice.setObjectName("Notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        v1.addWidget(self.notice)
        root.addWidget(g1)

        # ---- 粘贴文本
        g2 = QGroupBox("② 或者直接粘贴 / 手打词条（空格、逗号、换行分隔都可以）")
        v2 = QVBoxLayout(g2)
        self.paste_edit = QPlainTextEdit()
        self.paste_edit.setPlaceholderText(
            "例如：apple  take part in  look forward to  diligence obscure"
        )
        self.paste_edit.setFixedHeight(62)
        v2.addWidget(self.paste_edit)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        paste_btn = QPushButton("从文本挑选词条")
        paste_btn.clicked.connect(self._from_paste)
        row3.addWidget(paste_btn)
        v2.addLayout(row3)
        root.addWidget(g2)

        # ---- 预览
        g3 = QGroupBox("③ 确认要背的条目（默认只挑基础库已收录的）")
        v3 = QVBoxLayout(g3)
        tools = QHBoxLayout()
        self.count_label = QLabel("尚未识别")
        self.count_label.setObjectName("Hint")
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        for text, slot in (("全选", lambda: self._check_all(True)),
                           ("全不选", lambda: self._check_all(False)),
                           ("反选", self._invert)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            tools.addWidget(b)
        v3.addLayout(tools)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["导入", "#", "类型", "词条", "基础库状态 / 音标 · 释义"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellChanged.connect(self._update_count)
        h = self.table.horizontalHeader()
        # 不能用 ResizeToContents：识别出一千多个词时会扫描整张表算列宽，窗口假死
        for i in range(5):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        for i, width in ((0, 48), (1, 52), (2, 64), (3, 240)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, width)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(250)
        v3.addWidget(self.table, 1)

        row4 = QHBoxLayout()
        add_btn = QPushButton("补录选中到基础库")
        add_btn.setObjectName("Warn")
        add_btn.setToolTip("把库里没有的条目手动补上音标 / 释义 / 例句，之后即可导入")
        add_btn.clicked.connect(self._supplement_checked)
        row4.addWidget(add_btn)
        phrase_btn = QPushButton("把选中相邻词合成短语")
        phrase_btn.setToolTip("选中连续的几个单词，合成为短语并补录进基础库，之后文本里会自动识别")
        phrase_btn.clicked.connect(self._merge_phrase)
        row4.addWidget(phrase_btn)
        row4.addStretch(1)
        self.import_btn = QPushButton("导入选中的条目")
        self.import_btn.setObjectName("Primary")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._do_import)
        row4.addWidget(self.import_btn)
        v3.addLayout(row4)
        root.addWidget(g3, 1)

        # ---- 历史批次
        g4 = QGroupBox("导入记录（背诵页可以按批次单独背）")
        v4 = QVBoxLayout(g4)
        self.batch_table = QTableWidget(0, 4)
        self.batch_table.setHorizontalHeaderLabels(["批次", "来源", "词数", "已背"])
        self.batch_table.verticalHeader().setVisible(False)
        self.batch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        bh = self.batch_table.horizontalHeader()
        bh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        bh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, width in ((2, 80), (3, 80)):
            bh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.batch_table.setColumnWidth(i, width)
        self.batch_table.setMaximumHeight(110)
        v4.addWidget(self.batch_table)
        v4.addSpacing(6)
        row5 = QHBoxLayout()
        row5.addStretch(1)
        del_btn = QPushButton("删除选中批次")
        del_btn.setObjectName("Warn")
        del_btn.clicked.connect(self._delete_batch)
        row5.addWidget(del_btn)
        v4.addLayout(row5)
        root.addWidget(g4)

        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)

    # ------------------------------------------------------------------ 识别
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", FILE_FILTER)
        if path:
            self.path_edit.setText(path)
            if not self.batch_name.text().strip():
                self.batch_name.setText(os.path.splitext(os.path.basename(path))[0])
            self._scan_file()

    def _set_busy(self, busy: bool, msg: str = ""):
        self._busy = busy
        self.scan_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy and bool(self.items))
        if msg:
            self.ctx.status(msg)

    def _scan_file(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            self.ctx.status("请先选择一个存在的文件")
            return
        use_stop = self.stopword_cb.isChecked()
        self._set_busy(True, "正在本机识别文字…首次加载离线 OCR 模型约需几秒")
        self.notice.hide()
        run_async(
            lambda emit: extract.extract_from_file(path, filter_stopwords=use_stop),
            on_done=self._on_scanned, on_error=self._on_failed,
        )

    def _on_failed(self, msg: str):
        self._set_busy(False)
        self.notice.setText(f"识别失败：{msg}。可以改用下方「粘贴文本」。")
        self.notice.show()
        self.ctx.status("识别失败，可改用粘贴文本")

    def _on_scanned(self, res):
        self._set_busy(False)
        self.result = res
        self._analyze(res)
        if res.notice:
            self.notice.setText(res.notice)
            self.notice.show()
        if not self.batch_name.text().strip() and res.source:
            self.batch_name.setText(os.path.splitext(os.path.basename(res.source))[0])

    def _from_paste(self):
        # 手打/粘贴的词条：用户明确输入的就是要背的，一律不过滤虚词。
        res = extract.extract_from_pasted(self.paste_edit.toPlainText(), False)
        self.result = res
        self._analyze(res, filter_stopwords=False, min_len=1)
        if not self.batch_name.text().strip():
            self.batch_name.setText("手动录入")

    def _analyze(
        self, res: extract.ExtractionResult,
        filter_stopwords: bool | None = None, min_len: int | None = None,
    ):
        """核心：只做匹配，不写基础词库。"""
        if filter_stopwords is None:
            filter_stopwords = self.stopword_cb.isChecked()
        kwargs = {}
        if min_len is not None:
            kwargs["min_len"] = min_len
        self.items = matcher.detect_items(
            res.text,
            self.ctx.db.phrase_headwords(),
            filter_stopwords=filter_stopwords,
            detect_phrases=self.phrase_cb.isChecked(),
            **kwargs,
        )
        self.items = matcher.annotate(self.items, self.ctx.db.lookup_entry)
        self._fill_preview()
        phr = sum(1 for i in self.items if i["kind"] == "phrase")
        ok = sum(1 for i in self.items if i["in_library"])
        self.ctx.status(
            f"识别到 {len(self.items)} 条（短语 {phr} 条），其中 {ok} 条基础库已收录；"
            "基础词库未做任何改动"
        )

    # ------------------------------------------------------------------ 预览
    def _fill_preview(self, keep_checked: set[str] | None = None):
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        strict = self.strict_cb.isChecked()
        for i, it in enumerate(self.items):
            chk = QTableWidgetItem("")
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            default_checked = (
                it["headword"] in keep_checked if keep_checked is not None
                else (it["in_library"] or not strict)
            )
            chk.setCheckState(
                Qt.CheckState.Checked if default_checked else Qt.CheckState.Unchecked
            )
            self.table.setItem(i, 0, chk)
            num = QTableWidgetItem(str(i + 1))
            num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 1, num)
            kind = QTableWidgetItem("短语" if it["kind"] == "phrase" else "单词")
            self.table.setItem(i, 2, kind)
            self.table.setItem(i, 3, QTableWidgetItem(it["headword"]))
            if it["in_library"]:
                summary = "✓ 已收录"
                extra = (it.get("phonetic") or "") + " " + (it.get("translation") or "")
                if extra.strip():
                    summary += " · " + extra.strip()[:46]
            elif it.get("pending"):
                summary = "○ 待补录（信息为空）"
            else:
                summary = "○ 基础库里没有 · 需补录"
            self.table.setItem(i, 4, QTableWidgetItem(summary))
            if not it["in_library"]:
                self.table.item(i, 4).setForeground(Qt.GlobalColor.darkYellow)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self._update_count()

    def _update_count(self, *_):
        checked = self._checked_rows()
        ok = sum(1 for i in checked if self.items[i]["in_library"])
        todo = len(checked) - ok
        self.count_label.setText(
            f"共 {len(self.items)} 条 · 已勾选 {len(checked)} 条"
            + (f"（其中 {todo} 条库里没有）" if todo else "（全部已收录）")
        )
        self.import_btn.setText(f"导入选中的 {len(checked)} 条" if checked else "导入选中的条目")
        self.import_btn.setEnabled(bool(checked) and not self._busy)

    def _checked_rows(self) -> list[int]:
        return [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 0)
            and self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]

    def _selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self.table.selectedIndexes()})

    def _check_all(self, value: bool):
        state = Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(state)
        self._update_count()

    def _invert(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            it.setCheckState(
                Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self._update_count()

    # ------------------------------------------------------------------ 补录
    def _supplement_checked(self):
        rows = self._checked_rows() or self._selected_rows()
        targets = [r for r in rows if r < len(self.items) and not self.items[r]["in_library"]]
        if not targets:
            self.ctx.status("请勾选或选中「库里没有 / 待补录」的条目")
            return
        pending = [self.items[r] for r in targets]
        if len(pending) > 50:
            self.ctx.status("一次最多补录 50 条；词条很多时建议到「基础词库」页导入 CSV 词库文件")
            return
        dlg = BatchSupplementDialog(pending, self)
        if not dlg.exec():
            return
        for it, (ph, tr, ex) in zip(pending, dlg.values()):
            self.ctx.db.upsert_entry(
                it["headword"], ph, tr, ex, kind=it["kind"], source="manual", locked=1
            )
        self.ctx.status(f"已把 {len(pending)} 条补录进基础词库（人工词条会被锁定，自动补全不会覆盖）")
        self._refresh_library_state()

    def _merge_phrase(self):
        rows = self._selected_rows() or self._checked_rows()
        if len(rows) < 2:
            self.ctx.status("请先选中至少两个相邻的单词行（用鼠标拖选或勾选）")
            return
        if rows != list(range(rows[0], rows[-1] + 1)):
            self.ctx.status("选中的单词必须相邻，才能合成短语")
            return
        phrase = matcher.join_phrase([self.items[r]["headword"] for r in rows])
        if " " not in phrase:
            self.ctx.status("没有合成出有效的短语")
            return
        dlg = EditWordDialog({
            "word": phrase, "phonetic": "", "translation": "", "example": ""
        }, self)
        dlg.setWindowTitle("合成短语并补录到基础词库")
        if not dlg.exec():
            return
        ph, tr, ex = dlg.values()
        self.ctx.db.upsert_entry(
            phrase, ph, tr, ex, kind="phrase", source="manual", locked=1
        )
        self.ctx.status(f"短语「{phrase}」已加入基础词库，之后会自动从文本里识别")
        self._refresh_library_state()

    def _refresh_library_state(self):
        """补录后重新标注状态（不重新识别文本，避免丢失用户勾选）。"""
        keep = {self.items[r]["headword"] for r in self._checked_rows()}
        self.items = matcher.annotate(self.items, self.ctx.db.lookup_entry)
        self._fill_preview(keep_checked=keep)
        self.ctx.refresh_views()

    # ------------------------------------------------------------------ 导入
    def _do_import(self):
        rows = self._checked_rows()
        if not rows:
            return
        selected = [self.items[r] for r in rows]
        name = self.batch_name.text().strip() or "未命名批次"
        source = (self.result.source if self.result and self.result.source else "粘贴文本")
        allow_pending = not self.strict_cb.isChecked()
        if self.strict_cb.isChecked():
            selected = [i for i in selected if i["in_library"]]
        bid, ids, new, skipped = self.ctx.db.import_items(
            selected, name, source, allow_pending=allow_pending
        )
        self.ctx.status(
            f"已从基础词库挑出 {len(ids)} 条加入批次「{name}」"
            + (f"，跳过 {skipped} 条未收录" if skipped else "")
            + "；基础词库没有被覆盖"
        )
        self.ctx.refresh_views()
        self.items = []
        self.table.setRowCount(0)
        self.count_label.setText("尚未识别")
        self.import_btn.setEnabled(False)
        self.paste_edit.clear()
        self.batch_name.clear()

    def _delete_batch(self):
        row = self.batch_table.currentRow()
        if row < 0:
            self.ctx.status("请先在导入记录里选中一行")
            return
        bid = int(self.batch_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        name = self.batch_table.item(row, 0).text()
        self.ctx.db.delete_batch(bid)
        self.ctx.status(f"已删除批次「{name}」（词条和词库记录都保留）")
        self.ctx.refresh_views()

    def refresh(self):
        rows = self.ctx.db.list_batches()
        self.batch_table.setRowCount(len(rows))
        for i, b in enumerate(rows):
            it = QTableWidgetItem(b["name"])
            it.setData(Qt.ItemDataRole.UserRole, b["id"])
            self.batch_table.setItem(i, 0, it)
            self.batch_table.setItem(i, 1, QTableWidgetItem(os.path.basename(b["source"] or "")))
            self.batch_table.setItem(i, 2, QTableWidgetItem(str(b["total"])))
            self.batch_table.setItem(i, 3, QTableWidgetItem(str(b["mastered"])))
