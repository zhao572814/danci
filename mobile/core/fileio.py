"""手机端文件导入：系统文件选择器 + 文档文字提取，全程离线、零新增依赖。

关键点（v18 修复）：
* 旧代码用 plyer.filechooser，它把 content:// URI 解析成路径/文件名，
  在「下载」「媒体库」等来源经常只拿到文件名，open() 必然失败 —— 就是
  用户反馈的「导入 TXT 没成功」。这里改用系统 ACTION_OPEN_DOCUMENT，
  拿原始 URI 后用 ContentResolver.openInputStream 直接读字节流，最可靠。
* PDF 用纯 Python 提取内嵌文字（zlib 解 stream + 解析 Tj/TJ 操作符），
  不打包 PyMuPDF 等原生库，避免编译失败。文字版 PDF 可用；扫描版明确提示。
* 图片 OCR 在纯离线 + 无 GMS（卓易通）环境没有可靠引擎，诚实降级并给出
  「用系统/微信提取文字后粘贴」的替代路径，不虚假承诺。
"""

from __future__ import annotations

import os
import re

# 必须持有原生 listener 的 Python 引用，否则函数返回后被 GC 回收，
# Java 层的回调对象随之失效 —— 这是"有选择流程但结果永不回调"的隐蔽真凶。
_KEEPALIVE = []


def _debug(line):
    """把文件选择的每一步写入 fileio_debug.txt（数据目录），便于真机排查。
    用户可在「设置 → 查看崩溃日志」里看到，不用再靠猜。"""
    try:
        import time
        d = os.environ.get("VOCAB_DATA_DIR", "") or os.path.expanduser("~")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "fileio_debug.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(line) + "\n")
    except Exception:
        pass


TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".srt", ".text"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
# 常见的 Word 文本 mime 也归到文本兜底
MIME_FALLBACK_TEXT = "text/plain"


def classify(name: str) -> str:
    """按文件名判断类型：text / pdf / image / unknown。"""
    ext = os.path.splitext(name or "")[1].lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def decode_text(data: bytes) -> str:
    """多编码兜底解码：UTF-8 → UTF-8 BOM → GBK → Latin-1。"""
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _pdf_literal_decode(lit: str) -> str:
    """解码 PDF 字符串字面量：(...) 或 <hex>。"""
    if lit.startswith("<"):
        h = re.sub(r"\s", "", lit[1:-1])
        try:
            return bytes.fromhex(h).decode("latin-1")
        except Exception:
            return ""
    body = lit[1:-1]  # 去掉外层括号
    out = []
    i = 0
    n = len(body)
    ctrl = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            nx = body[i + 1]
            if nx in ctrl:
                out.append(ctrl[nx]); i += 2
            elif nx in "()\\":
                out.append(nx); i += 2
            elif nx in "\r\n":
                # 反斜杠 + 换行 = 续行，忽略
                i += 2
                if nx == "\r" and i < n and body[i] == "\n":
                    i += 1
            elif nx.isdigit():
                octal = nx
                i += 2
                while i < n and len(octal) < 3 and body[i].isdigit():
                    octal += body[i]
                    i += 1
                try:
                    out.append(chr(int(octal, 8) & 0xFF))
                except Exception:
                    pass
            else:
                out.append(nx); i += 2
        else:
            out.append(c); i += 1
    return "".join(out)


