"""准备现场 TTS 打包资源（开发期一次性运行，资源下载完成后执行）。

做三件事：
  1. 解压 sherpa.aar，只保留 classes.jar + arm64-v8a 的 so（AAR 50MB→约 15MB）
  2. 解压模型到 assets/tts_model/（onnx + tokens + lexicon）
  3. 打印需要写进 buildozer.spec 的配置

用法： python tools/prepare_tts.py
"""
import os, shutil, tarfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "tts_res")
ASSETS = os.path.join(ROOT, "assets")
TTS_OUT = os.path.join(ROOT, "jni_libs")  # 精简后的 jni 库 + classes.jar（buildozer add_jars/add_libs 指向这里）
MODEL_OUT = os.path.join(ASSETS, "tts_model")

AAR = os.path.join(RES, "sherpa.aar")
MODEL = os.path.join(RES, "model.tar.bz2")


def die(msg):
    print("错误:", msg)
    raise SystemExit(1)


def prepare_aar():
    if not os.path.exists(AAR):
        die(f"找不到 {AAR}")
    if os.path.getsize(AAR) < 50000000:
        die(f"AAR 不完整（{os.path.getsize(AAR)} 字节），请等下载完成")
    os.makedirs(TTS_OUT, exist_ok=True)
    print("解压 AAR...", flush=True)
    with zipfile.ZipFile(AAR) as z:
        names = z.namelist()
        # classes.jar
        cj = [n for n in names if n == "classes.jar"]
        if not cj:
            die("AAR 里没有 classes.jar")
        z.extract("classes.jar", TTS_OUT)
        # 只取 arm64-v8a 的 so
        sos = [n for n in names if n.startswith("jni/arm64-v8a/") and n.endswith(".so")]
        if not sos:
            die("AAR 里没有 arm64-v8a 的 so")
        for n in sos:
            z.extract(n, TTS_OUT)
    so_files = [os.path.join(TTS_OUT, "jni", "arm64-v8a", f)
                for f in os.listdir(os.path.join(TTS_OUT, "jni", "arm64-v8a"))]
    total = sum(os.path.getsize(f) for f in so_files) + os.path.getsize(os.path.join(TTS_OUT, "classes.jar"))
    print(f"AAR 精简完成：{len(so_files)} 个 so + classes.jar，共 {total/1024/1024:.1f} MB", flush=True)
    for f in so_files:
        print("  ", os.path.basename(f), f"{os.path.getsize(f)/1024//1024} MB")


def prepare_model():
    if not os.path.exists(MODEL):
        die(f"找不到 {MODEL}")
    os.makedirs(MODEL_OUT, exist_ok=True)
    print("解压模型...", flush=True)
    with tarfile.open(MODEL, "r:bz2") as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            base = os.path.basename(m.name)
            # v2.0.3 用 lexicon 方案，不需要 espeak-ng-data（358个小文件、60MB+），
            # 官方明确说删掉它减小 APK。只要 onnx + tokens.txt。
            if base.endswith(".onnx") or base == "tokens.txt":
                src = t.extractfile(m)
                dst = os.path.join(MODEL_OUT, base)
                with open(dst, "wb") as f:
                    f.write(src.read())
                print(f"  {base}  {os.path.getsize(dst)/1024//1024} MB", flush=True)
    # lexicon-en-us.txt 单独下载，放进模型目录
    lex_src = os.path.join(RES, "lexicon-en-us.txt")
    if os.path.exists(lex_src) and os.path.getsize(lex_src) > 10000:
        shutil.copy(lex_src, os.path.join(MODEL_OUT, "lexicon-en-us.txt"))
        print("  lexicon-en-us.txt 已就位", flush=True)
    else:
        print("  !! lexicon-en-us.txt 还没下载好，TTS 会不可用", flush=True)
    files = os.listdir(MODEL_OUT)
    print(f"模型就绪：{files}", flush=True)
    return files


def show_config():
    so_dir = os.path.join(TTS_OUT, "jni", "arm64-v8a")
    print("\n===== 把下面几行写进 buildozer.spec =====\n")
    print("source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf,otf,ttc,txt,mp3,onnx")
    print("android.add_jars = jni_libs/classes.jar")
    print("android.add_libs_arm64_v8a = jni_libs/jni/arm64-v8a/*.so")
    print("\n模型放 assets/tts_model/，会被自动打包（onnx 扩展名需加入 include_exts）")


if __name__ == "__main__":
    prepare_aar()
    files = prepare_model()
    show_config()
