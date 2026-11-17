[app]

# 应用名称与包名（卸载重装、更新都靠包名识别，别随意改）
title = 词库本
package.name = ciku
package.domain = com.zhao572814

# 源码目录：buildozer 在 mobile/ 下运行，main.py 也在那里
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,ttf,otf,ttc,txt
# 这些目录不打包进 APK
source.exclude_dirs = data, __pycache__, build, bin

version = 1.0.0

# 依赖：Kivy 做界面，plyer 调用安卓系统朗读（TTS）。pyjnius 由 p4a 自动带上。
requirements = python3,kivy,plyer

# 竖屏、非全屏（保留状态栏，华为手机上更稳）
orientation = portrait
fullscreen = 0

# 不需要任何权限：不联网、不读外部存储，数据全在 App 私有目录
android.permissions =

# 目标与最低安卓版本：API 34 稳定主流（API 35 太新，云端 SDK 常缺该平台导致编译失败）
android.api = 34
android.minapi = 21

# 只打 arm64-v8a：Mate 80 等现代机型都是 64 位，包更小、编译更快
# 新版 buildozer 用 android.archs（复数），旧写法 android.arch 会告警
android.archs = arm64-v8a

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
