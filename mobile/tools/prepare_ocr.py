"""把 tesseract4android AAR 解包成 buildozer 需要的结构，并放置英文训练数据。

产物：
* jni_libs/classes.jar       —— 合并 sherpa-onnx + tesseract 的 Java 类（无包名冲突）
* jni_libs/jni/arm64-v8a/    —— 追加 tesseract 的 4 个 so（libtesseract/leptonica/jpeg/pngx）
* assets/tessdata/eng.traineddata —— OCR 英文模型（运行时会被复制到 App 私有目录）

原始 AAR / traineddata 放在 ocr_res/，由 .gitignore 排除，不上传 GitHub
（这里只是构建期中转，成品已经在 jni_libs/ 和 assets/ 里了）。
"""
from __future__ import annotations

import io
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AAR = os.path.join(ROOT, "ocr_res", "tesseract4android.aar")
TRAIN = os.path.join(ROOT, "ocr_res", "eng.traineddata")
JNI = os.path.join(ROOT, "jni_libs")
CLASSES = os.path.join(JNI, "classes.jar")
SO_DIR = os.path.join(JNI, "jni", "arm64-v8a")
TESSDATA_DIR = os.path.join(ROOT, "assets", "tessdata")


def merge_classes(aar_jar_bytes: bytes):
    """把 tesseract 的 classes.jar 合并进现有 classes.jar，按名字去重。"""
    entries = {}
    if os.path.exists(CLASSES):
        with zipfile.ZipFile(CLASSES) as z:
            for n in z.namelist():
                entries[n] = z.read(n)
    with zipfile.ZipFile(io.BytesIO(aar_jar_bytes)) as z:
        for n in z.namelist():
            if n.endswith("/"):
                continue
            entries[n] = z.read(n)  # tesseract 覆盖同名（实际无冲突）
    tmp = CLASSES + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in entries.items():
            z.writestr(n, data)
    os.replace(tmp, CLASSES)
    print(f"classes.jar 合并完成：{len(entries)} 个类/资源，{os.path.getsize(CLASSES)} 字节")


def main():
    if not os.path.exists(AAR):
        raise SystemExit(f"缺少 AAR：{AAR}")
    if not os.path.exists(TRAIN):
        raise SystemExit(f"缺少训练数据：{TRAIN}")
    os.makedirs(SO_DIR, exist_ok=True)
    os.makedirs(TESSDATA_DIR, exist_ok=True)

    z = zipfile.ZipFile(AAR)
    # 1) 合并 classes.jar
    merge_classes(z.read("classes.jar"))
    # 2) 拷贝 arm64 的 4 个 so
    n_so = 0
    for n in z.namelist():
        if n.startswith("jni/arm64-v8a/") and n.endswith(".so"):
            out = os.path.join(SO_DIR, os.path.basename(n))
            with z.open(n) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  so: {os.path.basename(n)} {os.path.getsize(out)} 字节")
            n_so += 1
    z.close()
    print(f"共拷贝 {n_so} 个 arm64 so")

    # 3) 放置训练数据
    out_train = os.path.join(TESSDATA_DIR, "eng.traineddata")
    shutil.copyfile(TRAIN, out_train)
    print(f"训练数据：{out_train} {os.path.getsize(out_train)} 字节")


if __name__ == "__main__":
    main()
