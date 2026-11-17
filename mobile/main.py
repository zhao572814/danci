"""词库本 · 手机版（Kivy / Android APK）。

数据层复用 vocab_app/core（纯 Python + SQLite），界面用 Kivy 重写，
因为 PySide6 官方不支持打包 Android。全程离线：不联网、不上传任何数据。

Android 发音：调用系统 TextToSpeech（plyer / pyjnius），不依赖 Windows SAPI。
数据目录：Android 上通过环境变量 VOCAB_DATA_DIR 指向 app 私有可写目录。
"""

from __future__ import annotations

import os
import sys
import traceback
import faulthandler

# ---------------------------------------------------------------- 崩溃捕获
# 真机闪退多为 Python 异常，静默崩回桌面看不到原因。
# 关键教训：红字屏若只包 build()，启动后的 on_enter / 按钮回调报错时不会出现，
# 表现为「闪退但没有提示页」。这里做全周期捕获：任何异常都写日志 + 叠加红字层。
_BOOT_ERR = []          # 收集所有阶段的错误文本
_CRASH_DIR = ""         # Android 分支填入可写目录
_STAGE = "模块加载"      # 当前启动阶段，便于定位卡在哪一步

def set_stage(s):
    """记录启动进度：stage.txt 留最后一步，boot_trace.txt 留完整轨迹。
    原生层崩溃时 Python 钩子够不到，靠这两个文件判断卡在哪一步。"""
    global _STAGE
    _STAGE = s
    try:
        d = _CRASH_DIR or os.environ.get("VOCAB_DATA_DIR", "")
        if d:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "stage.txt"), "w", encoding="utf-8") as f:
                f.write(s)
            with open(os.path.join(d, "boot_trace.txt"), "a", encoding="utf-8") as f:
                f.write(s + "\n")
    except Exception:
        pass
    try:
        _update_status(s)
    except Exception:
        pass

def _crash_paths():
    return (_CRASH_DIR, os.environ.get("VOCAB_DATA_DIR", ""), os.path.expanduser("~"))

def _write_crash(text):
    for p in _crash_paths():
        if not p:
            continue
        try:
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "crash.txt"), "w", encoding="utf-8") as f:
                f.write(f"=== 阶段：{_STAGE} ===\n" + text)
            return True
        except Exception:
            continue
    return False

def _record_crash(text):
    """统一入口：收集 + 落盘 + 尽量显示红字层。"""
    _BOOT_ERR.append(text)
    _write_crash(text)
    _show_crash_overlay(text)

def _show_crash_overlay(text):
    """崩溃时弹出可截图的红字面板。
    v16 教训：往 Window.add_widget 叠加在部分真机 Kivy 上根本不显示，
    导致「有捕获但看不到提示页」。改用 Popup（Kivy 标准模态层）+
    直接替换 root（v13 验证过能显示）双保险，并延迟到下一帧避开重入。"""
    try:
        from kivy.app import App
        from kivy.clock import Clock
        app0 = App.get_running_app()
        if app0 is not None and getattr(app0, "_vocab_crash_shown", False):
            return
        if app0 is not None:
            app0._vocab_crash_shown = True

        def _do(_dt):
            # 保险 1：Popup 模态弹窗，稳定盖在最上层
            try:
                from kivy.uix.popup import Popup
                box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
                tip = _label(f"[{_STAGE}] 出错了，请截图发我：", 16,
                             get_color_from_hex("#FF6B6B"))
                tip.text_size = (dp(320), None)
                box.add_widget(tip)
                scroll = ScrollView()
                detail = _label(text[:6000], 12, get_color_from_hex("#B91C1C"))
                detail.text_size = (dp(320), None)
                detail.bind(texture_size=detail.setter("size"))
                scroll.add_widget(detail)
                box.add_widget(scroll)
                Popup(title="崩溃信息", content=box, size_hint=(0.94, 0.9)).open()
                return
            except Exception:
                pass
            # 保险 2：直接把当前 root 换成错误页（v13 验证过可显示）
            try:
                app = App.get_running_app()
                root = app.root if app else None
                if root is not None:
                    root.clear_widgets()
                    root.add_widget(error_screen(text))
            except Exception:
                pass

        Clock.schedule_once(_do, 0)
    except Exception:
        pass

_STATUS_LABEL = None

