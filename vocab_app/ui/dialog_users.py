"""用户选择 / 管理窗口：启动时先弹出，多用户各自独立词库。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from ..core.users import UserManager


class UserDialog(QDialog):
    """返回选中的用户名（self.selected）；取消时 selected 为空。"""

    def __init__(self, manager: UserManager, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.manager = manager
        self.selected = ""
        self.first_run = first_run
        self.setWindowTitle("选择用户")
        self.setMinimumWidth(440)
        self._build()
        self._reload()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        title = QLabel("词库本 · 选择使用者")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        tip = QLabel(
            "每个用户有自己独立的词库和背诵进度，互不干扰；"
            "双击用户名或点「进入」开始。"
        )
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(tip)

        self.list = QListWidget()
        self.list.setObjectName("NavList")
        self.list.setMinimumHeight(190)
        self.list.itemDoubleClicked.connect(lambda _: self.accept_user())
        v.addWidget(self.list)

        row = QHBoxLayout()
        add = QPushButton("新建用户")
        add.clicked.connect(self._add_user)
        row.addWidget(add)
        rename = QPushButton("重命名")
        rename.clicked.connect(self._rename_user)
        row.addWidget(rename)
        delete = QPushButton("删除用户")
        delete.setObjectName("Warn")
        delete.clicked.connect(self._delete_user)
        row.addWidget(delete)
        row.addStretch(1)
        v.addLayout(row)

        self.note = QLabel("")
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        v.addWidget(self.note)

        buttons = QDialogButtonBox()
        self.enter_btn = buttons.addButton("进入", QDialogButtonBox.ButtonRole.AcceptRole)
        if not self.first_run:
            buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept_user)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    # ------------------------------------------------------------------ 数据
    def _reload(self, keep: str = ""):
        current = keep or self.manager.active_user()
        self.list.clear()
        for name in self.manager.users():
            item = QListWidgetItem(f"👤  {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            if name == current:
                item.setSelected(True)
                self.list.setCurrentItem(item)
            self.list.addItem(item)
        active = self.manager.active_user()
        self.note.setText(f"上次使用：{active}　·　共 {len(self.manager.users())} 个用户")

    def _current_name(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    # ------------------------------------------------------------------ 操作
    def accept_user(self):
        name = self._current_name()
        if not name:
            QMessageBox.information(self, "选择用户", "请先在列表里选一个用户，或点「新建用户」。")
            return
        self.selected = name
        self.manager.set_active(name)
        self.accept()

    def _add_user(self):
        name, ok = QInputDialog.getText(self, "新建用户", "用户名（1–24 个字）：")
        if not ok or not name.strip():
            return
        success, msg = self.manager.add_user(name)
        QMessageBox.information(self, "新建用户", msg)
        if success:
            self._reload(keep=name.strip())

    def _rename_user(self):
        old = self._current_name()
        if not old:
            QMessageBox.information(self, "重命名", "请先选中一个用户。")
            return
        name, ok = QInputDialog.getText(self, "重命名用户", "新用户名：", text=old)
        if not ok or not name.strip():
            return
        success, msg = self.manager.rename_user(old, name)
        QMessageBox.information(self, "重命名", msg)
        if success:
            self._reload(keep=name.strip())

    def _delete_user(self):
        name = self._current_name()
        if not name:
            QMessageBox.information(self, "删除用户", "请先选中一个用户。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除用户")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"确定删除用户「{name}」吗？")
        box.setInformativeText(
            "会删除这个用户的词库和全部背诵记录；\n"
            "删除前会自动备份到 data/backup/users/，需要时可恢复。\n"
            "此操作只影响选中的用户，其他用户完全不受影响。"
        )
        yes = box.addButton("确认删除", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        success, msg = self.manager.delete_user(name)
        QMessageBox.information(self, "删除用户", msg)
        if success:
            self._reload()