def _pdf_content_to_text(content: bytes) -> str:
    """从解压后的 content stream 提取文本（Tj / TJ / ' / " 操作符）。"""
    s = content.decode("latin-1", "ignore")
    # PDF 字符串字面量：括号串（含转义与嵌套括号）或 hex 串
    lit = r"(?:\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>)"
    span = re.compile(
        r"\[((?:" + lit + r"|[^\]])*?)\]\s*TJ|" + lit + r"\s*(?:Tj|'|\")",
        re.S,
    )
    pieces = []
    for m in span.finditer(s):
        seg = m.group(0)
        if "]" in seg and "TJ" in seg and m.group(1) is not None:
            for one in re.findall(lit, m.group(1), re.S):
                pieces.append(_pdf_literal_decode(one))
        else:
            lm = re.match(lit, seg, re.S)
            if lm:
                pieces.append(_pdf_literal_decode(lm.group(0)))
        pieces.append(" ")
    text = "".join(pieces)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def extract_pdf_text(data: bytes) -> tuple[str, str]:
    """纯 Python 提取 PDF 内嵌文字。返回 (文本, 提示)。

    只处理最常见的 FlateDecode / 未压缩 stream；扫描版 PDF 无内嵌文字，
    会返回空文本 + 提示，界面引导用户改用文字版或粘贴。
    """
    import zlib

    chunks = []
    try:
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\n?endstream", data, re.S):
            raw = m.group(1)
            if not raw:
                continue
            candidates = []
            try:
                candidates.append(zlib.decompress(raw))
            except Exception:
                pass
            candidates.append(raw)  # 未压缩的 stream
            for content in candidates:
                if b"Tj" not in content and b"TJ" not in content:
                    continue
                text = _pdf_content_to_text(content)
                if text:
                    chunks.append(text)
                break
    except Exception as e:
        return "", f"PDF 解析出错：{type(e).__name__}: {e}"

    full = "\n".join(chunks)
    # 过滤明显是二进制乱码的片段（可读英文字符占比过低）
    letters = sum(ch.isalpha() for ch in full)
    if full and letters / max(1, len(full)) < 0.3:
        return "", "这份 PDF 没有可提取的文字（可能是扫描图片版），请用相册/微信的「提取文字」后粘贴。"
    if not full.strip():
        return "", "这份 PDF 没有内嵌文字（扫描版），请用相册/微信的「提取文字」后粘贴。"
    return full, ""


def _get_android_activity():
    """取当前 Activity，多重兜底。

    新版 p4a 里 `from android import mActivity` 由编译期的 _android 模块提供，
    某些构建/兼容层下不可用；这里优先它，失败再直接问 Java 类要。
    """
    errors = []
    # 路径 1：p4a 标准导出
    try:
        from android import mActivity  # type: ignore
        if mActivity:
            return mActivity, ""
    except Exception as e:
        errors.append(f"android.mActivity: {e}")
    # 路径 2：新版 android.activity 模块内部的 _activity
    try:
        import android.activity as _am  # type: ignore
        act = getattr(_am, "_activity", None) or getattr(_am, "mActivity", None)
        if act:
            return act, ""
    except Exception as e:
        errors.append(f"android.activity: {e}")
    # 路径 3：直接从 Java 类取静态字段（Activity 类一定在 APK 里）
    try:
        from jnius import autoclass  # type: ignore
        classes = ["org.kivy.android.PythonActivity",
                   "org.kivy.android.PythonService"]
        # 尽量从 android.config 读实际类名（不同 bootstrap 会改）
        try:
            from android.config import ACTIVITY_CLASS_NAME  # type: ignore
            if ACTIVITY_CLASS_NAME and ACTIVITY_CLASS_NAME not in classes:
                classes.insert(0, ACTIVITY_CLASS_NAME)
        except Exception:
            pass
        for cls in classes:
            try:
                act = autoclass(cls).mActivity
                if act:
                    return act, ""
            except Exception as e2:
                errors.append(f"{cls}: {e2}")
    except Exception as e:
        errors.append(f"autoclass: {e}")
    return None, "; ".join(errors)