def install_status_bar():
    """屏幕顶端常驻一条阶段文字。原生崩溃来不及走 Python 钩子时，
    用户截图这条就能看出卡在哪一步——这是反复闪退时最可靠的线索。"""
    global _STATUS_LABEL
    try:
        from kivy.core.window import Window
        from kivy.uix.label import Label as _L
        bar = _L(text="启动中…", font_name=FONT, font_size=sp(11),
                 color=get_color_from_hex("#DC2626"),
                 size_hint=(1, None), height=dp(20), halign="left", valign="middle")
        bar.text_size = (None, None)
        _STATUS_LABEL = bar
        Window.add_widget(bar)
    except Exception:
        pass

def _update_status(s):
    if _STATUS_LABEL is not None:
        try:
            _STATUS_LABEL.text = "阶段：" + s
        except Exception:
            pass

def _excepthook(etype, exc, tb):
    text = "".join(traceback.format_exception(etype, exc, tb))
    _record_crash(text)
    sys.__excepthook__(etype, exc, tb)

sys.excepthook = _excepthook

# Kivy 全局异常兜底：on_enter / 按钮回调里的异常也显示红字，且吞掉不再抛出。
# 返回 True 是关键——返回 False 会让 Kivy 重新抛出异常导致静默闪退。
def _install_kivy_exception_handler():
    try:
        from kivy.base import ExceptionManager, ExceptionHandlerBase

        class _CrashHandler(ExceptionHandlerBase):
            def catch_exception(self, exception):
                text = "".join(traceback.format_exception(
                    type(exception), exception, exception.__traceback__))
                _record_crash(text)
                return True  # 吞掉，避免 Kivy 重新抛出造成闪退

        ExceptionManager.add_handler(_CrashHandler())
    except Exception:
        pass

# Android 上把数据放到 App 私有目录（/data/data/<pkg>/files），免除存储权限。
# 必须在 import core 之前设置：db.py / users.py 在 import 时读取该环境变量。
_IS_ANDROID = sys.platform == "android" or "ANDROID_ARGUMENT" in os.environ
if _IS_ANDROID:
    _DATA = ""
    try:
        from android.storage import app_storage_path  # type: ignore
        _DATA = os.path.join(app_storage_path(), "vocab_data")
    except Exception:
        pass
    if not _DATA:
        try:
            _DATA = os.path.join(os.environ.get("HOME") or "/sdcard", "vocab_data")
        except Exception:
            _DATA = "/sdcard/vocab_data"
    os.environ["VOCAB_DATA_DIR"] = _DATA
    _CRASH_DIR = _DATA

# ---------------------------------------------------------------- 原生崩溃捕获
# 若崩溃发生在 C/C++ 层（OpenGL/字体渲染/ScreenManager FBO），Python 的
# sys.excepthook 和 Kivy ExceptionManager 都接不到，App 直接静默闪退。
# faulthandler 是唯一能记录这种崩溃的 Python 机制：把信号 + Python 栈写进文件。
_FATAL_FH = None
try:
    _FH_DIR = _CRASH_DIR or os.environ.get("VOCAB_DATA_DIR", "") or os.path.expanduser("~")
    os.makedirs(_FH_DIR, exist_ok=True)
    _FATAL_FH = open(os.path.join(_FH_DIR, "fatal.txt"), "a", encoding="utf-8", buffering=1)
    _FATAL_FH.write("\n\n===== 启动 =====\n")
    faulthandler.enable(file=_FATAL_FH, all_threads=True)
    # 也接 SIGBUS（兼容层内存映射异常常见）
    import signal as _sig
    if hasattr(_sig, "SIGBUS"):
        try:
            faulthandler.register(_sig.SIGBUS, file=_FATAL_FH, all_threads=True)
        except Exception:
            pass
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from kivy.app import App
# 不用 FadeTransition：切换时要创建 FBO/纹理，部分安卓兼容层（如鸿蒙卓易通）
# 图形栈支持不完整，会在切换动画时 native 崩溃——Python 异常钩子完全抓不到，
# 表现就是「闪退且没有红字页」。改用默认 NoTransition，稳定且 on_enter 同步触发。
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.graphics import Color, Rectangle
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.metrics import dp, sp
from kivy.utils import get_color_from_hex
from kivy.clock import Clock

