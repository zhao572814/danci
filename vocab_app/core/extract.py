"""文档 / 图片 → 英文单词。全部在本机完成，不调用任何在线 OCR 服务。

支持：
  .txt .md .csv .log  → 直接读文本
  .pdf                 → 优先抽内嵌文字；扫描版自动转图片走本地 OCR
  .docx                → 直接解 zip 里的 XML（不需要额外依赖）
  .png .jpg .jpeg .webp .bmp .tif .tiff → 本地 PaddleOCR（模型已在用户目录缓存）

离线 OCR 不可用时不会让程序崩溃，会返回带提示的结果，界面可以让用户手动粘贴文本。
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".srt"}
PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
SUPPORTED_EXT = IMAGE_EXT | TEXT_EXT | PDF_EXT | DOCX_EXT

# 常见虚词 / 功能词，导入文章时可以过滤掉，导入单词表时可关闭过滤
STOPWORDS = set("""
a an the and or but if then than so as at by for from in into on onto of off to with without
is am are was were be been being do does did done have has had having will would shall should
can could may might must need ought
i you he she it we they me him her us them my your his its our their mine yours hers ours theirs
this that these those there here what which who whom whose when where why how
not no nor yes all any some each every both either neither few more most other another such
very too also just only even still yet ever never almost quite rather
up down out over under again further once about above below between through during before after
one two three four five six seven eight nine ten first second
s t re ve ll d m o y
""".split())

WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019-][A-Za-z]+)*")


@dataclass
class ExtractionResult:
    text: str = ""
    words: list[str] = field(default_factory=list)
    kind: str = ""            # text / pdf / pdf_ocr / docx / image_ocr / manual
    notice: str = ""          # 给用户的提示（例如 OCR 不可用）
    source: str = ""


def normalize_word(raw: str) -> str:
    w = raw.strip().strip(".,;:!?()[]{}\"'`\u201c\u201d")
    w = w.replace("\u2019", "'")
    if not w:
        return ""
    # 缩写（NASA / TOEFL）保留大写，其余统一小写，避免大小写造成重复
    if w.isupper() and 2 <= len(w) <= 6:
        return w
    return w.lower()


def words_from_text(text: str, filter_stopwords: bool = True, min_len: int = 2) -> list[str]:
    """按出现顺序抽取、规范化、去重。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in WORD_RE.finditer(text or ""):
        w = normalize_word(m.group(0))
        if len(w) < min_len:
            continue
        if filter_stopwords and w.lower() in STOPWORDS:
            continue
        if not any(ch.isalpha() for ch in w):
            continue
        key = w.lower()
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


# --------------------------------------------------------------------------- OCR
class LocalOCR:
    """懒加载的本地 PaddleOCR。首次初始化约 3~8 秒，建议放后台线程调用。"""

    def __init__(self):
        self._engine = None
        self._error: Optional[str] = None
        self._model_root = os.path.join(
            os.path.expanduser("~"), ".paddlex", "official_models"
        )

    @property
    def available(self) -> bool:
        det = os.path.join(self._model_root, "PP-OCRv6_medium_det", "inference.json")
        rec = os.path.join(self._model_root, "PP-OCRv6_medium_rec", "inference.json")
        return os.path.exists(det) and os.path.exists(rec)

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _init(self):
        if self._engine is not None or self._error:
            return
        try:
            import numpy  # noqa: F401  (确保依赖齐全)
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            os.environ["HF_HUB_OFFLINE"] = "1"
            from paddleocr import PaddleOCR

            self._engine = PaddleOCR(
                text_detection_model_dir=os.path.join(self._model_root, "PP-OCRv6_medium_det"),
                text_recognition_model_dir=os.path.join(self._model_root, "PP-OCRv6_medium_rec"),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,   # 本机 paddle 3.3 + oneDNN 会报 NotImplementedError
            )
        except Exception as e:  # pragma: no cover - 环境相关
            self._error = f"{type(e).__name__}: {e}"
            self._engine = None

    def recognize(self, path: str) -> list[str]:
        """返回识别出的文本行。传入 ndarray 避免中文路径兼容问题。"""
        self._init()
        if self._engine is None:
            raise RuntimeError(self._error or "本地 OCR 引擎不可用")
        from PIL import Image
        import numpy as np

        img = Image.open(path).convert("RGB")
        # 小图放大有助于提高英文单词识别率
        if img.width < 900:
            scale = min(3.0, 900 / max(1, img.width))
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        arr = np.array(img)
        result = self._engine.predict(arr)
        lines: list[str] = []
        for r in result:
            lines.extend([t for t in r.get("rec_texts", []) if t])
        return lines


