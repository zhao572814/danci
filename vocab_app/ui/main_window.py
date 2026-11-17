"""主窗口：左侧导航 + 五个页面，统一持有核心层实例。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QMainWindow, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..core.db import VocabDB
from ..core.dictionary import Dictionary
from ..core.models import LIST_MASTERED
from ..core.speech import Speech
from .common import run_async
from .page_baselib import BaseLibraryPage
from .page_import import ImportPage
from .page_library import LibraryPage
from .page_settings import SettingsPage
from .page_study import StudyPage

NAV = ["基础词库", "识别单词", "开始背诵", "已背词库", "设置与说明"]
TITLES = [
    "基础词库 · 音标 / 释义 / 例句的唯一来源",
    "识别单词 · 从 txt / 文本挑出要背的词",
    "开始背诵 · 读音背诵 / 看中文背诵 / 查看",
    "已背词库 · 含背诵日期，可移出",
    "设置与说明",
]


class MainWindow(QMainWindow):
    def __init__(self, db_path: str | None = None, user_name: str = "",
                 user_manager=None):
        super().__init__()
        self.setWindowTitle("词库本 · 离线背单词")
        self.resize(1180, 780)
        self.setMinimumSize(980, 660)

        self.user_manager = user_manager
        self.user_name = user_name
        self.db = VocabDB(db_path) if db_path else VocabDB()
        self.dictionary = Dictionary(self.db)
        self.speech = Speech(self.db)
        self._enriching = False
        # 数据变化后只刷新当前页，其它页标记「待刷新」，切过去时再重建，避免假死。
        self._dirty = [True] * 5

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFixedWidth(178)
        self.nav.addItems(NAV)
        self.nav.setCurrentRow(0)
        layout.addWidget(self.nav)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        header = QWidget()
        header.setObjectName("Header")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(22, 12, 22, 12)
        self.title_label = QLabel(TITLES[0])
        self.title_label.setObjectName("Title")
        hh.addWidget(self.title_label)
        hh.addStretch(1)
        self.header_stat = QLabel("")
        self.header_stat.setObjectName("SubTitle")
        hh.addWidget(self.header_stat)
        if self.user_name:
            hh.addSpacing(14)
            self.user_btn = QPushButton(f"👤 {self.user_name} · 切换")
            self.user_btn.setObjectName("UserChip")
            self.user_btn.setToolTip("切换到其他用户（各自独立的词库与背诵进度）")
            self.user_btn.clicked.connect(self._switch_user)
            hh.addWidget(self.user_btn)
        rv.addWidget(header)

        self.stack = QStackedWidget()
        self.page_base = BaseLibraryPage(self)
        self.page_import = ImportPage(self)
        self.page_study = StudyPage(self)
        self.page_mastered = LibraryPage(self, LIST_MASTERED)
        self.page_settings = SettingsPage(self)
        for p in (self.page_base, self.page_import, self.page_study,
                  self.page_mastered, self.page_settings):
            self.stack.addWidget(p)
        self.stack.setCurrentIndex(0)
        self.nav.currentRowChanged.connect(self._on_nav)
        rv.addWidget(self.stack, 1)
        layout.addWidget(right, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("就绪 · 基础词库是音标 / 释义 / 例句的唯一来源")
        self.refresh_views()

    # ------------------------------------------------------------------ 通用方法
    def status(self, msg: str):
        self.statusBar().showMessage(msg, 12000)

    def _switch_user(self):
        """就地切换到另一个用户：重建核心层并刷新所有页，不重启程序。"""
        from .dialog_users import UserDialog

        if not self.user_manager:
            self.status("当前没有启用多用户管理")
            return
        dlg = UserDialog(self.user_manager, self)
        if not dlg.exec() or not dlg.selected:
            return
        name = dlg.selected
        path = self.user_manager.db_path(name)
        if not path:
            self.status(f"找不到用户「{name}」的数据库")
            return
        # 页面全部通过 self.ctx.db / speech / dictionary 动态取用，替换即可
        self.db = VocabDB(path)
        self.dictionary = Dictionary(self.db)
        self.speech = Speech(self.db)
        self.user_name = name
        self.user_manager.set_active(name)
        if hasattr(self, "user_btn"):
            self.user_btn.setText(f"👤 {name} · 切换")
        self.setWindowTitle(f"词库本 · {name} · 离线背单词")
        self._enriching = False
        self.refresh_views()
        c = self.db.counts()
        self.status(
            f"已切换到用户「{name}」· 待背 {c['inbox']} · 已背 {c['mastered']}"
        )

    def _on_nav(self, index: int):
        self.stack.setCurrentIndex(index)
        self.title_label.setText(TITLES[index])
        self.refresh_counts_only()
        self._refresh_current()

    def _refresh_page(self, index: int):
        pages = (self.page_base, self.page_import, self.page_study,
                 self.page_mastered, self.page_settings)
        if 0 <= index < len(pages):
            pages[index].refresh()

    def _refresh_current(self):
        index = self.stack.currentIndex()
        if 0 <= index < len(self._dirty) and self._dirty[index]:
            self._refresh_page(index)
            self._dirty[index] = False

    def refresh_counts_only(self):
        c = self.db.counts()
        s = self.db.entry_stats()
        self.header_stat.setText(
            f"基础词条 {s['total']} · 待背 {c['inbox']} · 已背 {c['mastered']}"
        )

    def refresh_views(self):
        self.refresh_counts_only()
        self._dirty = [True] * 5
        self._refresh_current()

    def enrich_async(self, words=None):
        """用本机 Ollama 补全基础词库里信息不全的词条（用户主动触发才会写库）。"""
        if self._enriching:
            self.status("正在补全词条，请稍候…")
            return
        missing = self.db.missing_headwords()
        targets = missing if words is None else [w for w in words if w in set(missing)]
        if not targets:
            self.status("基础词库里没有需要补全的词条")
            return
        max_batch = 200
        rest = max(0, len(targets) - max_batch)
        targets = targets[:max_batch]
        extra = f"；还剩 {rest} 条，完成后再点一次即可" if rest else ""
        self._enriching = True
        self.status(f"正在用本机模型补全基础词库 {len(targets)} 条…{extra}")

        def work(emit):
            return self.dictionary.enrich_words(
                targets,
                progress=lambda done, total, w: emit((done, total, w)),
                cancel=lambda: False,
            )

        def done(res):
            self._enriching = False
            filled, note = res
            self.status(note)
            self.refresh_views()

        def failed(msg):
            self._enriching = False
            self.status(f"补全失败：{msg}")

        run_async(
            work, on_done=done, on_error=failed,
            on_progress=lambda v: self.status(f"正在补全基础词库 {v[0]}/{v[1]} …"),
        )