from core.db import VocabDB, parse_entry_lines
from core.matcher import detect_items, annotate
from core.users import UserManager
from core.models import SCOPE_INBOX, SCOPE_MASTERED

# ---------------------------------------------------------------- 中文字体
# 优先用打进 APK 的 simhei.ttf（各机型一致，已验证中文正常），系统字体兜底。
_FONT_REGISTERED = False
for _fp in (
    os.path.join(BASE, "assets", "fonts", "simhei.ttf"),
    os.path.join(os.getcwd(), "assets", "fonts", "simhei.ttf"),
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansCJKsc-Regular.otf",
    "/system/fonts/NotoSansSC-Regular.otf",
):
    if os.path.exists(_fp):
        try:
            LabelBase.register(name="NotoSC", fn_regular=_fp)
            _FONT_REGISTERED = True
            break
        except Exception:
            pass
FONT = "NotoSC" if _FONT_REGISTERED else "Roboto"

# ---------------------------------------------------------------- 主题色
C_PRIMARY = get_color_from_hex("#2563EB")
C_PRIMARY_D = get_color_from_hex("#1D4ED8")
C_BG = get_color_from_hex("#F4F6FB")
C_CARD = get_color_from_hex("#FFFFFF")
C_TEXT = get_color_from_hex("#1F2937")
C_MUTED = get_color_from_hex("#6B7280")
C_OK = get_color_from_hex("#059669")
C_WARN = get_color_from_hex("#D97706")
C_LINE = get_color_from_hex("#E5E7EB")

Window.clearcolor = C_BG


def _label(text, size=16, color=C_TEXT, **kw):
    # 允许调用处覆盖 height / halign 等，避免重复关键字参数报错
    opts = dict(
        font_name=FONT, font_size=sp(size), color=color,
        size_hint_y=None, height=sp(size * 1.7), halign="left", valign="middle",
        text_size=(None, None),
    )
    opts.update(kw)
    return Label(text=text, **opts)


def _btn(text, bg=C_PRIMARY, color=(1, 1, 1, 1), size=16, height=48, on_press=None):
    b = Button(
        text=text, font_name=FONT, font_size=sp(size), background_color=bg,
        color=color, size_hint_y=None, height=dp(height),
    )
    b.background_normal = ""
    b.background_disabled_normal = ""
    if on_press:
        b.bind(on_press=on_press)
    return b


class CardBox(BoxLayout):
    """带背景色的卡片容器。

    Kivy 原生 BoxLayout 没有 background_color 属性，直接传会抛
    TypeError（真机启动闪退）。这里用 canvas.before 画一块矩形实现背景，
    并跟随 pos/size 变化更新，缩放时不留残影。
    """
    def __init__(self, **kw):
        bg = kw.pop("background_color", (1, 1, 1, 1))
        super().__init__(**kw)
        self._bg_rect = None
        with self.canvas.before:
            Color(*bg)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)

    def _sync_bg(self, *_):
        if self._bg_rect is not None:
            self._bg_rect.pos = self.pos
            self._bg_rect.size = self.size


def error_screen(text):
    """启动崩溃时显示的红字错误页（可滚动、可截图发回）。"""
    root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
    root.add_widget(_label("启动出错，请把下面红字截图发给我：", 18,
                           get_color_from_hex("#DC2626"), halign="center"))
    scroll = ScrollView()
    detail = _label(text, 13, get_color_from_hex("#B91C1C"))
    detail.text_size = (dp(340), None)
    detail.bind(texture_size=detail.setter("size"))
    scroll.add_widget(detail)
    root.add_widget(scroll)
    return root


def toast(title, message):
    """轻量提示弹窗。"""
    box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
    lbl = _label(message, 15)
    lbl.text_size = (dp(280), None)
    box.add_widget(lbl)
    ok = _btn("好的", on_press=lambda *_: None)
    box.add_widget(ok)
    p = Popup(title=title, content=box, size_hint=(0.85, None), height=dp(190))
    ok.bind(on_press=lambda *_: p.dismiss())
    p.open()
    return p


