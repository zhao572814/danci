"""离线图片文字识别（OCR）：tesseract4android + 内置英文训练数据。

完全离线、不联网、不依赖 GMS。识别流程：
  1. 首次使用时把 assets/tessdata/eng.traineddata 复制到 App 私有目录
     （tesseract 的 native 层必须读真实文件路径，不能直接读 APK 里的 asset）；
  2. 把选中的图片字节写到私有目录的临时文件，用 Android BitmapFactory 解码；
  3. pyjnius 调 TessBaseAPI.init/setImage/getUTF8Text 出文字；
  4. 每次识别后 recycle 释放 native 实例，避免内存堆积。

全程 try/except，任何一步失败都返回 (False, "", 原因)，不让 App 崩。
"""
from __future__ import annotations

import os

# 引擎与模型在打包时的位置
OEM_DEFAULT = 3            # TessBaseAPI.OEM_DEFAULT：自动识别引擎模式，兼容 tessdata_fast
_TRAIN_ASSET = "tessdata/eng.traineddata"
_TESS_DIRNAME = "tesseract"  # App 私有目录下的工作目录，内含 tessdata/

_INIT_FAILED = None  # 缓存初始化失败原因，避免每次点都重复走一遍坏路径


def _data_dir() -> str:
    return os.environ.get("VOCAB_DATA_DIR") or os.path.expanduser("~")


def work_dir() -> str:
    """返回给 tesseract 用的数据根目录（其下必须有 tessdata/）。"""
    return os.path.join(_data_dir(), _TESS_DIRNAME)


def _log(line: str) -> None:
    try:
        d = _data_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ocr_debug.txt"), "a", encoding="utf-8") as f:
            import time
            f.write(time.strftime("%H:%M:%S ") + str(line) + "\n")
    except Exception:
        pass


def _ensure_traineddata() -> str:
    """确保私有目录里有 eng.traineddata，返回 tesseract 数据根目录。
    已复制过且大小正常就直接返回（4MB 文件只复制一次）。
    """
    root = work_dir()
    td_dir = os.path.join(root, "tessdata")
    target = os.path.join(td_dir, "eng.traineddata")
    if os.path.exists(target) and os.path.getsize(target) > 3_000_000:
        return root
    os.makedirs(td_dir, exist_ok=True)
    # 从 APK assets 复制到私有目录：一次读全部字节（不用 jarray，避开 p4a 坑）
    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        activity = PythonActivity.mActivity
        am = activity.getAssets()
        raw = am.open(_TRAIN_ASSET)  # Java InputStream
        data = None
        try:
            try:
                allb = raw.readAllBytes()
                if allb is not None:
                    data = bytes((int(x) & 0xFF) for x in allb)
            except Exception:
                raw.reset()
                out = bytearray()
                while True:
                    b = raw.read()
                    if b is None or b < 0:
                        break
                    out.append(int(b) & 0xFF)
                data = bytes(out)
        finally:
            try:
                raw.close()
            except Exception:
                pass
        if not data or len(data) < 3_000_000:
            raise RuntimeError(f"训练数据读取不完整（{len(data) if data else 0} 字节）")
        with open(target, "wb") as f:
            f.write(data)
        _log(f"训练数据已复制到 {target} ({len(data)} 字节)")
        return root
    except Exception as e:
        # 桌面调试：直接从源码 assets 读
        try:
            src = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "assets", "tessdata", "eng.traineddata")
            if os.path.exists(src) and os.path.getsize(src) > 3_000_000:
                with open(src, "rb") as f, open(target, "wb") as g:
                    g.write(f.read())
                _log(f"训练数据（本地调试）已复制 ({os.path.getsize(target)} 字节)")
                return root
        except Exception:
            pass
        raise RuntimeError(f"无法准备 OCR 训练数据：{type(e).__name__}: {e}")


def extract_text(data: bytes) -> tuple[bool, str, str]:
    """识别图片字节中的文字。返回 (成功, 文本, 错误信息)。"""
    global _INIT_FAILED
    if _INIT_FAILED:
        return False, "", _INIT_FAILED
    if not data:
        return False, "", "图片是空的"
    tess = None
    tmp = ""
    try:
        from jnius import autoclass  # type: ignore
        TessBaseAPI = autoclass("com.googlecode.tesseract.android.TessBaseAPI")
        BitmapFactory = autoclass("android.graphics.BitmapFactory")

        root = _ensure_traineddata()
        # 图片字节写临时文件，让 Java BitmapFactory 解码（不碰 byte[]，避开 p4a 坑）
        tmp = os.path.join(_data_dir(), "_ocr_input.jpg")
        with open(tmp, "wb") as f:
            f.write(data)
        bitmap = BitmapFactory.decodeFile(tmp)
        if bitmap is None:
            return False, "", "图片解码失败，请换一张 JPG/PNG 图片试试"

        tess = TessBaseAPI()
        ok = tess.init(root, "eng", OEM_DEFAULT)
        if not ok:
            raise RuntimeError("OCR 引擎初始化失败（训练数据路径或模型不对）")
        tess.setImage(bitmap)
        text = tess.getUTF8Text() or ""
        text = str(text).strip()
        _log(f"识别完成：{len(text)} 字符")
        if not text:
            return False, "", "图片里没识别出文字，请换一张更清晰、文字更大的图片"
        return True, text, ""
    except Exception as e:
        msg = f"图片识别失败：{type(e).__name__}: {e}"
        _log(msg)
        return False, "", msg
    finally:
        if tess is not None:
            try:
                tess.recycle()
            except Exception:
                pass
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass


def available() -> tuple[bool, str]:
    """OCR 是否可用（不触发完整识别，用于设置页展示状态）。"""
    try:
        from jnius import autoclass  # type: ignore  # noqa: F401
        return True, "离线图片识别已就绪"
    except Exception as e:
        return False, f"离线图片识别不可用：{type(e).__name__}: {e}"
