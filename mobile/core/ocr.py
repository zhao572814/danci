"""离线图片文字识别（OCR）：tesseract4android + 内置中英文训练数据。

完全离线、不联网、不依赖 GMS。识别流程：
  1. 首次使用时把 assets/tessdata/ 下的 chi_sim + eng 复制到 App 私有目录
     （tesseract 的 native 层必须读真实文件路径，不能直接读 APK 里的 asset）；
  2. 把选中的图片字节写到私有目录的临时文件，用 Android BitmapFactory 解码；
  3. pyjnius 调 TessBaseAPI.init/setImage/getUTF8Text 出文字（chi_sim+eng 双语）；
  4. 每次识别后 recycle 释放 native 实例，避免内存堆积。

为什么必须加中文：教材词表是「中文释义在上、英文词条在下」排版，只用英文模型
时中文标题会被硬读成英文乱码（如 WEA) LEBA），污染候选。中文模型能把中文正确
读成汉字，识别层再把汉字剔除，英文行就干净了。

降级保护：中文模型缺失或双语初始化失败时自动退回 eng 单语，保证纯英文图片
仍能识别，不让 App 崩。

全程 try/except，任何一步失败都返回 (False, "", 原因)，不让 App 崩。
"""
from __future__ import annotations

import os

# 引擎与模型在打包时的位置
OEM_DEFAULT = 3            # TessBaseAPI.OEM_DEFAULT：自动识别引擎模式，兼容 tessdata_fast
_TRAIN_ASSETS = ("tessdata/chi_sim.traineddata", "tessdata/eng.traineddata")
_LANG = "chi_sim+eng"      # 双语：中文标题 + 英文词条一起读
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


def _read_asset_bytes(am, asset_path: str) -> bytes:
    """从 APK assets 读一个文件的全部字节（不用 jarray，避开 p4a 坑）。"""
    raw = am.open(asset_path)  # Java InputStream
    try:
        try:
            allb = raw.readAllBytes()
            if allb is not None:
                data = bytes((int(x) & 0xFF) for x in allb)
                if data:
                    return data
        except Exception:
            pass
        raw.reset()
        out = bytearray()
        while True:
            b = raw.read()
            if b is None or b < 0:
                break
            out.append(int(b) & 0xFF)
        return bytes(out)
    finally:
        try:
            raw.close()
        except Exception:
            pass


def _upscale_for_ocr(bitmap, BitmapClass):
    """把偏小的图片放大后再识别，提升手机截图 / 微信压缩图的小字识别率。

    tesseract 对文字高度 <20px 的图识别率明显下降，而相册缩略图、聊天压缩过的
    截图经常就是这种尺寸。这里只做一次标准 Android Bitmap 缩放：
      * 最长边 <600px 视为图标/无效图，不动；
      * 最长边 >=1800px 已经够清晰，不动（避免无谓耗时和内存）；
      * 其余放大到约 2000px（最多 2.5 倍），双线性插值。
    任何异常都返回原图，绝不影响识别主流程。
    """
    try:
        w = int(bitmap.getWidth())
        h = int(bitmap.getHeight())
        longest = max(w, h)
        if longest < 600 or longest >= 1800:
            return bitmap
        scale = min(2.5, 2000.0 / longest)
        nw, nh = int(w * scale), int(h * scale)
        scaled = BitmapClass.createScaledBitmap(bitmap, nw, nh, True)
        if scaled is not None:
            _log(f"图片放大：{w}x{h} -> {nw}x{nh}")
            return scaled
        return bitmap
    except Exception as e:
        _log(f"图片放大跳过（用原图）：{type(e).__name__}: {e}")
        return bitmap


def _english_score(text: str) -> int:
    """统计文本里成形的英文词块数，用于比较两次 OCR 结果谁更有用。"""
    import re
    return len(re.findall(r"[A-Za-z]{2,}", text or ""))


