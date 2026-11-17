"""电脑版入口：python vocab_app/main.py（或双击 start.bat）。"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    from PySide6.QtWidgets import QApplication

    from vocab_app.core.users import UserManager
    from vocab_app.ui.common import STYLE
    from vocab_app.ui.dialog_users import UserDialog
    from vocab_app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("词库本")
    app.setStyleSheet(STYLE)

    manager = UserManager()
    dlg = UserDialog(manager, first_run=True)
    if not dlg.exec():
        return  # 用户取消，直接退出
    name = dlg.selected
    db_path = manager.db_path(name)

    win = MainWindow(db_path=db_path, user_name=name, user_manager=manager)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 用 pythonw 双击启动时没有控制台，把异常落到日志文件方便排查
        log_dir = os.path.join(ROOT, "vocab_app", "data")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "error.log"), "a", encoding="utf-8") as f:
            f.write("\n===== 启动失败 =====\n")
            f.write(traceback.format_exc())
        raise
