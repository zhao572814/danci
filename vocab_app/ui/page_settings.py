"""设置页：离线发音、本机词条补全、自定义词典、数据文件与说明。"""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.db import DATA_DIR
from ..core.dictionary import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from .common import run_async


class SettingsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build()
        self._load()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(10)

        # ---- 发音
        g1 = QGroupBox("① 单词发音（系统自带语音，完全离线）")
        v1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("语音："))
        self.voice_cb = QComboBox()
        self.voice_cb.setMinimumWidth(280)
        self.voice_cb.currentIndexChanged.connect(self._save_voice)
        r1.addWidget(self.voice_cb, 1)
        r1.addWidget(QLabel("语速："))
        self.rate = QSpinBox()
        self.rate.setRange(-5, 5)
        self.rate.valueChanged.connect(lambda v: self.ctx.db.set_setting("tts_rate", str(v)))
        r1.addWidget(self.rate)
        root_hint = QLabel("（0 为正常，负数更慢）")
        root_hint.setObjectName("Hint")
        r1.addWidget(root_hint)
        r1.addStretch(1)
        v1.addLayout(r1)
        r2 = QHBoxLayout()
        self.test_word = QLineEdit("pronunciation")
        self.test_word.setMaximumWidth(220)
        r2.addWidget(self.test_word)
        tbtn = QPushButton("试听")
        tbtn.clicked.connect(self._test_speak)
        r2.addWidget(tbtn)
        r2.addStretch(1)
        self.voice_note = QLabel("")
        self.voice_note.setObjectName("Hint")
        r2.addWidget(self.voice_note)
        v1.addLayout(r2)
        root.addWidget(g1)

        # ---- 词条补全
        g2 = QGroupBox("② 基础词库补全（可选，只连本机 127.0.0.1）")
        v2 = QVBoxLayout(g2)
        note = QLabel(
            "基础词库默认是空的：可以自己导入 CSV / JSON / TXT，或在基础词库页点「植入内置标准词库」。"
            "只有你在基础词库页主动点「补全缺失」时，才会让本机 Ollama 填空缺词条；"
            "人工录入和导入的词条会被锁定，不会被改写。上传图片 / PDF 永远不会触发这里的写入。"
            "本机回环地址，不联网、不上传，不装 Ollama 也完全不影响使用。"
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        v2.addWidget(note)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("地址："))
        self.url_edit = QLineEdit()
        self.url_edit.editingFinished.connect(
            lambda: self.ctx.db.set_setting("ollama_url", self.url_edit.text().strip())
        )
        r3.addWidget(self.url_edit, 1)
        r3.addWidget(QLabel("模型："))
        self.model_edit = QLineEdit()
        self.model_edit.setMaximumWidth(190)
        self.model_edit.editingFinished.connect(
            lambda: self.ctx.db.set_setting("ollama_model", self.model_edit.text().strip())
        )
        r3.addWidget(self.model_edit)
        v2.addLayout(r3)
        r4 = QHBoxLayout()
        check = QPushButton("测试本机连接")
        check.clicked.connect(self._test_ollama)
        r4.addWidget(check)
        self.enrich_btn = QPushButton("补全基础词库缺失词条")
        self.enrich_btn.setObjectName("Primary")
        self.enrich_btn.clicked.connect(lambda: self.ctx.enrich_async(None))
        r4.addWidget(self.enrich_btn)
        csv_btn = QPushButton("导入词库文件（CSV / JSON / TXT）")
        csv_btn.clicked.connect(self._import_csv)
        r4.addWidget(csv_btn)
        r4.addStretch(1)
        self.ollama_status = QLabel("")
        self.ollama_status.setObjectName("Hint")
        r4.addWidget(self.ollama_status)
        v2.addLayout(r4)
        root.addWidget(g2)

        # ---- 数据
        g3 = QGroupBox("③ 数据文件（全部在你自己的电脑上）")
        v3 = QVBoxLayout(g3)
        self.db_label = QLabel(f"数据库：{self.ctx.db.path}")
        v3.addWidget(self.db_label)
        v3.addWidget(QLabel(f"发音缓存：{os.path.join(DATA_DIR, 'audio')}"))
        r5 = QHBoxLayout()
        open_btn = QPushButton("打开数据目录")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(DATA_DIR)))
        r5.addWidget(open_btn)
        clear_btn = QPushButton("清空学习词单（保留基础词库）")
        clear_btn.setObjectName("Warn")
        clear_btn.setToolTip("按新流程重新开始时用：清掉旧的导入批次和背诵记录，词条主档不动")
        clear_btn.clicked.connect(self._clear_study)
        r5.addWidget(clear_btn)
        clear_all = QPushButton("清空全部数据（含基础词库）")
        clear_all.setObjectName("Warn")
        clear_all.setToolTip("恢复空白词库：所有词条、批次、背诵记录都删掉；清空前自动备份数据库")
        clear_all.clicked.connect(self._clear_all)
        r5.addWidget(clear_all)
        r5.addStretch(1)
        v3.addLayout(r5)
        root.addWidget(g3)

        # ---- 关于
        g4 = QGroupBox("④ 关于 / 使用要点")
        v4 = QVBoxLayout(g4)
        about = QLabel(
            "· 纯单机：识别、发音、词库、词条信息全在本机，断网可用。\n"
            "· 多人使用：启动时选用户，每人一个独立数据库，词库和背诵进度互不干扰；"
            "窗口右上角「👤 用户名 · 切换」可随时换人，无需重启。\n"
            "· 默认空白启动，不自动灌任何单词；基础词库由你自己导入，或一键植入内置标准词库。\n"
            "· 两层结构：「基础词库」是音标/词义/例句的唯一来源；「识别单词」只从 txt / 文本挑出要背哪些词，"
            "音标等信息一律以基础词库为准，绝不覆盖它。\n"
            "· 背诵三种模式：读音背诵（听发音拼单词）、看中文背诵（看释义拼单词）、查看模式（看单词全部内容）。\n"
            "· 答对自动划入已背词库、答错留在待背；已背词库记录背诵日期（年月日），可随时删除 / 移出。\n"
            "· 内置词条信息不全时可手动编辑，或让你本机 Ollama 补全（默认关闭，不联网、不上传）。\n"
            "· 备份：直接复制 data 目录，或在基础词库页导出 CSV。\n"
            "· 手机版：核心逻辑层不依赖界面框架，后续换 Kivy 界面即可复用，打包成安卓 APK。"
        )
        about.setObjectName("Hint")
        about.setWordWrap(True)
        v4.addWidget(about)
        root.addWidget(g4)
        root.addStretch(1)

    def refresh(self):
        """切换用户后重新读取当前用户的设置和数据库路径。"""
        self.db_label.setText(f"数据库：{self.ctx.db.path}")
        self._load()

    # ------------------------------------------------------------------ 载入 / 保存
    def _load(self):
        self.voice_cb.blockSignals(True)
        self.voice_cb.clear()
        self.voice_cb.addItem("自动（优先选英文语音）", "")
        self.voice_cb.blockSignals(False)
        try:
            self.rate.setValue(int(self.ctx.db.get_setting("tts_rate", "0")))
        except ValueError:
            pass
        url, model, _ = self.ctx.dictionary.ollama_config()
        self.url_edit.setText(url or DEFAULT_OLLAMA_URL)
        self.model_edit.setText(model or DEFAULT_OLLAMA_MODEL)
        self._test_ollama()
        # 枚举系统语音要启动 PowerShell，放后台线程，避免开窗口时同步等 1 秒以上
        run_async(
            lambda emit: (
                self.ctx.speech.list_voices(),
                self.ctx.speech.default_english_voice(),
            ),
            on_done=self._fill_voices,
            on_error=lambda m: self.voice_note.setText("未能读取系统语音列表；发音可用自动语音"),
        )

    def _fill_voices(self, res):
        voices, default = res
        saved = self.ctx.db.get_setting("tts_voice", "")
        self.voice_cb.blockSignals(True)
        self.voice_cb.clear()
        self.voice_cb.addItem("自动（优先选英文语音）", "")
        for v in voices:
            self.voice_cb.addItem(v, v)
        idx = self.voice_cb.findData(saved)
        self.voice_cb.setCurrentIndex(max(0, idx))
        self.voice_cb.blockSignals(False)
        self.voice_note.setText(
            f"检测到英文语音：{default}" if default else "未检测到英文语音，将使用系统默认语音"
        )

    def _save_voice(self, index: int):
        if index >= 0:
            self.ctx.db.set_setting("tts_voice", self.voice_cb.itemData(index) or "")

    def _test_speak(self):
        word = self.test_word.text().strip() or "apple"
        self.ctx.status(f"试听发音：{word}")
        run_async(
            lambda emit: self.ctx.speech.speak(word),
            on_done=lambda res: self.ctx.status(f"♪ {word}" if res[0] else f"试听失败：{res[1]}"),
        )

    def _test_ollama(self):
        self.ollama_status.setText("正在检测本机模型服务…")
        run_async(
            lambda emit: self.ctx.dictionary.ollama_status(timeout=3),
            on_done=lambda res: self.ollama_status.setText(
                ("✓ " if res[0] else "○ ") + res[1]
            ),
        )

    def _clear_study(self):
        from PySide6.QtWidgets import QMessageBox

        n = self.ctx.db.counts()["total"]
        ret = QMessageBox.warning(
            self, "确认清空学习词单",
            f"将删除 {n} 条学习条目，以及所有导入批次、待背 / 已背 / 重点记录、熟练度和背诵日期。\n"
            "基础词库里的音标、词义、例句会完整保留。\n"
            "此操作不可撤销；建议先到「基础词库」页导出 CSV 备份。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        removed = self.ctx.db.clear_study_items()
        self.ctx.status(f"已清空 {removed} 条学习条目；基础词库完整保留")
        self.ctx.refresh_views()

    def _clear_all(self):
        from PySide6.QtWidgets import QMessageBox

        s = self.ctx.db.entry_stats()
        c = self.ctx.db.counts()
        ret = QMessageBox.warning(
            self, "确认清空全部数据",
            f"将删除基础词库 {s['total']} 条词条、{c['total']} 条学习条目，"
            "以及所有导入批次、背诵日期、重点标记和熟练度。\n"
            "清空前会自动在 data/backup 里生成一份数据库备份，可以随时找回。\n"
            "清空后是空白词库，需要你自己导入，或点「植入内置标准词库」。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        backup = os.path.join(
            DATA_DIR, "backup", f"vocab_{datetime.now():%Y%m%d_%H%M%S}.db"
        )
        try:
            self.ctx.db.backup_to(backup)
        except Exception as e:
            QMessageBox.critical(self, "备份失败", f"为避免误删，已取消清空：{e}")
            return
        self.ctx.db.clear_all_data()
        self.ctx.status(
            f"已清空全部数据，现在是空白词库；备份：{os.path.basename(backup)}"
        )
        self.ctx.refresh_views()

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择词库文件", "",
            "词库文件 (*.csv *.json *.txt *.md);;CSV (*.csv);;JSON (*.json);;文本清单 (*.txt *.md);;所有文件 (*.*)",
        )
        if not path:
            return
        self.ctx.status(f"正在导入 {os.path.basename(path)} …（在后台进行，界面不会卡住）")
        run_async(
            lambda emit: self.ctx.dictionary.import_dict_file(path, "merge"),
            on_done=lambda res: (self.ctx.status(res[1]), self.ctx.refresh_views()),
            on_error=lambda m: self.ctx.status(f"导入失败：{m}"),
        )