def _read_uri_bytes(resolver, uri):
    """用 ContentResolver 读 content:// URI 的原始字节。

    v26 修复：真机报 `ImportError: cannot import name 'jarray' from 'jnius'`，
    原因是这个 p4a 打包的 pyjnius 没有 jarray 模块。改成三级兜底，全程不 import jarray：
      1. Java 9+ InputStream.readAllBytes()（一步读完，最快）；
      2. autoclass('[B') 建 byte[] 缓冲分块读（多数 p4a 支持）；
      3. 逐字节 read() 写进 Python bytearray（最慢但零依赖，几百 KB 文本足够）。
    """
    stream = resolver.openInputStream(uri)
    if stream is None:
        return None, "无法打开这个文件，请换一个试试"
    try:
        # 方案 1：Java 层一次读完
        try:
            raw = stream.readAllBytes()  # Java 9+；鸿蒙runtime通常支持
            if raw is not None:
                return bytes((int(x) & 0xFF) for x in raw), ""
        except Exception as e1:
            _debug("readAllBytes 不可用: %s" % e1)
        # 方案 2：autoclass('[B') 建 byte[] 分块读（不用 jarray）
        try:
            from jnius import autoclass  # type: ignore
            BAOS = autoclass("java.io.ByteArrayOutputStream")
            buf = autoclass("[B")(16384)
            baos = BAOS()
            while True:
                count = stream.read(buf)
                if count is None or count <= 0:
                    break
                baos.write(buf, 0, count)
            raw = baos.toByteArray()
            return bytes((int(x) & 0xFF) for x in raw), ""
        except Exception as e2:
            _debug("[B]缓冲不可用，改逐字节: %s" % e2)
        # 方案 3：逐字节读进 Python bytearray（零依赖兜底）
        out = bytearray()
        while True:
            b = stream.read()
            if b is None or b < 0:
                break
            out.append(int(b) & 0xFF)
        if not out:
            return None, "这个文件读出来是空的"
        return bytes(out), ""
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _uri_display_name(resolver, uri):
    """从 URI 取文件名，取不到就用路径尾段。"""
    name = ""
    try:
        OpenableColumns = __import__("jnius").autoclass(  # type: ignore
            "android.provider.OpenableColumns")
        cursor = resolver.query(uri, None, None, None, None)
        if cursor is not None:
            idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if idx >= 0 and cursor.moveToFirst():
                name = cursor.getString(idx) or ""
            cursor.close()
    except Exception:
        pass
    if not name:
        try:
            name = os.path.basename(uri.getPath() or "") or "file"
        except Exception:
            name = "file"
    return name


def pick_file(callback, mime: str = "*/*") -> None:
    """调起系统文件选择器，读取原始字节后回调 callback(name, data, err)。

    v21 关键修复（"选了文件却没反应"）：
    * 旧代码用 `if requestCode != REQ: return` 严格匹配，鸿蒙卓易通用
      createChooser 后回传的 requestCode 可能对不上，结果被静默丢弃 ——
      这正好解释"有选择流程、但导入没效果、连错误提示都没有"。现在只要
      拿到结果（resultCode OK 且 data 非空）就处理，不纠结 requestCode。
    * 回调双通道注册：官方 android.activity.bind + 原生 listener 都上，
      任一触发即可，用 fired 去重。单一通道在某些兼容层会失效。
    * 每一步写 fileio_debug.txt，真机问题能直接看到卡在哪。
    """
    _debug("=== pick_file 开始 mime=%s ===" % mime)
    try:
        from jnius import autoclass  # type: ignore
    except Exception as e:
        _debug("缺少 jnius: %s" % e)
        callback("", b"", f"当前环境不支持文件选择（缺少 jnius：{e}）。请用复制粘贴。")
        return
    mActivity, act_err = _get_android_activity()
    if mActivity is None:
        _debug("拿不到 Activity: %s" % act_err)
        callback("", b"", f"当前环境不支持文件选择（拿不到 Activity：{act_err}）。请用复制粘贴。")
        return
    _debug("Activity 已获取: %s" % type(mActivity).__name__)

    try:
        Intent = autoclass("android.content.Intent")
        Activity = autoclass("android.app.Activity")
    except Exception as e:
        _debug("autoclass Intent/Activity 失败: %s" % e)
        callback("", b"", f"系统接口不可用：{e}。请用复制粘贴。")
        return
    REQ = 42117
    state = {"fired": False}

    def _handle_result(requestCode, resultCode, data):
        """统一结果处理，两个注册通道都汇到这里。"""
        _debug("收到结果 req=%s code=%s data=%s" %
               (requestCode, resultCode, "有" if data is not None else "空"))
        if state["fired"]:
            _debug("结果重复，忽略")
            return
        # 放宽：不再要求 requestCode == REQ（兼容层可能改动它）。
        # 一次只开一个选择器，只要 resultCode 是 OK 且有 data 就处理。
        if resultCode != Activity.RESULT_OK or data is None:
            _debug("判定为取消或失败，静默返回")
            state["fired"] = True
            return
        state["fired"] = True
        try:
            uri = data.getData()
            _debug("URI=%s" % uri)
            if uri is None:
                callback("", b"", "系统没返回文件，请重新选一次")
                return
            resolver = mActivity.getContentResolver()
            name = _uri_display_name(resolver, uri)
            _debug("文件名=%s" % name)
            payload, err = _read_uri_bytes(resolver, uri)
            _debug("读取字节数=%s err=%s" % (len(payload) if payload else 0, err))
            if err:
                callback(name, b"", err)
                return
            if not payload:
                callback(name, b"", "这个文件是空的")
                return
            callback(name, payload, "")
            _debug("已回调 on_text")
        except Exception as e:
            _debug("处理结果异常: %s: %s" % (type(e).__name__, e))
            callback("", b"", f"读取文件失败：{type(e).__name__}: {e}")

    def _start():
        inner = Intent(Intent.ACTION_GET_CONTENT)  # 兼容层上比 OPEN_DOCUMENT 支持更广
        inner.setType(mime)
        inner.addCategory(Intent.CATEGORY_OPENABLE)
        try:
            inner.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        except Exception:
            pass
        try:
            chooser = Intent.createChooser(inner, "选择文件")
            _debug("startActivityForResult(chooser)")
            mActivity.startActivityForResult(chooser, REQ)
        except Exception as ce:
            _debug("chooser 失败，改直启: %s" % ce)
            mActivity.startActivityForResult(inner, REQ)

    # 通道 1：官方 android.activity.bind（内部就是 registerActivityResultListener）
    bound = False
    try:
        import android.activity as activity_mod  # type: ignore
        activity_mod.bind(on_activity_result=_handle_result)
        bound = True
        _debug("通道1 android.activity.bind 注册成功")
    except Exception as e:
        _debug("通道1 bind 失败: %s" % e)

    # 通道 2：直接注册原生 ActivityResultListener（兜底，双保险）
    # 关键：把 listener 存入 _KEEPALIVE 持有强引用，防止被 GC 后回调失效。
    try:
        _native_listener = _bind_native_result(mActivity, _handle_result)
        _KEEPALIVE.append(_native_listener)
        _debug("通道2 native listener 注册成功（已持有引用）")
    except Exception as e:
        _debug("通道2 native 失败: %s" % e)

    if not bound:
        _debug("警告：两个通道都未注册成功")

    try:
        _start()
    except Exception as e:
        _debug("启动选择器异常: %s" % e)
        callback("", b"", f"打不开文件选择器：{type(e).__name__}: {e}。请用复制粘贴。")