def confirm(title, message, on_yes):
    box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
    lbl = _label(message, 15)
    lbl.text_size = (dp(280), None)
    box.add_widget(lbl)
    row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(48))
    yes = _btn("确定", bg=C_WARN)
    no = _btn("取消", bg=C_LINE, color=C_TEXT)
    row.add_widget(yes)
    row.add_widget(no)
    box.add_widget(row)
    p = Popup(title=title, content=box, size_hint=(0.85, None), height=dp(240))
    yes.bind(on_press=lambda *_: (p.dismiss(), on_yes()))
    no.bind(on_press=lambda *_: p.dismiss())
    p.open()
    return p


class TTS:
    """安卓系统朗读；桌面/无 TTS 环境降级为静默并给出提示。"""

    def __init__(self):
        self._engine = None
        self._ready = False

    def _init(self):
        if self._ready:
            return
        try:
            from plyer import tts  # type: ignore

            self._engine = tts
            self._ready = True
        except Exception:
            self._ready = True

    def speak(self, word: str):
        self._init()
        if not self._engine:
            return False, "当前环境不支持朗读"
        try:
            self._engine.speak(message=word)
            return True, ""
        except Exception as e:
            return False, f"朗读失败：{e}"


# ------------------------------------------------------------------ 页面
class UserScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14))
        root.add_widget(_label("词库本", 30, C_PRIMARY, halign="center"))
        root.add_widget(_label("选择用户 · 数据彼此隔离", 14, C_MUTED, halign="center"))
        self.user_box = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.user_box.bind(minimum_height=self.user_box.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.user_box)
        root.add_widget(scroll)
        add = _btn("＋ 新建用户", on_press=self.new_user)
        root.add_widget(add)
        self.add_widget(root)

    def on_enter(self):
        set_stage("用户页:on_enter")
        self.refresh()

    def refresh(self):
        self.user_box.clear_widgets()
        app = App.get_running_app()
        for name in app.users.users():
            row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(52))
            b = _btn(name, bg=C_CARD, color=C_TEXT, on_press=lambda _, n=name: self.pick(n))
            row.add_widget(b)
            if len(app.users.users()) > 1:
                rm = _btn("删除", bg=C_LINE, color=C_WARN, size=13, height=52,
                          on_press=lambda _, n=name: self.remove_user(n))
                rm.size_hint_x = None
                rm.width = dp(64)
                row.add_widget(rm)
            self.user_box.add_widget(row)

    def pick(self, name):
        app = App.get_running_app()
        app.switch_user(name)
        self.manager.current = "main"

    def remove_user(self, name):
        def do():
            app = App.get_running_app()
            ok, msg = app.users.delete_user(name)
            toast("删除用户", msg)
            self.refresh()
        confirm("删除用户", f"确定删除「{name}」？其数据库会先备份再删除。", do)

    def new_user(self, *_):
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        ti = TextInput(font_name=FONT, font_size=sp(16), multiline=False,
                       size_hint_y=None, height=dp(44), hint_text="输入用户名")
        box.add_widget(ti)
        ok = _btn("创建")
        box.add_widget(ok)
        p = Popup(title="新建用户", content=box, size_hint=(0.85, None), height=dp(200))

        def create(*_):
            ok_name, msg = App.get_running_app().users.add_user(ti.text)
            p.dismiss()
            toast("新建用户", msg)
            self.refresh()
        ok.bind(on_press=create)
        ti.bind(on_text_validate=create)
        p.open()
        ti.focus = True


