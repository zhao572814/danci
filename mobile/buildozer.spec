[app]

# 应用名称与包名（卸载重装、更新都靠包名识别，别随意改）
title = 词库本
package.name = ciku
package.domain = com.zhao572814

# 应用图标（v28）：青蓝渐变 + 打开的书 + 字母 A，避免与默认黑色三角图标混淆
icon = icon.png

# 源码目录：buildozer 在 mobile/ 下运行，main.py 也在那里
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf,otf,ttc,txt,mp3,onnx,traineddata
# 这些目录不打包进 APK（作为普通源码资源）
# tts_res/jni_libs 只通过 android.add_jars/add_libs 打进 APK 的 libs/，
# 不当普通资源重复拷贝（它们的 .so/.jar 也不在 include_exts 里）。
source.exclude_dirs = data, __pycache__, build, bin, tts_res, ocr_res, jni_libs, tools

version = 1.0.0

# 依赖：Kivy 做界面；plyer 提供文件选择器，pyjnius 直接调系统 TTS（朗读）。
# pyjnius 显式列出，避免 p4a 版本差异导致 import jnius 失败。
requirements = python3,kivy,plyer,pyjnius

# 竖屏、非全屏（保留状态栏，华为手机上更稳）
orientation = portrait
fullscreen = 0

# 权限：App 不联网、不录音、数据都在私有目录，不需要任何系统权限。
# v28 移除麦克风录音（部分设备如鸿蒙卓易通不支持），改为从本地导入音频文件，
# 通过 ACTION_GET_CONTENT 的 content:// URI 读取，无需 READ_EXTERNAL_STORAGE。
android.permissions =

# 目标与最低安卓版本：API 34 稳定主流（API 35 太新，云端 SDK 常缺该平台导致编译失败）
android.api = 34
android.minapi = 21

# 只打 arm64-v8a：Mate 80 等现代机型都是 64 位，包更小、编译更快
# 新版 buildozer 用 android.archs（复数），旧写法 android.arch 会告警
android.archs = arm64-v8a

# 现场离线 TTS（sherpa-onnx）+ 离线图片 OCR（tesseract4android）：
# classes.jar 已合并两套 Java API（com.k2fsa.* 与 com.googlecode.* 无冲突）；
# so 目录含两套 arm64 组件（onnxruntime/sherpa + tesseract/leptonica/jpeg/pngx）。
# TTS 模型在 assets/tts_model/，OCR 英文模型在 assets/tessdata/eng.traineddata，均随 source 打包。
android.add_jars = jni_libs/classes.jar
android.add_libs_arm64_v8a = jni_libs/jni/arm64-v8a/*.so

# 让 App  Private Storage 可写（默认即可，显式声明避免个别机型限制）
android.private_storage = True

# 允许 cleartext（用不到网络，保留默认关闭）
android.allow_backup = True

# Android 系统自带中文字体 NotoSansCJK，main.py 会自动注册，无需打包字体文件

[buildozer]

log_level = 2
warn_on_root = 0

# 首次构建会联网下载 Android SDK/NDK（约 1~2GB），只在 GitHub 云端发生，
# 之后用缓存；本机不装任何东西。