def _bind_native_result(activity, callback):
    """当 android.activity 模块不可用时的原生 ActivityResult 监听兜底。"""
    from jnius import PythonJavaClass, autoclass  # type: ignore
    try:
        from jnius import java_method as _jm  # type: ignore
    except Exception:
        from jnius import javaapi as _jm  # type: ignore

    # PythonActivity 暴露了 registerActivityResultListener，监听器接口来自它自己。
    # p4a 官方源码用 ACTIVITY_CLASS_NAMESPACE + '$ActivityResultListener'，
    # namespace 可能是点号或斜杠形式，两种都试（pyjnius 两者都接受）。
    ifaces = [
        "org.kivy.android.PythonActivity$ActivityResultListener",
        "org/kivy/android/PythonActivity$ActivityResultListener",
    ]
    try:
        from android.config import ACTIVITY_CLASS_NAMESPACE as _ns  # type: ignore
        if _ns:
            if not _ns.endswith("/") and not _ns.endswith("."):
                # _ns 通常是类名（含 $ 或不含），补成 ActivityResultListener
                base = _ns
                for cand in (base + "$ActivityResultListener",
                             base.replace(".", "/") + "$ActivityResultListener"):
                    if cand not in ifaces:
                        ifaces.insert(0, cand)
    except Exception:
        pass

    last_err = None
    for iface in ifaces:
        try:
            class _ResultListener(PythonJavaClass):
                __javainterfaces__ = [iface]
                __javacontext__ = "app"

                def __init__(self, cb):
                    super().__init__()
                    self.cb = cb

                @_jm("(IILandroid/content/Intent;)V")
                def onActivityResult(self, requestCode, resultCode, data):
                    try:
                        self.cb(requestCode, resultCode, data)
                    except Exception:
                        pass

            listener = _ResultListener(callback)
            activity.registerActivityResultListener(listener)
            return listener
        except Exception as e:
            last_err = e
    raise last_err if last_err else Exception("无法注册 ActivityResult 监听")