class DictScreen(Screen):
    """基础词库：粘贴导入 / 导入内置 / 查看统计。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        self.stat = _label("", 15, C_MUTED)
        root.add_widget(self.stat)
        root.add_widget(_label("粘贴词库（每行一条：单词 [音标] 释义）", 14, C_TEXT))
        self.text = TextInput(font_name=FONT, font_size=sp(15), multiline=True,
                              hint_text="apple /ˈæp.əl/ n. 苹果\ntake part in  参与",
                              size_hint=(1, 0.4), background_color=C_CARD,
                              foreground_color=C_TEXT)
        root.add_widget(self.text)
        row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(48))
        row.add_widget(_btn("导入粘贴", on_press=self.import_text))
        row.add_widget(_btn("植入内置词库", bg=C_OK, on_press=self.install_builtin))
        root.add_widget(row)
        root.add_widget(_btn("打开内置词库说明", bg=C_LINE, color=C_TEXT,
                             on_press=self.help_builtin))
        self.add_widget(root)

    def on_enter(self):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        if not app.db:
            return
        s = app.db.entry_stats()
        self.stat.text = (f"基础词库：共 {s['total']} 条（单词 {s['words']} · "
                          f"短语 {s['phrases']}），完整 {s['complete']} 条")

    def import_text(self, *_):
        app = App.get_running_app()
        items = parse_entry_lines(self.text.text)
        if not items:
            toast("导入", "没解析出词条，请按「单词 释义」格式每行一条")
            return
        n, msg = app.db.import_entries(items, source="manual")
        self.text.text = ""
        self.refresh()
        toast("导入完成", msg)

    def install_builtin(self, *_):
        def do():
            app = App.get_running_app()
            n, msg = app.db.install_builtin_library()
            self.refresh()
            toast("内置词库", msg)
        confirm("植入内置词库", "将常用单词与短语写入基础词库（已有词条不覆盖），继续？", do)

    def help_builtin(self, *_):
        toast("说明", "「植入内置词库」会加入一批常用单词和短语（含音标/释义/例句）。"
                     "也可在上方粘贴自己的词库，或导入 TXT/CSV/JSON 文件后复制过来。")


class ImportScreen(Screen):
    """识别要背词：粘贴文本 → 从基础库挑词 → 加入待背。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        root.add_widget(_label("粘贴要背的英文文本 / 文章", 16))
        self.text = TextInput(font_name=FONT, font_size=sp(15), multiline=True,
                              hint_text="把英文段落粘贴到这里，程序会从基础词库里挑出要背的单词和短语",
                              size_hint=(1, 0.35), background_color=C_CARD,
                              foreground_color=C_TEXT)
        root.add_widget(self.text)
        opt = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(44))
        self.chk_stop = Button(text="过滤常见虚词：关", font_name=FONT, font_size=sp(13),
                               background_color=C_LINE, color=C_TEXT,
                               size_hint_y=None, height=dp(44))
        self.chk_stop.bind(on_press=self.toggle_stop)
        opt.add_widget(self.chk_stop)
        opt.add_widget(_btn("识别文本", bg=C_OK, on_press=self.recognize))
        root.add_widget(opt)
        self.hint = _label("识别后在此勾选要背的条目", 13, C_MUTED)
        root.add_widget(self.hint)
        self.list = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.list.bind(minimum_height=self.list.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.list)
        root.add_widget(scroll)
        root.add_widget(_btn("加入待背词库", on_press=self.add_selected))
        self.filter_stop = False
        self.candidates = []
        self.add_widget(root)

    def toggle_stop(self, *_):
        self.filter_stop = not self.filter_stop
        self.chk_stop.text = f"过滤常见虚词：{'开' if self.filter_stop else '关'}"

    def recognize(self, *_):
        app = App.get_running_app()
        if not app.db or not self.text.text.strip():
            toast("识别", "请先粘贴文本")
            return
        phrases = app.db.phrase_headwords()
        items = detect_items(self.text.text, phrases,
                             filter_stopwords=self.filter_stop, detect_phrases=True, min_len=1)
        annotate(items, app.db.lookup_entry)
        self.candidates = items
        self.render_candidates()
        self.hint.text = f"识别到 {len(items)} 个候选，取消勾选可排除："

    def render_candidates(self):
        self.list.clear_widgets()
        for it in self.candidates:
            row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(46))
            cb = Button(text="☑" if it.get("checked", True) else "☐", font_name=FONT,
                        font_size=sp(20), background_color=C_CARD, color=C_OK,
                        size_hint_x=None, width=dp(46))
            def toggle(_, item=it, btn=cb):
                item["checked"] = not item.get("checked", True)
                btn.text = "☑" if item["checked"] else "☐"
            cb.bind(on_press=toggle)
            row.add_widget(cb)
            tag = "短语" if it["kind"] == "phrase" else "单词"
            mark = "" if it.get("in_library") else "（基础库未收录，可先去基础词库补录）"
            lbl = _label(f"{it['headword']}  [{tag}]{mark}", 14)
            row.add_widget(lbl)
            self.list.add_widget(row)
            it["checked"] = True

    def add_selected(self, *_):
        app = App.get_running_app()
        chosen = [it for it in self.candidates if it.get("checked", True)]
        if not chosen:
            toast("加入待背", "没有勾选任何条目")
            return
        bid, ids, new, skipped = app.db.import_items(chosen, "粘贴识别", source="paste")
        self.candidates = []
        self.list.clear_widgets()
        self.text.text = ""
        self.hint.text = f"已加入 {len(ids)} 条到待背词库（新增 {new}，跳过 {skipped}）"
        toast("完成", f"已加入 {len(ids)} 条待背单词")