_OCR = LocalOCR()


def local_ocr() -> LocalOCR:
    return _OCR


# --------------------------------------------------------------------------- 文件解析
def _read_text_file(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith("word/") and n.endswith(".xml")]
        parts = []
        for n in ("word/document.xml", *[x for x in names if x != "word/document.xml"]):
            if n not in z.namelist():
                continue
            xml = z.read(n).decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
            parts.append(re.sub(r"<[^>]+>", "", xml))
        return "\n".join(parts)


def _read_pdf(path: str, use_ocr: bool = True) -> tuple[str, str, str]:
    """返回 (文本, 类型, 提示)。"""
    import fitz

    doc = fitz.open(path)
    page_count = len(doc)
    texts = []
    for page in doc:
        texts.append(page.get_text("text"))
    full = "\n".join(texts).strip()
    if len(full) >= 20 or not use_ocr:
        doc.close()
        return full, "pdf", ""

    # 扫描版 PDF：渲染页面后走本地 OCR
    if not _OCR.available:
        doc.close()
        return "", "pdf", "这份 PDF 没有内嵌文字（扫描件），且本机 OCR 模型不可用，请改用「粘贴文本」。"
    lines: list[str] = []
    temp_paths: list[str] = []
    import tempfile
    import warnings
    for idx, page in enumerate(doc):
        if idx >= 20:
            lines.append("")
            break
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        tmp = os.path.join(tempfile.gettempdir(), f"vocab_pdf_{os.getpid()}_{idx}.png")
        pix.save(tmp)
        temp_paths.append(tmp)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                lines.extend(_OCR.recognize(tmp))
        except Exception as e:
            doc.close()
            return "", "pdf_ocr", f"OCR 识别失败：{e}。可以改用「粘贴文本」。"
    doc.close()
    for p in temp_paths:
        try:
            os.remove(p)
        except OSError:
            pass
    notice = f"该 PDF 共 {page_count} 页，已识别前 20 页" if page_count > 20 else ""
    return "\n".join(lines), "pdf_ocr", notice


def extract_from_file(
    path: str, filter_stopwords: bool = True, use_ocr: bool = True
) -> ExtractionResult:
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        return ExtractionResult(notice=f"暂不支持的文件类型：{ext}")

    try:
        if ext in TEXT_EXT:
            text = _read_text_file(path)
            kind = "text"
            notice = ""
        elif ext in DOCX_EXT:
            text = _read_docx(path)
            kind, notice = "docx", ""
        elif ext in PDF_EXT:
            text, kind, notice = _read_pdf(path, use_ocr=use_ocr)
        else:
            if not _OCR.available:
                return ExtractionResult(
                    kind="image_ocr", source=path,
                    notice="本机未找到离线 OCR 模型，图片识别不可用；请在右侧「粘贴文本」手动录入单词。",
                )
            lines = _OCR.recognize(path)
            text = "\n".join(lines)
            kind = "image_ocr"
            notice = "" if lines else "图片里没有识别到文字，可以试试更清晰的图片，或直接粘贴文本。"
        return ExtractionResult(
            text=text, words=words_from_text(text, filter_stopwords=filter_stopwords),
            kind=kind, notice=notice, source=path,
        )
    except Exception as e:
        return ExtractionResult(kind=ext, source=path, notice=f"读取失败：{type(e).__name__}: {e}")


def extract_from_pasted(text: str, filter_stopwords: bool = True) -> ExtractionResult:
    return ExtractionResult(
        text=text, words=words_from_text(text, filter_stopwords=filter_stopwords), kind="manual"
    )