def _ensure_traineddata() -> str:
    """确保私有目录里有 chi_sim + eng 两个训练数据，返回 tesseract 数据根目录。

    中文模型是可选增强：缺失/复制失败只跳过中文，不阻断英文识别。
    已复制过且大小正常的文件直接跳过（模型只复制一次）。
    """
    root = work_dir()
    td_dir = os.path.join(root, "tessdata")
    os.makedirs(td_dir, exist_ok=True)
    # 最小合法大小：chi_sim 约 2.4MB，eng 约 4MB，统一按 1MB 判定，过小视为损坏
    min_size = 1_000_000

    def ready(name: str) -> bool:
        target = os.path.join(td_dir, name)
        return os.path.exists(target) and os.path.getsize(target) > min_size

    missing = [n for n in ("chi_sim.traineddata", "eng.traineddata") if not ready(n)]
    if not missing:
        return root

    try:
        from jnius import autoclass  # type: ignore
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        am = PythonActivity.mActivity.getAssets()
        for name in missing:
            asset = "tessdata/" + name
            data = _read_asset_bytes(am, asset)
            if len(data) < min_size:
                _log(f"跳过 {name}：assets 里读取不完整（{len(data)} 字节）")
                continue
            with open(os.path.join(td_dir, name), "wb") as f:
                f.write(data)
            _log(f"训练数据已复制：{name} ({len(data)} 字节)")
    except Exception as e:
        # 桌面调试：直接从源码 assets 读
        src_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "assets", "tessdata")
        for name in missing:
            try:
                src = os.path.join(src_dir, name)
                if os.path.exists(src) and os.path.getsize(src) > min_size:
                    with open(src, "rb") as f, open(os.path.join(td_dir, name), "wb") as g:
                        g.write(f.read())
                    _log(f"训练数据（本地调试）已复制：{name}")
            except Exception:
                pass
    # 英文是底线：没有它 OCR 无法工作
    if not ready("eng.traineddata"):
        raise RuntimeError("无法准备 OCR 英文训练数据（eng.traineddata 缺失或过小）")
    return root


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
        Bitmap = autoclass("android.graphics.Bitmap")

        root = _ensure_traineddata()
        # 图片字节写临时文件，让 Java BitmapFactory 解码（不碰 byte[]，避开 p4a 坑）
        tmp = os.path.join(_data_dir(), "_ocr_input.jpg")
        with open(tmp, "wb") as f:
            f.write(data)
        bitmap = BitmapFactory.decodeFile(tmp)
        if bitmap is None:
            return False, "", "图片解码失败，请换一张 JPG/PNG 图片试试"

        tess = TessBaseAPI()
        # 优先中英双语；中文模型异常时降级为英文单语，保证纯英文图片仍可用
        has_chi = (os.path.exists(os.path.join(root, "tessdata", "chi_sim.traineddata"))
                   and os.path.getsize(os.path.join(root, "tessdata", "chi_sim.traineddata")) > 1_000_000)
        lang = _LANG if has_chi else "eng"
        ok = tess.init(root, lang, OEM_DEFAULT)
        if not ok and lang != "eng":
            _log("双语初始化失败，降级 eng 单语")
            try:
                tess.recycle()
            except Exception:
                pass
            tess = TessBaseAPI()
            ok = tess.init(root, "eng", OEM_DEFAULT)
            lang = "eng"
        if not ok:
            raise RuntimeError("OCR 引擎初始化失败（训练数据路径或模型不对）")
        # 原图优先——与上一版行为完全一致，保证以前认得好的图不会变差
        tess.setImage(bitmap)
        text = str(tess.getUTF8Text() or "").strip()
        base_score = _english_score(text)
        _log(f"原图识别：lang={lang}，{len(text)} 字符，英文词块 {base_score}")

        # 只有原图读出的英文明显偏少、且图片偏小时，才再试一次放大图，
        # 取英文更多的那份。放大失败/无改善都用原图结果，不引入新风险。
        if base_score < 12:
            try:
                big = _upscale_for_ocr(bitmap, Bitmap)
                if big is not bitmap:
                    tess.setImage(big)
                    text2 = str(tess.getUTF8Text() or "").strip()
                    score2 = _english_score(text2)
                    _log(f"放大图重试：{len(text2)} 字符，英文词块 {score2}")
                    if score2 > base_score:
                        text = text2
                        _log("采用放大图结果")
            except Exception as e:
                _log(f"放大重试跳过（用原图）：{type(e).__name__}: {e}")

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