class StudyScreen(Screen):
    """三种背诵模式：读音 / 看中文 / 查看。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        top = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(44))
        self.mode = Spinner(text="读音背诵",
                            values=("读音背诵", "看中文背诵", "查看模式"),
                            font_name=FONT, font_size=sp(14),
                            background_color=C_LINE, color=C_TEXT,
                            size_hint_y=None, height=dp(44))
        self.mode.bind(text=lambda *_: self.restart())
        top.add_widget(self.mode)
        top.add_widget(_btn("重新开始", bg=C_LINE, color=C_TEXT, on_press=self.restart))
        root.add_widget(top)
        self.progress = ProgressBar(max=1, value=0, size_hint_y=None, height=dp(10))
        root.add_widget(self.progress)
        self.count = _label("待背 0 条", 14, C_MUTED, halign="center")
        root.add_widget(self.count)
        self.card = CardBox(orientation="vertical", spacing=dp(10), padding=dp(16),
                            size_hint=(1, 0.55), background_color=C_CARD)
        self.prompt = _label("", 24, C_PRIMARY, halign="center", valign="middle")
        self.prompt.text_size = (dp(320), None)
        self.extra = _label("", 15, C_MUTED, halign="center")
        self.extra.text_size = (dp(320), None)
        self.card.add_widget(self.prompt)
        self.card.add_widget(self.extra)
        self.answer = TextInput(font_name=FONT, font_size=sp(18), multiline=False,
                                hint_text="输入英文单词后确认", size_hint_y=None, height=dp(50),
                                background_color=get_color_from_hex("#FFFFFF"),
                                foreground_color=C_TEXT)
        self.card.add_widget(self.answer)
        speak_row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(46))
        speak_row.add_widget(_btn("♪ 朗读", bg=C_OK, on_press=self.speak))
        speak_row.add_widget(_btn("确认", on_press=self.check))
        self.card.add_widget(speak_row)
        root.add_widget(self.card)
        nav = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(46))
        nav.add_widget(_btn("跳过", bg=C_LINE, color=C_TEXT, on_press=self.skip))
        nav.add_widget(_btn("显示答案", bg=C_WARN, on_press=self.reveal))
        root.add_widget(nav)
        root.add_widget(_btn("清空待背单词（不影响已背）", bg=C_LINE, color=C_WARN,
                             on_press=self.clear_inbox))
        self.add_widget(root)
        self.words = []
        self.idx = 0
        self.current = None

    def on_enter(self):
        set_stage("背诵页:on_enter")
        if not self.words:
            self.restart()

    def restart(self, *_):
        set_stage("背诵页:restart查询待背词")
        app = App.get_running_app()
        self.words = app.db.list_words(SCOPE_INBOX) if app.db else []
        self.idx = 0
        self.show()
        set_stage("背诵页:显示完成")

    def show(self):
        if self.idx >= len(self.words):
            self.prompt.text = "🎉 待背词库已全部完成"
            self.extra.text = "可以回到「识别文本」导入新词，或在已背词库查看成果"
            self.count.text = "待背 0 条"
            self.progress.value = 1
            self.answer.disabled = True
            self.current = None
            return
        self.answer.disabled = False
        self.answer.text = ""
        self.current = self.words[self.idx]
        self.progress.value = self.idx / max(1, len(self.words))
        self.count.text = f"待背 {len(self.words) - self.idx} 条 · 第 {self.idx + 1}/{len(self.words)}"
        mode = self.mode.text
        if mode == "读音背诵":
            self.prompt.text = "♪ 点击朗读，听发音后拼写单词"
            self.extra.text = "输入你听到的英文单词"
        elif mode == "看中文背诵":
            self.prompt.text = self.current.get("translation") or "（该词暂无释义）"
            self.extra.text = "根据中文释义，输入对应的英文单词"
        else:
            w = self.current
            self.prompt.text = w.get("word", "")
            self.extra.text = (f"音标：{w.get('phonetic') or '—'}\n"
                               f"解释：{w.get('translation') or '—'}\n"
                               f"例句：{w.get('example') or '—'}")
            self.extra.text_size = (dp(320), None)
        if mode == "查看模式":
            self.answer.text = self.current.get("word", "")

    def speak(self, *_):
        if not self.current:
            return
        if self.mode.text == "查看模式":
            word = self.current.get("word", "")
        else:
            word = self.current.get("word", "")
        ok, msg = App.get_running_app().tts.speak(word)
        if not ok:
            toast("朗读", msg)

    def check(self, *_):
        if not self.current:
            return
        if self.mode.text == "查看模式":
            self.advance()
            return
        typed = (self.answer.text or "").strip().lower()
        target = (self.current.get("word") or "").strip().lower()
        if typed == target:
            App.get_running_app().db.mark_mastered([self.current["id"]])
            self.advance()
        else:
            toast("再想想", f"拼写不对，正确答案是：{self.current.get('word')}")

    def advance(self):
        self.idx += 1
        self.show()

    def skip(self, *_):
        self.advance()

    def reveal(self, *_):
        if self.current:
            toast("答案", self.current.get("word", ""))

    def clear_inbox(self, *_):
        def do():
            app = App.get_running_app()
            r = app.db.clear_inbox_words()
            self.restart()
            toast("已清空", f"清掉 {r['cleared']} 条待背单词，已背 {r['kept_mastered']} 条不受影响")
        confirm("清空待背", "只清空待背单词，已背单词和基础词库不受影响。确定继续？", do)


class MasteredScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        self.stat = _label("已背 0 条", 16, C_PRIMARY)
        root.add_widget(self.stat)
        self.list = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.list.bind(minimum_height=self.list.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.list)
        root.add_widget(scroll)
        self.add_widget(root)

    def on_enter(self):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        self.list.clear_widgets()
        rows = app.db.list_words(SCOPE_MASTERED) if app.db else []
        self.stat.text = f"已背词库：{len(rows)} 条"
        for w in rows[:500]:
            row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(64))
            info = _label(f"{w['word']}  {w.get('phonetic') or ''}\n"
                          f"{w.get('translation') or ''}", 14)
            info.text_size = (dp(250), None)
            row.add_widget(info)
            rm = _btn("移出", bg=C_LINE, color=C_WARN, size=13, height=48,
                      on_press=lambda _, wid=w["id"]: self.remove(wid))
            rm.size_hint_x = None
            rm.width = dp(64)
            row.add_widget(rm)
            self.list.add_widget(row)

    def remove(self, wid):
        def do():
            app = App.get_running_app()
            app.db.unmark_mastered([wid])
            self.refresh()
            toast("已移出", "该单词已回到待背词库")
        confirm("移出已背库", "移出后该词会回到待背词库，确定吗？", do)


class SettingsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(18))
        self.user_lbl = _label("", 16, C_TEXT)
        root.add_widget(self.user_lbl)
        self.path_lbl = _label("", 12, C_MUTED)
        self.path_lbl.text_size = (dp(300), None)
        root.add_widget(self.path_lbl)
        root.add_widget(_btn("切换 / 管理用户", on_press=self.switch_user))
        root.add_widget(_btn("测试朗读（apple）", bg=C_OK, on_press=self.test_tts))
        root.add_widget(_btn("查看崩溃日志（排查闪退用）", bg=C_LINE, color=C_TEXT,
                             on_press=self.show_crash))
        root.add_widget(_label("词库本 · 手机版 1.0\n纯离线 · 数据只保存在本机", 13,
                               C_MUTED, halign="center"))
        self.add_widget(root)

    def on_enter(self):
        app = App.get_running_app()
        self.user_lbl.text = f"当前用户：{app.current_user}"
        self.path_lbl.text = f"数据目录：{app.data_dir}"

    def switch_user(self, *_):
        App.get_running_app().stop_user_and_show_users()

    def test_tts(self, *_):
        ok, msg = App.get_running_app().tts.speak("apple")
        toast("朗读测试", "已朗读 apple" if ok else msg)

    def show_crash(self, *_):
        app = App.get_running_app()
        parts = []
        # 原生崩溃看 fatal.txt / boot_trace.txt / stage.txt；Python 异常看 crash.txt。
        for d in (getattr(app, "data_dir", ""), os.environ.get("VOCAB_DATA_DIR", "")):
            if not d:
                continue
            for name in ("fatal.txt", "crash.txt", "boot_trace.txt", "stage.txt"):
                try:
                    p = os.path.join(d, name)
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8") as f:
                            body = f.read().strip()
                        if body:
                            parts.append(f"----- {name} -----\n{body}")
                except Exception:
                    pass
            if parts:
                break
        text = "\n\n".join(parts)
        if not text:
            text = "\n".join(_BOOT_ERR) or "没有发现崩溃日志（说明没有 Python 层报错）。"
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        scroll = ScrollView()
        detail = _label(text, 12, get_color_from_hex("#B91C1C"))
        detail.text_size = (dp(320), None)
        detail.bind(texture_size=detail.setter("size"))
        scroll.add_widget(detail)
        box.add_widget(scroll)
        close = _btn("关闭", on_press=lambda *_: None)
        box.add_widget(close)
        p = Popup(title="崩溃日志（可截图发我）", content=box,
                  size_hint=(0.92, 0.85))
        close.bind(on_press=lambda *_: p.dismiss())
        p.open()


class MainScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical")
        bar = BoxLayout(size_hint_y=None, height=dp(52), padding=dp(12), spacing=dp(8))
        bar.add_widget(_label("词库本", 20, C_PRIMARY))
        self.bar_user = _label("", 13, C_MUTED, halign="right")
        bar.add_widget(self.bar_user)
        root.add_widget(bar)
        self.tabs = ScreenManager()  # NoTransition：避免兼容层 FBO 崩溃
        self.tabs.add_widget(DictScreen(name="dict"))
        self.tabs.add_widget(ImportScreen(name="import"))
        self.tabs.add_widget(StudyScreen(name="study"))
        self.tabs.add_widget(MasteredScreen(name="mastered"))
        self.tabs.add_widget(SettingsScreen(name="settings"))
        root.add_widget(self.tabs)
        nav = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(2), padding=dp(4))
        for text, name in (("词库", "dict"), ("识别", "import"), ("背诵", "study"),
                           ("已背", "mastered"), ("设置", "settings")):
            b = _btn(text, bg=C_PRIMARY, size=13, height=50,
                     on_press=lambda _, n=name: self.go(n))
            nav.add_widget(b)
        root.add_widget(nav)
        self.add_widget(root)

    def on_enter(self):
        set_stage("主界面:on_enter")
        app = App.get_running_app()
        self.bar_user.text = app.current_user
        set_stage("主界面:切到背诵页前")
        self.go("study")
        set_stage("主界面:切到背诵页后")

    def go(self, name):
        self.tabs.current = name


class VocabularyApp(App):
    def build(self):
        self.title = "词库本"
        _install_kivy_exception_handler()
        # 阶段条延迟到首帧后挂上，避免和根控件一起布局时冲突
        try:
            Clock.schedule_once(lambda *_: install_status_bar(), 0.1)
        except Exception:
            pass
        try:
            set_stage("build:创建用户管理")
            self.users = UserManager()
            self.tts = TTS()
            self.current_user = ""
            self.db = None
            self.data_dir = ""
            set_stage("build:创建界面")
            sm = ScreenManager()  # NoTransition：避免兼容层 FBO 崩溃
            sm.add_widget(UserScreen(name="users"))
            sm.add_widget(MainScreen(name="main"))
            self.sm = sm
            set_stage("build:切换用户")
            # 只有一个默认用户时直接进入，省一次点击
            names = self.users.users()
            if len(names) == 1:
                self.switch_user(names[0])
                sm.current = "main"
            set_stage("build:完成")
            return sm
        except Exception:
            text = "".join(traceback.format_exc())
            _BOOT_ERR.append(text)
            _write_crash(text)
            # 无法用 Kivy 布局时，退回最简 Label 也保证能看到错误
            try:
                return error_screen(text)
            except Exception:
                return Label(text=text[:2000], font_size=sp(13),
                             color=get_color_from_hex("#B91C1C"))

    def switch_user(self, name):
        self.current_user = name
        path = self.users.db_path(name)
        self.data_dir = os.path.dirname(path) if path else ""
        self.users.set_active(name)
        self.db = VocabDB(path) if path else None

    def stop_user_and_show_users(self):
        self.sm.current = "users"
        self.sm.get_screen("users").refresh()


if __name__ == "__main__":
    VocabularyApp().run()
